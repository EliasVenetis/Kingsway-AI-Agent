FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code + knowledge file only. Secrets are mounted at runtime from Secret Manager.
COPY *.py school_info.yaml ./

ENV PYTHONUNBUFFERED=1 \
    HEADLESS=1 \
    CREDENTIALS_PATH=/secrets/credentials/credentials.json \
    TOKEN_PATH=/secrets/token/token.json \
    DB_PATH=/tmp/agent_log.db

# One pass per run; Cloud Scheduler starts a new run every few minutes.
CMD ["python", "main.py", "run", "--limit", "20", "--days", "2"]
