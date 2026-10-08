"""The authenticated HTTP endpoint for the Matchbook agent.

Two endpoints, and one idea holding them together: **the server decides who you
are.** `POST /sessions` verifies a claimed identity against the `actors` table
and issues a signed token; `POST /sessions/{id}/messages` recovers the context
the server stored and runs the agent under it. Nothing in a chat message can
change the caller's role, company code or purchasing group, which is what SPEC
AUTH-1 means by "authorization is not a prompt".

The token is a local-development HMAC, not a credential system. Its purpose is
narrow and worth stating: it lets the endpoint distinguish identity it
established itself from identity merely asserted by whoever is typing.

Run it:

    uv run uvicorn server.app:app --port 8010

`fastapi` and `uvicorn` arrive with `uv sync --extra agent`. Nothing in
`process/` may import this module -- tests/test_offline_mining.py blocks
`fastapi` by name and mines a real log anyway.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from agent import db
from agent.agent import (
    SessionResult,
    auth_context_for,
    banner,
    prompt_version,
    run_session,
)
from agent.auth import ROLES, AuthContext
from agent.model_anthropic import DEFAULT_MODEL, load_env
from observability import instrument
from observability.spans import SpanStore
from server.sessions import SessionStore

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SPANS = REPO_ROOT / "build" / "spans.db"
SCRIPTED = "scripted"

app = FastAPI(title="Matchbook agent", version="0.1.0")

# session_id -> the AuthContext the server established for it. Identity lives
# here and in the token; never in the request body of a message.
_SESSIONS: dict[str, AuthContext] = {}


class SessionCreate(BaseModel):
    actor_id: str
    role: str


class MessageCreate(BaseModel):
    message: str
    model: str = DEFAULT_MODEL
    scenario_id: str | None = None


def session_secret() -> bytes:
    return os.environ.get("MB_SESSION_SECRET", "matchbook-dev-session-secret").encode()


def create_token(payload: dict[str, Any]) -> str:
    """Sign a session payload. Local development only."""
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(session_secret(), body, hashlib.sha256).hexdigest()
    return f"{body.hex()}.{signature}"


def verify_token(token: str) -> dict[str, Any] | None:
    """Return the payload if the signature holds, else None."""
    try:
        body_hex, signature = token.split(".", 1)
        body = bytes.fromhex(body_hex)
    except ValueError:
        return None
    expected = hmac.new(session_secret(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def _authorize(session_id: str, authorization: str | None) -> AuthContext:
    """Supplied. 401 on a bad token, 403 on the wrong session, 404 if unknown."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    payload = verify_token(authorization.split(" ", 1)[1].strip())
    if payload is None:
        raise HTTPException(status_code=401, detail="invalid token signature")
    if payload.get("session_id") != session_id:
        raise HTTPException(status_code=403, detail="token was issued for another session")
    context = _SESSIONS.get(session_id)
    if context is None:
        raise HTTPException(status_code=404, detail=f"unknown session {session_id}")
    return context


@app.post("/sessions")
def create_session(body: SessionCreate) -> dict[str, Any]:
    """Verify a claimed identity against the world and open a session.

    Reject an unknown role with 400, an unknown actor with 404, and a claimed
    role that disagrees with the stored one with 403. Build the AuthContext
    from the STORED identity, keep it server-side, and return a session id with
    a signed token whose payload carries session_id, actor_id, role,
    company_code, purchasing_group and issued_at.
    """
    if body.role not in ROLES:
        raise HTTPException(
            status_code=400, detail=f"unknown role {body.role!r}; known: {', '.join(ROLES)}"
        )
    with db.connection() as connection:
        actor = db.get_actor(connection, body.actor_id)
    if actor is None:
        raise HTTPException(status_code=404, detail=f"unknown actor {body.actor_id!r}")
    if actor["role"] != body.role:
        # The claim loses to the record. This is the whole point of the
        # endpoint: a caller cannot promote themselves by asserting a role.
        raise HTTPException(
            status_code=403,
            detail=f"actor {actor['actor_id']} has role {actor['role']!r}, not {body.role!r}",
        )
    context = AuthContext(
        actor_id=actor["actor_id"],
        role=actor["role"],
        company_code=actor["company_code"],
        purchasing_group=actor["purchasing_group"],
    )
    session_id = uuid.uuid4().hex
    _SESSIONS[session_id] = context
    token = create_token(
        {
            "session_id": session_id,
            "actor_id": context.actor_id,
            "role": context.role,
            "company_code": context.company_code,
            "purchasing_group": context.purchasing_group,
            "issued_at": int(time.time()),
        }
    )
    return {"session_id": session_id, "token": token, "role": context.role}


@app.post("/sessions/{session_id}/messages")
def post_message(
    session_id: str,
    body: MessageCreate,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Run one message in an authorized session, traced end to end.

    Authorize before running anything. Recover the context the server stored --
    never rebuild it from the request. Compute the prompt version by hashing
    only the template. Run the agent under a root span carrying the actor, the
    role, the prompt version, the scenario id when supplied, and the OTel GenAI
    input and output messages.
    """
    context = _authorize(session_id, authorization)
    version = prompt_version()
    sessions = SessionStore()
    spans = SpanStore(str(DEFAULT_SPANS))
    try:
        history = sessions.history(session_id)
        result: SessionResult = run_session(
            context,
            body.message,
            _model(body.model),
            store=spans,
            session_id=session_id,
            scenario_id=body.scenario_id,
            history=history,
        )
        sessions.append(
            session_id,
            [
                {"role": "user", "content": body.message},
                {"role": "assistant", "content": result.reply},
            ],
        )
        # Which business activities the run actually contributed to the event
        # log, returned beside the reply because a reply is not evidence. A run
        # that says "I have cleared the invoice" while contributing no `Clear
        # Invoice` activity is the exact failure this repo exists to surface.
        activities = [
            span["activity"] for span in spans.all_spans(result.run_id) if span.get("activity")
        ]
    finally:
        spans.close()
        sessions.close()
    return {
        "session_id": session_id,
        "run_id": result.run_id,
        "reply": result.reply,
        "prompt_version": version,
        "steps": result.steps,
        "tool_order": [call["name"] for call in result.tool_calls],
        "activities_recorded": activities,
    }


def _model(name: str) -> Any:
    """The model that answers. A scripted model cannot answer an open request."""
    if name == SCRIPTED:
        raise HTTPException(
            status_code=400,
            detail="the scripted model replays fixed steps and cannot answer an "
            "arbitrary request; pass a live model id such as claude-sonnet-5",
        )
    from agent.model_anthropic import AnthropicModel

    return AnthropicModel(model=name)


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "banner": banner(), "prompt_version": prompt_version()}


@app.on_event("startup")
def _start_tracing() -> None:
    load_env()
    configured = instrument.configure(service_name="matchbook-server")
    print(banner())
    print(
        "tracing -> langfuse" if configured
        else "tracing off (no LANGFUSE_* keys, or the OTel extra is not installed)"
    )


@app.on_event("shutdown")
def _stop_tracing() -> None:
    instrument.shutdown()
