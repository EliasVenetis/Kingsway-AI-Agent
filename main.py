"""Entry point.

  python main.py test-gmail              # Phase 1: prove Gmail access works
  python main.py run --dry-run           # agent decides, nothing is written to Gmail
  python main.py run                     # creates drafts + labels
  python main.py run --loop --every 5    # keep running, check every 5 minutes
  python main.py learn --days 365        # one-time: learn from the past year of replies
"""
import argparse
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from dotenv import load_dotenv

import db
import learning
import state_sync
from agent import ALL_LABELS, NEEDS_REVIEW, PROCESSED, EmailAgent
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


def is_contact_form(email: dict) -> bool:
    """Website contact-form notifications (from Gymdesk) look automated but are real leads."""
    return ("(contact form)" in email["subject"].lower()
            or "reply to this email to respond" in email["body"].lower())


def learn_skip(email: dict) -> bool:
    return is_automated(email) and not is_contact_form(email)


def test_gmail(args):
    gmail = GmailClient()
    print(f"Connected as {gmail.me}\n")
    for msg_id in gmail.list_message_ids("in:inbox newer_than:7d", args.limit):
        m = gmail.get_message(msg_id)
        print(f"From:    {m['from']}\nSubject: {m['subject']}\n{m['body'][:200]}\n{'-' * 60}")


def run_once(gmail: GmailClient, agent: EmailAgent, args) -> None:
    # 1) Learn from anything the team replied to since last time.
    try:
        new = learning.harvest(gmail, f"in:sent newer_than:{args.days}d", skip_fn=learn_skip,
                               needs_review_label=NEEDS_REVIEW, dry_run=args.dry_run)
    except Exception as e:  # learning should never block drafting
        print(f"(learning skipped this round: {e})")
        new = 0
    if new:
        print(f"learned from {new} new staff repl{'y' if new == 1 else 'ies'} "
              f"({learning.count()} total)")

    # 2) Handle new emails.
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

        email["contact_form"] = is_contact_form(email)
        if is_automated(email) and not email["contact_form"]:
            print(f"skip (automated)        {tag}")
            db.log(email, None, args.dry_run, note="automated")
            if not args.dry_run:
                gmail.add_labels(msg_id, [PROCESSED])
            continue

        try:
            examples = learning.find_similar(email["subject"], email["body"])
            decision = agent.handle(email, thread, examples)
        except Exception as e:  # never let one bad email stop the run
            print(f"ERROR {tag}: {e}")
            continue

        used = f"  (used {len(examples)} past repl{'y' if len(examples) == 1 else 'ies'})" if examples else ""
        print(f"{decision.action:<22}  {tag}  -> {', '.join(decision.labels)}{used}")
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


def learn(args):
    """One-time backfill: learn from replies the team already sent."""
    gmail = GmailClient()
    gmail.ensure_labels(ALL_LABELS)
    print(f"Reading up to {args.max} sent threads from the last {args.days} days...")
    new = learning.harvest(gmail, f"in:sent newer_than:{args.days}d", skip_fn=learn_skip,
                           max_threads=args.max, needs_review_label=NEEDS_REVIEW)
    print(f"Learned {new} new example(s). Total saved: {learning.count()}")


def days_ago(email: dict) -> int | None:
    try:
        sent = parsedate_to_datetime(email["date"])
        return (datetime.now(timezone.utc) - sent).days
    except Exception:
        return None


def catch_up_forms(args):
    """Draft replies to website contact forms that never got an answer."""
    gmail = GmailClient()
    agent = EmailAgent()
    if not args.dry_run:
        gmail.ensure_labels(ALL_LABELS)
    me = gmail.me.lower()
    ids = gmail.list_message_ids(f'subject:"Contact Form" newer_than:{args.days}d -from:me', args.max)
    print(f"Found {len(ids)} contact form(s) in the last {args.days} days. Checking which are unanswered...\n")

    seen_threads, drafted, skipped = set(), 0, 0
    for msg_id in ids:
        email = gmail.get_message(msg_id)
        if not is_contact_form(email) or email["thread_id"] in seen_threads:
            continue
        seen_threads.add(email["thread_id"])
        tag = f"{email['from_name'] or email['from_email']}  ({email['date'][:16]})"
        thread = gmail.get_thread(email["thread_id"])
        if any(m["from_email"] == me and "DRAFT" not in m["label_ids"] for m in thread):
            print(f"already answered        {tag}"); skipped += 1; continue
        if any("DRAFT" in m["label_ids"] for m in thread):
            print(f"already has a draft     {tag}"); skipped += 1; continue

        email["contact_form"] = True
        email["late_days"] = days_ago(email)
        try:
            decision = agent.handle(email, thread, learning.find_similar(email["subject"], email["body"]))
        except Exception as e:
            print(f"ERROR {tag}: {e}"); continue
        print(f"{decision.action:<22}  {tag}")
        if args.dry_run and decision.draft:
            print("    --- draft ---\n    " + decision.draft.replace("\n", "\n    "))
        db.log(email, decision, args.dry_run, note="catch-up")
        if not args.dry_run:
            if decision.draft:
                gmail.create_reply_draft(email, decision.draft)
                drafted += 1
            gmail.add_labels(msg_id, decision.labels + [PROCESSED])
    print(f"\nDone. {drafted} draft(s) created, {skipped} skipped (already answered or drafted).")


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
    # Cloud only: pull saved memory first. If this fails we stop, so we never
    # overwrite good saved memory with an empty one.
    state_sync.download()
    db.init()
    learning.init()
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

    lr = sub.add_parser("learn", help="Learn from replies the team already sent")
    lr.add_argument("--days", type=int, default=365)
    lr.add_argument("--max", type=int, default=300, help="Max threads to read (up to 500)")
    lr.set_defaults(func=learn)

    cf = sub.add_parser("catch-up-forms", help="Draft replies to unanswered website contact forms")
    cf.add_argument("--days", type=int, default=60)
    cf.add_argument("--max", type=int, default=100)
    cf.add_argument("--dry-run", action="store_true", help="Show drafts without touching Gmail")
    cf.set_defaults(func=catch_up_forms)

    args = p.parse_args()
    try:
        args.func(args)
    finally:
        state_sync.upload()  # cloud only: save what was learned/logged this run
