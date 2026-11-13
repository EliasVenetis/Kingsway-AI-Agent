"""Learning from your team's real replies.

Every time someone at Kingsway replies to an email (an escalated one, or a draft
they edited and sent), we save the customer's question + the reply that was
actually sent. When a similar email arrives later, the agent sees those past
replies as examples and can answer the same way instead of escalating again.
"""
import math
import re
import sqlite3
import time
from datetime import datetime, timezone

import db

INTERNAL_DOMAIN = "@kingswayjiujitsu.com"
STOPWORDS = set("""
a about after again all also am an and any are as at be been before being but by can
could did do does doing for from get got had has have hello hi hey how i if in into is it
its just know like me my need not of on or our please so some than thanks thank that the
their them then there these they this to too up us was we were what when where which who
will with would you your yours im ive id dont kingsway jiu jitsu jiujitsu bjj gym
""".split())


def init():
    with sqlite3.connect(db.DB_PATH) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS examples (
                thread_id TEXT PRIMARY KEY,
                ts TEXT, sender TEXT, subject TEXT,
                question TEXT, reply TEXT
            )""")


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9$]+", text.lower())
    return {w for w in words if len(w) > 2 and w not in STOPWORDS}


def clean_reply(text: str) -> str:
    """Keep only what was newly written, not the quoted email underneath."""
    kept = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(">"):
            break
        if re.match(r"^On .+wrote:?$", s) or s.startswith("-----Original Message"):
            break
        if s.startswith("---------- Forwarded message"):
            break
        kept.append(line)
    return "\n".join(kept).strip()


def harvest(gmail, query: str, skip_fn=None, max_threads: int = 50,
            needs_review_label: str | None = None, dry_run: bool = False) -> int:
    """Find threads where a human replied, and save question + reply pairs.

    Also clears the Needs Review label once someone has replied (not in dry run).
    Returns the number of new examples saved.
    """
    me = gmail.me.lower()
    resp = gmail.service.users().threads().list(
        userId="me", q=query, maxResults=max_threads).execute(num_retries=7)
    saved = 0
    with sqlite3.connect(db.DB_PATH) as c:
        known = {r[0] for r in c.execute("SELECT thread_id FROM examples")}
    for t in resp.get("threads", []):
        if t["id"] in known:
            continue
        time.sleep(0.3)  # stay under Gmail's per-minute limit on big imports
        # Ignore drafts: the agent's own drafts are "from me" too.
        msgs = [m for m in gmail.get_thread(t["id"]) if "DRAFT" not in m["label_ids"]]

        pair = None
        for i in range(len(msgs) - 1, 0, -1):
            if msgs[i]["from_email"] == me:
                inbound = [m for m in msgs[:i] if m["from_email"] != me]
                if inbound:
                    pair = (inbound[-1], msgs[i])
                break
        if not pair:
            continue
        question, reply_msg = pair
        if question["from_email"].endswith(INTERNAL_DOMAIN):
            continue  # forwards / internal mail aren't customer questions
        if skip_fn and skip_fn(question):
            continue
        reply = clean_reply(reply_msg["body"])
        if len(reply) < 20:
            continue  # "sounds good!" teaches nothing

        with sqlite3.connect(db.DB_PATH) as c:
            c.execute(
                "INSERT OR IGNORE INTO examples VALUES (?,?,?,?,?,?)",
                (t["id"], datetime.now(timezone.utc).isoformat(), question["from_email"],
                 question["subject"], clean_reply(question["body"])[:3000], reply[:3000]),
            )
        saved += 1

        # Someone answered it, so it no longer needs review.
        if needs_review_label and not dry_run:
            gmail.remove_thread_labels(t["id"], [needs_review_label])
    return saved


def find_similar(subject: str, body: str, k: int = 3, min_score: float = 0.12) -> list[dict]:
    """Return the k past exchanges most similar to this email (simple word overlap)."""
    query = _tokens(f"{subject} {body}")
    if not query:
        return []
    try:
        with sqlite3.connect(db.DB_PATH) as c:
            rows = c.execute("SELECT subject, question, reply FROM examples").fetchall()
    except sqlite3.Error:
        return []
    scored = []
    for subj, q, r in rows:
        toks = _tokens(f"{subj} {q}")
        if not toks:
            continue
        score = len(query & toks) / math.sqrt(len(query) * len(toks))
        if score >= min_score:
            scored.append((score, {"subject": subj, "question": q, "reply": r}))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [ex for _, ex in scored[:k]]


def count() -> int:
    with sqlite3.connect(db.DB_PATH) as c:
        return c.execute("SELECT COUNT(*) FROM examples").fetchone()[0]
