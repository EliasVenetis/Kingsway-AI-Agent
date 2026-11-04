"""SQLite log of every decision. This becomes your eval data and resume metrics later."""
import json
import sqlite3
from datetime import datetime, timezone

DB_PATH = "agent_log.db"


def init():
    with sqlite3.connect(DB_PATH) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT, dry_run INTEGER,
                message_id TEXT, thread_id TEXT, sender TEXT, subject TEXT, body TEXT,
                category TEXT, confidence REAL, action TEXT,
                escalate_reason TEXT, draft TEXT, tool_calls TEXT, note TEXT
            )""")


def log(email: dict, decision, dry_run: bool, note: str = ""):
    with sqlite3.connect(DB_PATH) as c:
        c.execute(
            """INSERT INTO decisions (ts, dry_run, message_id, thread_id, sender, subject, body,
               category, confidence, action, escalate_reason, draft, tool_calls, note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                datetime.now(timezone.utc).isoformat(), int(dry_run),
                email["id"], email["thread_id"], email["from_email"], email["subject"],
                email["body"][:5000],
                getattr(decision, "category", None), getattr(decision, "confidence", None),
                getattr(decision, "action", "skipped"),
                getattr(decision, "escalate_reason", None), getattr(decision, "draft", None),
                json.dumps(getattr(decision, "tool_calls", [])), note,
            ),
        )
