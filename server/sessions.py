"""Conversation history for the HTTP endpoint.

A session is a conversation; a *case* is a purchase-order item. The two are not
the same thing and the distinction is the repo's central one: three clearing
attempts on one blocked item, spread over two conversations, are one case with
three steps (see bridge/spans_to_log.py). So this store holds only the turns of
a conversation. It never holds the authenticated identity -- that comes from
the world, through `auth_context_for`, on every request.

File-backed SQLite rather than a dict, because a session that evaporates when
uvicorn reloads makes a multi-turn scenario unreproducible.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
  session_id TEXT NOT NULL,
  seq        INTEGER NOT NULL,
  role       TEXT NOT NULL,
  name       TEXT,
  content    TEXT NOT NULL,
  PRIMARY KEY (session_id, seq)
);
"""


def sessions_path() -> Path:
    return Path(os.environ.get("MB_SESSIONS_DB", REPO_ROOT / ".sessions.db"))


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path or sessions_path()))
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    return connection


class SessionStore:
    def __init__(self, path: Path | str | None = None) -> None:
        self._connection = connect(path)

    def close(self) -> None:
        self._connection.close()

    def history(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT role, name, content FROM turns WHERE session_id = ? ORDER BY seq",
            (session_id,),
        ).fetchall()
        turns: list[dict[str, Any]] = []
        for row in rows:
            turn: dict[str, Any] = {"role": row["role"], "content": row["content"]}
            if row["name"]:
                turn["name"] = row["name"]
            turns.append(turn)
        return turns

    def append(self, session_id: str, turns: list[dict[str, Any]]) -> None:
        with self._connection:
            start = self._connection.execute(
                "SELECT COALESCE(MAX(seq), -1) + 1 FROM turns WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            self._connection.executemany(
                "INSERT INTO turns (session_id, seq, role, name, content) VALUES (?,?,?,?,?)",
                [
                    (
                        session_id,
                        start + offset,
                        turn["role"],
                        turn.get("name"),
                        turn["content"]
                        if isinstance(turn["content"], str)
                        else json.dumps(turn["content"], default=str),
                    )
                    for offset, turn in enumerate(turns)
                ],
            )
