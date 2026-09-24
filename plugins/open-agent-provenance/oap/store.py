"""Transactional, append-only event journal with per-session integrity chains."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def state_dir() -> Path:
    override = os.environ.get("OPENPROVENANCE_HOME")
    if override:
        return Path(override).expanduser().absolute()
    return Path.home() / ".local" / "state" / "open-agent-provenance"


class Store:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or state_dir()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.directory / "events.sqlite3"
        # Create with private permissions before SQLite opens it. Never truncate.
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        with closing(self.connect()) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    session TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    body TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL,
                    UNIQUE(session, event_id)
                );
                CREATE INDEX IF NOT EXISTS events_session ON events(session, seq);
            """)

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=3)
        db.row_factory = sqlite3.Row
        return db

    def append(self, session: str, body: dict, event_id: str | None = None) -> dict:
        event_id = event_id or uuid4().hex
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT * FROM events WHERE session=? AND event_id=?", (session, event_id)
            ).fetchone()
            if previous:
                old_body = json.loads(previous["body"])
                for field in ("session", "event_id", "observed_at"):
                    old_body.pop(field, None)
                if canonical(old_body) != canonical(body):
                    raise ValueError("Conflicting retry for an existing event ID")
                return {"session": session, "event_id": event_id, "duplicate": True}
            row = db.execute(
                "SELECT event_hash FROM events WHERE session=? ORDER BY seq DESC LIMIT 1",
                (session,),
            ).fetchone()
            previous_hash = row[0] if row else "0" * 64
            body = {**body, "session": session, "event_id": event_id, "observed_at": now()}
            event_hash = digest({"previous": previous_hash, "body": body})
            db.execute(
                "INSERT INTO events(session,event_id,body,previous_hash,event_hash) VALUES(?,?,?,?,?)",
                (session, event_id, canonical(body), previous_hash, event_hash),
            )
        return {"session": session, "event_id": event_id, "duplicate": False}

    def rows(self, session: str) -> list[dict]:
        with closing(self.connect()) as db:
            rows = db.execute("SELECT * FROM events WHERE session=? ORDER BY seq", (session,))
            return [dict(row) for row in rows]

    def events(self, session: str) -> list[dict]:
        rows = self.rows(session)
        if not rows:
            raise ValueError("Unknown session")
        return [json.loads(row["body"]) for row in rows]

    def sessions(self, limit: int = 20) -> list[dict]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with closing(self.connect()) as db:
            rows = db.execute(
                """
                SELECT session, COUNT(*) AS event_count, MAX(seq) AS last_seq
                FROM events GROUP BY session ORDER BY last_seq DESC LIMIT ?
            """,
                (limit,),
            ).fetchall()
            result = []
            for row in rows:
                body = json.loads(
                    db.execute(
                        "SELECT body FROM events WHERE seq=?", (row["last_seq"],)
                    ).fetchone()[0]
                )
                result.append(
                    {
                        "session": row["session"],
                        "event_count": row["event_count"],
                        "host": body["host"],
                        "last_observed_at": body["observed_at"],
                        "project_digest": body.get("project_digest"),
                    }
                )
            return result

    def verify(self, session: str) -> dict:
        rows = self.rows(session)
        if not rows:
            raise ValueError("Unknown session")
        previous = "0" * 64
        for row in rows:
            try:
                body = json.loads(row["body"])
                valid = (
                    body["session"] == session
                    and body["event_id"] == row["event_id"]
                    and row["previous_hash"] == previous
                    and digest({"previous": previous, "body": body}) == row["event_hash"]
                )
            except (ValueError, KeyError, TypeError):
                valid = False
            if not valid:
                return {"valid": False, "first_invalid_seq": row["seq"]}
            previous = row["event_hash"]
        return {
            "valid": True,
            "event_count": len(rows),
            "head": previous,
            "scope": "Local consistency only; not signed or externally anchored.",
        }
