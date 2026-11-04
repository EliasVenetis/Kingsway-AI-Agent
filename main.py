"""Entry point.

  python main.py test-gmail              # Phase 1: prove Gmail access works
  python main.py run --dry-run           # agent decides, nothing is written to Gmail
  python main.py run                     # creates drafts + labels
  python main.py run --loop --every 5    # keep running, check every 5 minutes
"""
import argparse
import re
import time

from dotenv import load_dotenv

import db
from agent import ALL_LABELS, PROCESSED, EmailAgent
from gmail_client import GmailClient

AUTOMATED_SENDERS = ("noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon",
                     "notifications@", "postmaster@")


def is_automated(email: dict) -> bool:
    """Cheap pre-filter so we don't spend API calls (or reply) on bulk/automated mail."""
    h = email["headers"]
    if any(s in email["from_email"] for s in AUTOMATED_SENDERS):
        return True
    if "list-unsubscribe" in h or h.get("precedence", "").lower() in ("bulk", "list", "junk"):
        return True
    if h.get("auto-submitted", "no").lower() != "no":
        return True
    return False


def test_gmail(args):
    gmail = GmailClient()
    print(f"Connected as {gmail.me}\n")
    for msg_id in gmail.list_message_ids("in:inbox newer_than:7d", args.limit):
        m = gmail.get_message(msg_id)
        print(f"From:    {m['from']}\nSubject: {m['subject']}\n{m['body'][:200]}\n{'-' * 60}")


def run_once(gmail: GmailClient, agent: EmailAgent, args) -> None:
    # Gmail search wants label names lowercased with spaces and "/" turned into "-"
    processed_slug = re.sub(r"[ /]+", "-", PROCESSED.lower())
    query = f"in:inbox newer_than:{args.days}d -from:me -label:{processed_slug}"
    ids = gmail.list_message_ids(query, args.limit)
    print(f"{len(ids)} email(s) to process")

    for msg_id in ids:
        email = gmail.get_message(msg_id)
        thread = gmail.get_thread(email["thread_id"])
        tag = f"[{email['from_email']}] {email['subject'][:60]}"

        # Already answered by a human? Then the latest message in the thread is ours.
        if thread and thread[-1]["from_email"] == gmail.me.lower():
            print(f"skip (already replied)  {tag}")
            if not args.dry_run:
                gmail.add_labels(msg_id, [PROCESSED])
            continue

        if is_automated(email):
            print(f"skip (automated)        {tag}")
            db.log(email, None, args.dry_run, note="automated")
            if not args.dry_run:
                gmail.add_labels(msg_id, [PROCESSED])
            continue

        try:
            decision = agent.handle(email, thread)
        except Exception as e:  # never let one bad email stop the run
            print(f"ERROR {tag}: {e}")
            continue

        print(f"{decision.action:<22}  {tag}  -> {', '.join(decision.labels)}")
        if decision.escalate_reason:
            print(f"    reason: {decision.escalate_reason}")
        if decision.draft and args.dry_run:
            print("    --- draft ---\n    " + decision.draft.replace("\n", "\n    "))

        db.log(email, decision, args.dry_run)
        if args.dry_run:
            continue
        if decision.draft:
            gmail.create_reply_draft(email, decision.draft)
        gmail.add_labels(msg_id, decision.labels + [PROCESSED])


def run(args):
    gmail = GmailClient()
    agent = EmailAgent()
    if not args.dry_run:
        gmail.ensure_labels(ALL_LABELS)
    print(f"Connected as {gmail.me} | model {agent.model} | dry_run={args.dry_run}")

    while True:
        print(f"\n--- check at {time.strftime('%I:%M %p')} ---")
        try:
            run_once(gmail, agent, args)
        except Exception as e:  # e.g. Wi-Fi drop; keep the loop alive
            if not args.loop:
                raise
            print(f"Run failed, will retry next round: {e}")
        if not args.loop:
            break
        time.sleep(args.every * 60)


if __name__ == "__main__":
    load_dotenv()
    db.init()
    p = argparse.ArgumentParser(description="Kingsway email agent")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("test-gmail", help="Print recent inbox emails")
    t.add_argument("--limit", type=int, default=5)
    t.set_defaults(func=test_gmail)

    r = sub.add_parser("run", help="Process new emails")
    r.add_argument("--dry-run", action="store_true", help="Decide but don't touch Gmail")
    r.add_argument("--limit", type=int, default=10)
    r.add_argument("--days", type=int, default=2, help="Only look at the last N days")
    r.add_argument("--loop", action="store_true")
    r.add_argument("--every", type=int, default=5, help="Minutes between checks with --loop")
    r.set_defaults(func=run)

    args = p.parse_args()
    args.func(args)
