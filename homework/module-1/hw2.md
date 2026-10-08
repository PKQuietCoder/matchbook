# Homework 2, implementing the traced agent endpoint

Homework 2 asks you to expose the purchase-to-pay agent through an authenticated
HTTP endpoint and record its execution using OpenTelemetry GenAI semantic
conventions. You will inspect model inputs, outputs and tool results, then verify
which authenticated identity reached the tools.

## Working through the assignment with a coding agent

If you would like a coding agent to walk you through this, paste the prompt below
at the start of a session in your repository. It assumes no programming
background, so it suits an analyst or a product manager as well as an engineer.
The Homework 1 tutorial does not cover Homework 2.

> Walk me through Homework 2 in `homework/module-1/hw2.md` as an interactive
> tutorial. Read `AGENTS.md`, the handout, and `SPEC.md` first. I may not have a
> programming background, so assume nothing about what I know, and adapt once you
> see what I do know.
>
> I am driving. Work one step at a time, in the handout's order. Before each
> step, explain in plain language what you propose to do and why the assignment
> needs it, and show me the command you would run or the change you would make.
> Then wait for me to say go. Do not run a command, change a file, or generate
> anything until I have said so, and do not take several steps on one go ahead.
> Reading files to prepare a proposal is fine. Once I say go, do that step, show
> me the result, and explain what it means. Move on only when you are confident I
> understand the current step. One short question about what I expect to see, or
> what a result means, is enough to check; keep questions few, and do not turn
> the session into a quiz. Explain every unfamiliar term the first time it
> appears, using the actual files and outputs as examples. When a picture would
> help, draw one; a text diagram is fine.
>
> If something fails, read the error, explain it plainly, and propose a focused
> fix. Keep a short progress note of what is done and what is next, so we can
> resume later, and keep a checklist of every deliverable so nothing is skipped.
> Leave the assessments and the video to me. Do not call the assignment done
> until every file in the "Files to commit" list exists and the checks in the
> handout pass.
>
> Separate eval concepts from plumbing. Explain the eval concepts in depth --
> traces, spans, attributes, prompt versioning, and why a *business activity* is
> not the same thing as a successful tool call -- because those are what I need to
> understand. Treat the infrastructure steps as a checklist I follow: Docker,
> ports, tokens, environment variables. Concepts I need before we use them: what
> a trace and a span are, how the standard `gen_ai.*` fields differ from this
> application's `mb.*` fields, why the server and not the conversation decides who
> I am, and what prompt versioning is for. Diagrams that would help: the path from
> my message through the endpoint, the agent, the tools and into the trace; and
> the tree of spans inside one trace.

## Expected work

- Estimated time: 4 to 6 hours.
- Expected production code: approximately 70 to 100 lines across
  `observability/instrument.py` and `server/app.py`.
- Expected test code: approximately 30 to 50 lines in
  `tests/test_observability.py`.
- Other work: at least five traced requests, two trace records in JSON, and a
  video of no more than 5 minutes.

Most of the time is implementing and testing the endpoint. Starting Docker is
necessary setup, not substantive programming work.

## Preparation

Continue in the same repository as Homework 1, with the five tools implemented
and the world generated. If you did not finish Homework 1, apply its reference
implementation first — Homework 2 traces tool calls, so it needs tools that work:

```bash
git apply homework/module-1/hw1-reference.patch
```

Then install the agent extra and open the Homework 2 holes:

```bash
uv sync --extra agent
git apply homework/module-1/hw2-holes.patch
```

Unlike Homework 1's holes, these **stop the agent running** until you fill them.
That is deliberate, and worth understanding before you hit it. `agent/agent.py`'s
`_call` seam absorbs a `NotImplementedError` from a *tool*, because a half-built
agent that still holds a conversation is useful. It does not absorb one from a
*recorder*: an instrumentation layer that failed quietly would leave a partially
recorded event log, and a log that is wrong is worse than a log that is missing.

Read the following before editing code:

