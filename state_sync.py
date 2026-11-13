"""Keeps the agent's memory (agent_log.db) between cloud runs.

Each cloud run starts on a blank machine. If STATE_BUCKET is set, we download
agent_log.db from that Google Cloud Storage bucket before the run and upload it
again afterwards, so learned replies and the decision log are never lost.

On your laptop STATE_BUCKET isn't set, so this does nothing.
"""
import os

import db

OBJECT_NAME = "agent_log.db"


def _blob():
    bucket = os.getenv("STATE_BUCKET")
    if not bucket:
        return None
    from google.cloud import storage  # only needed in the cloud
    return storage.Client().bucket(bucket).blob(OBJECT_NAME)


def download() -> None:
    blob = _blob()
    if blob is None:
        return
    if blob.exists():
        blob.download_to_filename(db.DB_PATH)
        print(f"Loaded memory from gs://{blob.bucket.name}/{OBJECT_NAME}")
    else:
        print("No saved memory yet; starting fresh.")


def upload() -> None:
    blob = _blob()
    if blob is None or not os.path.exists(db.DB_PATH):
        return
    blob.upload_from_filename(db.DB_PATH)
    print(f"Saved memory to gs://{blob.bucket.name}/{OBJECT_NAME}")
