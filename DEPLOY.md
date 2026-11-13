# Running 24/7 on Google Cloud

The agent runs as a **Cloud Run Job** that **Cloud Scheduler** starts every 10 minutes.

- Secrets (`credentials.json`, `token.json`, the Anthropic key) live in **Secret Manager**,
  never inside the app.
- The agent's memory (`agent_log.db`: learned staff replies + decision log) lives in a
  **Cloud Storage bucket**. Each run loads it, works, and saves it back, so nothing it
  learned is ever lost.

## Before your first deploy
1. On your laptop, run `python main.py learn --days 365` so the agent has learned from
   your past replies. The first deploy copies that memory to the cloud.
2. Make sure `python main.py test-gmail` works (so `token.json` is fresh).
3. Turn on billing for the `kingsway-email-agent` project (Cloud Console → Billing).
4. Install the gcloud CLI: `brew install --cask google-cloud-sdk`
5. Log in: `gcloud auth login` (use admin@kingswayjiujitsu.com)

## Deploy
```bash
bash deploy.sh
```
Then **stop the loop on your laptop** (Ctrl + C). Only one copy should run at a time.

## Everyday
- **Changed `school_info.yaml` or code?** Re-run `bash deploy.sh`. (Your cloud memory is kept.)
- **Run it right now:** `gcloud run jobs execute kingsway-email-agent --region us-central1`
- **See what it did:** Cloud Console → Cloud Run → Jobs → kingsway-email-agent → Logs
- **Pause it:** Cloud Console → Cloud Scheduler → kingsway-email-agent-schedule → Pause
- **Copy the cloud's memory to your laptop** (e.g. to look at the log):
  `gcloud storage cp gs://kingsway-agent-state-PROJECT_NUMBER/agent_log.db .`
  (deploy.sh prints the exact bucket name at the end)

## If Gmail stops working ("token is missing or revoked")
Run `python main.py test-gmail` on your laptop (log in again), then `bash deploy.sh`.