- `observability/spans.py`, the local SQLite span store, which is **authoritative**:
  `bridge/spans_to_log.py` mines the event log from it and `bridge/cost.py`
  prices it.
- `observability/instrument.py`, which configures OpenTelemetry and exports to
  Langfuse.
- `agent/auth.py`, which defines the `AuthContext` passed to every tool.
- `_call` in `agent/agent.py`, which invokes the recorders after every tool call.
- `server/app.py`, which provides the request models, the token helpers and
  `_authorize`.

The token implementation is suitable only for local development. Its purpose is
narrow and is the point of Part B: it lets the endpoint distinguish identity
*supplied by the server* from identity *claimed in a conversation*.

There is no auto-instrumentation here. Matchbook has no agent framework, so every
span is opened by hand. That is more work than importing an instrumentor and it
is the only way the span tree can be shaped around purchase-order items rather
than around a framework's idea of a run. Read the
[OTel GenAI overview](https://opentelemetry.io/blog/2026/genai-observability/).

Start the trace stack:

```bash
docker compose -f observability/docker-compose.yml up -d
```

Open `http://localhost:3001` and sign in as `student@example.com` with the
password `matchbook-dev-pass`. The project, its API keys and that login are
pre-created on first boot and the keys match `.env.example`, so there is nothing
to click through.

Port 3001, not 3000: every port in this stack is shifted by one so it cannot
collide with the sibling Oakline course's Langfuse. Two Langfuse instances is a
lot of ClickHouse for one laptop, so stop the other course's stack while you work
here.

Copy the `LANGFUSE_*` values from `.env.example` into `.env` if they are not
already there, and set `MB_TRACE_CONTENT=true`. Without content capture the span
tree, tool order, roles, permission decisions and token counts are all still
recorded, but the message text Module 2's review needs is not. Turn it off when
message content must not leave the machine, and redact before exporting traces.

## Part A, add application attributes to tool spans

In this part you record the authenticated caller and the permission decision on
each tool span, and decide what reached the event log.

Implement three functions in `observability/instrument.py`:

- `record_tool_result`
- `_set_permission_denied_attributes`
- `record_activity`

Use the SQLite span passed in and the active OTel span from `current_span()`. One
call site in `_call` drives both recorders, which is what stops them disagreeing.
Add the following application attributes:

- `mb.actor_id`, `mb.role`, `mb.company_code`, `mb.purchasing_group` — the
  authenticated caller, the same for every tool call in a request
- `mb.permission_denied`, a boolean, on **every** call
- `mb.permission_denied.reason`, when permission was denied

Set the denial flag on every call, not only the denied ones. An absent attribute
and a false one are different claims: the first says nobody looked, the second
says the call was allowed. A denial rate computed over spans that carry the flag
only when it is true has no denominator. The Homework 3 smoke report counts
denials from this attribute and Module 3 asserts on it.

`record_activity` is the one with teeth, and it is where this assignment stops
resembling Oakline's. Only a successful write **that actually changed the
world** contributes a business activity. A refused or paused attempt is a span —
visible, countable, minable in the `attempts` layer — but it is not a step that
happened. The sharp case is a clearing queued for a controller: the tool returns
`ok`, so the easy reading records `Clear Invoice`, but nothing was paid. Record
that and the mined event log asserts a payment that never happened, and every
later conformance comparison against the human log compares the agent's *claim*
instead of its behaviour. That is SPEC RESP-3 in mined-log form.

Standard `gen_ai.*` fields and application `mb.*` fields belong on the same span.
The `mb.` prefix is not new: `process/otlp.py` already exports the human event log
with `mb.case_id`, `mb.activity` and `mb.tie_broken`, so the agent's spans and the
real company's spans describe themselves in one vocabulary.

One disclosure to read before you report any token number. Anthropic reports four
token counts as **disjoint** sets, billed at three different rates, while OTel's
`gen_ai.usage.input_tokens` means total prompt tokens. `record_usage` therefore
puts the uncached input in the standard field and carries the cache counts beside
it under `mb.*`, rather than summing them into a total no provider reported. The
consequence is measured: on one live session Langfuse priced the run at $0.005544
and `python -m bridge.cost` at $0.0106. Langfuse is not wrong — it prices exactly
what the standard field means — it is missing the cache tokens. **`bridge.cost`
is authoritative for money; the trace UI is authoritative for shape.**

## Part B, implement session creation

The agent needs an authenticated caller before it can enforce access control. In
this part you build the endpoint that validates an identity against the world and
issues a signed token.

Implement `create_session` in `server/app.py`. The endpoint must:

- Reject an unknown role with HTTP 400.
- Load the requested actor from the world.
- Reject an unknown actor with HTTP 404.
- Reject a claimed role that differs from the actor's stored role with HTTP 403.
- Build an `AuthContext` from the **stored** identity.
- Keep that context server-side in `_SESSIONS`.
- Return a session identifier and a signed token with HTTP 200.

The token payload must contain `session_id`, `actor_id`, `role`, `company_code`,
`purchasing_group` and `issued_at`, using the verified identity from the world.
Do not construct the authorization context from a later chat message.

After implementing, run the supplied test:

```bash
uv run pytest --runxfail -vv tests/test_hw_holes.py -k "create_session_binds"
```

## Part C, implement the traced message endpoint

Implement `post_message` in `server/app.py`. The endpoint must authorize the
bearer token before it runs the agent. The checks are supplied in `_authorize`: a
missing or invalid token returns 401, a token issued for another session returns
403, and an unknown session returns 404. It must then recover the session the
server stored and compute the prompt version by hashing only the template.

Run the agent inside the root span `mb.session_message`, carrying:

- `mb.actor_id`, `mb.role`, `mb.company_code`
- `mb.prompt_version`
- `mb.scenario_id`, when the request supplies a nonempty value
- `gen_ai.input.messages`, containing the incoming user message
- `gen_ai.output.messages`, containing the final reply once the run completes

Use the OTel GenAI message format, serialized with `json.dumps`. The input is
`[{"role": "user", "parts": [{"type": "text", "content": body.message}]}]`; the
output is the same shape with role `assistant`. Langfuse consumes both into the
trace's own input and output fields, which is that convention working as
intended.

The span also carries `langfuse.trace.*` attributes, which are supplied and are
the one vendor-specific thing in the file. They are what makes a trace
*filterable* by session, actor and scenario rather than merely inspectable — the
difference between Module 2 reviewing 100 traces and reading them one at a time.

Return the session identifier, the run id, the final reply, the prompt version,
the tool order, and `activities_recorded`.

That last field is the Matchbook addition. A reply is not evidence. Returning
beside the reply the list of business activities the run actually contributed lets
a caller see immediately when the two disagree.

Pass the session's earlier turns to `run_session` as `history`, so a conversation
continues rather than restarting. Note what a session *is not*: a session is a
conversation, while a **case** is a purchase-order item. Two conversations about
one item are one case, which is the decision `bridge/spans_to_log.py` exists to
make.

## Part D, test authentication

Create tests in `tests/test_observability.py` for at least:

- Session creation rejects an actor whose claimed role differs from the stored
  role.
- A token issued for one session cannot authorize a different session.

The tests must not require Langfuse, Docker or a model key. Export spans to an
`InMemorySpanExporter` and drive the endpoint with FastAPI's `TestClient`; the
`otel_spans` fixture in `tests/conftest.py` sets up the first for you. A test that
needs a container is a test that stops being run.

Run the supplied hole tests, then your own, then everything:

```bash
uv run pytest --runxfail tests/test_hw_holes.py -k hw2
uv run pytest tests/test_observability.py
uv run pytest
```

## Part E, run the endpoint and inspect traces

Start the server in a second terminal and leave it running:

```bash
uv run uvicorn server.app:app --port 8010
```

It prints the prompt version, the kill-switch state, and whether tracing is on.
If it says tracing is off, `.env` is missing the `LANGFUSE_*` values.

Create a session:

```bash
curl -s -X POST http://localhost:8010/sessions \
  -H 'Content-Type: application/json' \
  -d '{"actor_id":"ap-003","role":"ap_clerk"}'
```

Copy the session identifier and token into a message request:

```bash
curl -s -X POST http://localhost:8010/sessions/SESSION_ID/messages \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer TOKEN' \
  -d '{"message":"Why is the invoice on 4507001234_00010 still unpaid?"}'
```

Submit at least five requests drawn from `hw1-session.jsonl`, using a session for
the matching actor, and inspect each resulting trace.

In Langfuse, open the root span and the tool spans and check the attributes from
Parts A and C. For an allowed tool call confirm `mb.permission_denied = false`
with no reason; for a denial confirm `true` and the recorded reason. Confirm the
trace itself carries a session, a user and your scenario id — if those are empty,
the attributes went onto the wrong span. For a model call, inspect
`gen_ai.request.model`, `gen_ai.response.model` and the token counts. For a tool
call, inspect `gen_ai.operation.name=execute_tool`, `gen_ai.tool.name` and the
captured arguments and result, and confirm the spans share one trace id.

Then inspect the same runs through Matchbook's own views, which the trace UI
cannot give you:

```bash
uv run python -m analysis.review list --sort retries
uv run python -m analysis.review show <trace_id>
uv run python -m bridge.cost --by run
```

Include one request whose clearing is **queued** — `4507003300_00010` is above
the limit. Confirm three things agree: the reply says queued and not paid, the
response's `activities_recorded` is empty, and the span carries `mb.queued` with
no `mb.activity`. That is one requirement, RESP-3, checked in three places.

## Part F, compare two prompt versions

Module 2 uses prompt version hashes to group traces by the prompt that produced
them. Here you confirm the instrumentation captures the version and that a prompt
change produces a different hash.

Choose one request from `hw1-session.jsonl` and save the current
`SYSTEM_PROMPT_TEMPLATE` from `agent/agent.py`. Submit the request in a new
session, find its trace, and record `mb.prompt_version`.

Then run the same request against a different template. The CLI takes one
directly, which avoids editing and restarting:

```bash
uv run python -m agent --actor ap-003 --message "..." \
  --model claude-sonnet-5 --prompt-file /tmp/alt-prompt.txt
```

If Homework 1 produced a revision, use it. If not, make a small wording change
for the purpose of this check; a change made to test version recording does not
need to address a failure. Verify the two hashes differ.

Both runs must start from the same world state. If a request changes the world,
re-seed between them:

```bash
uv run python -m seed.generate
```

The comparison checks version *recording*, not whether one prompt performs
better.

## Trace record

Choose any two traces you can explain from the root span through to the final
response.

Create `hw2-traces.json` as a JSON array of exactly two objects. For each trace
record:

- `trace_id`
- `permalink`
- `prompt_version`
- `actor_id`
- `role`
- `tool_order`, an ordered list of tool names
- `activities_recorded`, the business activities the run contributed
- `final_status`, either `completed` or `error`

Choose one trace where `tool_order` and `activities_recorded` have different
lengths, and be ready to explain the difference. It is almost always the
interesting one.

## Files to commit

- `observability/instrument.py`
- `server/app.py`
- `tests/test_observability.py`
- `hw2-traces.json`, containing the two selected traces

## Video

Record one continuous screen video of no more than 5 minutes. In the recording:

- Run one authentication test.
- Read both selected traces from the root span to the final response.
- Explain how the endpoint established the authenticated identity, and why a
  message cannot change it.
- Explain the tool calls and their results in the selected traces.
- Show one run where the reply and `activities_recorded` disagree, or where a
  queued clearing recorded no activity, and say why that matters.
- Show the two prompt version hashes from the controlled comparison.
- Regenerate the span count for one selected trace.
