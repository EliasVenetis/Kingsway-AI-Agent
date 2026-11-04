# Kingsway Email Agent

An AI agent that reads new emails to Kingsway Jiu-Jitsu, labels them, and saves reply
drafts for a human to review and send. Built with Python, the Gmail API, and Claude tool use.

## How it works

```
new email → pre-filter (skip automated/bulk) → Claude agent
   ├─ categorize       → Gmail label (Leads, Kids & Teens, Visitors, ...)
   ├─ create_draft     → reply draft saved on the thread
   ├─ escalate         → "Needs Review" label
   └─ no_reply_needed  → label only
→ mark "AI Processed" → log to SQLite
```

- **Facts** come only from `school_info.yaml` (schedule, pricing, policies).
- **The agent only drafts.** It can't send, forward, or delete anything, and it can only act on the email it was handed.
- Low-confidence drafts are automatically flagged for review.
- Emails are treated as untrusted data (prompt-injection attempts get escalated).

## Setup

Requires Python 3.10+.

1. **Google Cloud** (console.cloud.google.com), signed in as the inbox account:
   - Create a project, then **APIs & Services → Library → Gmail API → Enable**.
   - **OAuth consent screen:** *Internal* if it's a Google Workspace account; otherwise *External* and add yourself as a test user.
   - **Credentials → Create credentials → OAuth client ID → Desktop app.** Download it as `credentials.json` into this folder.
2. **Anthropic API key** from console.anthropic.com. Copy `.env.example` to `.env` and paste the key in.
3. Install:
   ```bash
   python3 -m venv venv && source venv/bin/activate
   pip install -r requirements.txt
   ```

## Usage

```bash
python main.py test-gmail            # Phase 1: confirm Gmail access (opens browser to log in once)
python main.py run --dry-run         # see what the agent WOULD do; touches nothing in Gmail
python main.py run                   # create drafts + labels for real
python main.py run --loop --every 5  # keep checking every 5 minutes
```

Options: `--limit 10` (emails per run), `--days 2` (how far back to look).

## Files

| File | What it does |
|---|---|
| `main.py` | CLI, pre-filter, runs the loop |
| `agent.py` | System prompt, tools, Claude tool-use loop |
| `gmail_client.py` | Gmail auth, reading, labels, reply drafts |
| `db.py` | SQLite log of every decision (`agent_log.db`) |
| `school_info.yaml` | Knowledge base. Edit this when prices or the schedule change |

## Roadmap

- [ ] Eval set: ~50 labeled emails + accuracy script
- [ ] Learn from past replies: pull sent threads, retrieve similar examples as style guides
- [ ] Feedback loop: compare drafts to what was actually sent
- [ ] Gymdesk booking link in replies
- [ ] Deploy to Cloud Run + Cloud Scheduler; Gmail push notifications
- [ ] Daily summary email
