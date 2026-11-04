"""Thin wrapper around the Gmail API: auth, reading, labels, and reply drafts."""
import base64
import os
import re
from email.message import EmailMessage
from email.utils import parseaddr

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# modify = read, label, and create drafts. It cannot permanently delete mail.
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data.encode()).decode("utf-8", errors="ignore")


def _strip_html(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", html)
    text = re.sub(r"<[^>]+>", "", html)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _find_part(payload: dict, mime: str):
    if payload.get("mimeType") == mime and payload.get("body", {}).get("data"):
        return _decode(payload["body"]["data"])
    for part in payload.get("parts", []) or []:
        found = _find_part(part, mime)
        if found:
            return found
    return None


def extract_body(payload: dict) -> str:
    text = _find_part(payload, "text/plain")
    if text:
        return text.strip()
    html = _find_part(payload, "text/html")
    return _strip_html(html) if html else ""


class GmailClient:
    def __init__(self, credentials_path="credentials.json", token_path="token.json"):
        creds = self._authenticate(credentials_path, token_path)
        self.service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        self.me = self.service.users().getProfile(userId="me").execute()["emailAddress"]
        self._label_ids: dict[str, str] = {}

    # ---------- auth ----------
    @staticmethod
    def _authenticate(credentials_path, token_path):
        creds = None
        if os.path.exists(token_path):
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception:
                    creds = None  # token revoked/expired (e.g. 7-day testing limit) -> log in again
            if not creds or not creds.valid:
                flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
                creds = flow.run_local_server(port=0)
            with open(token_path, "w") as f:
                f.write(creds.to_json())
        return creds

    # ---------- reading ----------
    def list_message_ids(self, query: str, max_results: int = 20) -> list[str]:
        resp = self.service.users().messages().list(
            userId="me", q=query, maxResults=max_results
        ).execute()
        return [m["id"] for m in resp.get("messages", [])]

    def get_message(self, msg_id: str) -> dict:
        raw = self.service.users().messages().get(userId="me", id=msg_id, format="full").execute()
        return self._parse(raw)

    def get_thread(self, thread_id: str) -> list[dict]:
        raw = self.service.users().threads().get(userId="me", id=thread_id, format="full").execute()
        return [self._parse(m) for m in raw.get("messages", [])]

    def _parse(self, raw: dict) -> dict:
        headers = {h["name"].lower(): h["value"] for h in raw["payload"].get("headers", [])}
        from_name, from_email = parseaddr(headers.get("from", ""))
        return {
            "id": raw["id"],
            "thread_id": raw["threadId"],
            "label_ids": raw.get("labelIds", []),
            "from": headers.get("from", ""),
            "from_email": from_email.lower(),
            "from_name": from_name,
            "reply_to": headers.get("reply-to", ""),
            "to": headers.get("to", ""),
            "subject": headers.get("subject", "(no subject)"),
            "date": headers.get("date", ""),
            "message_id": headers.get("message-id", ""),
            "references": headers.get("references", ""),
            "headers": headers,
            "body": extract_body(raw["payload"]),
        }

    # ---------- labels ----------
    def ensure_labels(self, names: list[str]) -> None:
        existing = self.service.users().labels().list(userId="me").execute().get("labels", [])
        self._label_ids = {l["name"]: l["id"] for l in existing}
        for name in names:
            if name not in self._label_ids:
                created = self.service.users().labels().create(
                    userId="me",
                    body={"name": name, "labelListVisibility": "labelShow",
                          "messageListVisibility": "show"},
                ).execute()
                self._label_ids[name] = created["id"]

    def add_labels(self, msg_id: str, names: list[str]) -> None:
        ids = [self._label_ids[n] for n in names if n in self._label_ids]
        if ids:
            self.service.users().messages().modify(
                userId="me", id=msg_id, body={"addLabelIds": ids}
            ).execute()

    # ---------- drafts ----------
    def build_reply(self, original: dict, body: str) -> EmailMessage:
        msg = EmailMessage()
        msg["To"] = original["reply_to"] or original["from"]
        msg["From"] = self.me
        subject = original["subject"]
        msg["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        if original["message_id"]:
            msg["In-Reply-To"] = original["message_id"]
            msg["References"] = f'{original["references"]} {original["message_id"]}'.strip()
        msg.set_content(body)
        return msg

    def create_reply_draft(self, original: dict, body: str) -> str:
        raw = base64.urlsafe_b64encode(self.build_reply(original, body).as_bytes()).decode()
        draft = self.service.users().drafts().create(
            userId="me",
            body={"message": {"raw": raw, "threadId": original["thread_id"]}},
        ).execute()
        return draft["id"]
