#!/usr/bin/env bash
# Deploys the agent to Google Cloud Run (as a Job) and schedules it every 10 minutes.
# Run from the project folder:  bash deploy.sh
# Safe to re-run: it updates things that already exist.
set -euo pipefail

PROJECT=kingsway-email-agent
REGION=us-central1
JOB=kingsway-email-agent
SCHEDULE="*/10 * * * *"   # every 10 minutes
TZ="America/Chicago"

gcloud config set project "$PROJECT"

echo "== Enabling Google Cloud services (first time takes a minute) =="
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com cloudscheduler.googleapis.com \
  storage.googleapis.com

echo "== Uploading secrets to Secret Manager =="
ANTHROPIC_KEY=$(grep '^ANTHROPIC_API_KEY=' .env | cut -d= -f2-)
upsert_secret () {  # name, file-or-stdin
  if gcloud secrets describe "$1" >/dev/null 2>&1; then
    gcloud secrets versions add "$1" --data-file="$2"
  else
    gcloud secrets create "$1" --data-file="$2"
  fi
}
upsert_secret gmail-credentials credentials.json
upsert_secret gmail-token token.json
printf '%s' "$ANTHROPIC_KEY" | upsert_secret anthropic-api-key -

PROJECT_NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

BUCKET="kingsway-agent-state-${PROJECT_NUMBER}"

echo "== Setting up permanent memory (Cloud Storage bucket) =="
if ! gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://$BUCKET" --location "$REGION" --uniform-bucket-level-access
fi
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member="serviceAccount:$SA" \
  --role=roles/storage.objectAdmin >/dev/null
# First deploy only: seed the cloud with what the agent already learned on this laptop.
# (Never overwrites memory the cloud has built up since.)
if ! gcloud storage objects describe "gs://$BUCKET/agent_log.db" >/dev/null 2>&1; then
  if [ -f agent_log.db ]; then
    echo "Copying this laptop's learned replies to the cloud..."
    gcloud storage cp agent_log.db "gs://$BUCKET/agent_log.db"
  fi
fi

echo "== Letting the job read the secrets =="
for s in gmail-credentials gmail-token anthropic-api-key; do
  gcloud secrets add-iam-policy-binding "$s" --member="serviceAccount:$SA" \
    --role=roles/secretmanager.secretAccessor >/dev/null
done

echo "== Building and deploying the job =="
gcloud run jobs deploy "$JOB" --source . --region "$REGION" \
  --tasks 1 --max-retries 0 --task-timeout 9m \
  --set-secrets "/secrets/credentials/credentials.json=gmail-credentials:latest,/secrets/token/token.json=gmail-token:latest,ANTHROPIC_API_KEY=anthropic-api-key:latest" \
  --set-env-vars "CLAUDE_MODEL=claude-sonnet-5-5,STATE_BUCKET=$BUCKET"

echo "== Scheduling it =="
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$SA" \
  --role=roles/run.invoker >/dev/null
URI="https://run.googleapis.com/v2/projects/${PROJECT}/locations/${REGION}/jobs/${JOB}:run"
if gcloud scheduler jobs describe "${JOB}-schedule" --location "$REGION" >/dev/null 2>&1; then
  gcloud scheduler jobs update http "${JOB}-schedule" --location "$REGION" \
    --schedule "$SCHEDULE" --time-zone "$TZ" --uri "$URI" --http-method POST \
    --oauth-service-account-email "$SA"
else
  gcloud scheduler jobs create http "${JOB}-schedule" --location "$REGION" \
    --schedule "$SCHEDULE" --time-zone "$TZ" --uri "$URI" --http-method POST \
    --oauth-service-account-email "$SA"
fi

echo
echo "Done. It now runs every 10 minutes."
echo "Run it right now:   gcloud run jobs execute $JOB --region $REGION"
echo "See its output:     Cloud Console > Cloud Run > Jobs > $JOB > Logs"
echo "Memory is saved in: gs://$BUCKET/agent_log.db"
echo
echo "IMPORTANT: stop the loop on your laptop (Ctrl + C) so they don't both draft replies."
