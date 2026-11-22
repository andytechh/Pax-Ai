"""Gmail API client with Windows DPAPI-protected OAuth token storage."""
from __future__ import annotations

import base64
import mimetypes
import os
import threading
import time
from email.message import EmailMessage
from pathlib import Path

from memory.config_manager import get_base_dir

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.send",
]
BASE = get_base_dir() / "config"
CLIENT_SECRET = BASE / "gmail_client_secret.json"
TOKEN_FILE = BASE / "gmail_token.dpapi"


def _protect(data: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("PAX Gmail token storage is configured for Windows DPAPI only.")
    import win32crypt
    return win32crypt.CryptProtectData(data, "PAX Gmail OAuth token", None, None, None, 0)


def _unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("PAX Gmail token storage is configured for Windows DPAPI only.")
    import win32crypt
    return win32crypt.CryptUnprotectData(data, None, None, None, 0)[1]


def _save_token(credentials) -> None:
    BASE.mkdir(parents=True, exist_ok=True)
    payload = credentials.to_json().encode("utf-8")
    encrypted = _protect(payload)
    tmp = TOKEN_FILE.with_suffix(".tmp")
    tmp.write_bytes(encrypted)
    os.replace(tmp, TOKEN_FILE)


def _credentials(interactive: bool = False):
    if os.name != "nt":
        raise RuntimeError("The Gmail integration is currently configured for Windows 10.")
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if TOKEN_FILE.exists():
        try:
            import json
            raw = _unprotect(TOKEN_FILE.read_bytes()).decode("utf-8")
            creds = Credentials.from_authorized_user_info(json.loads(raw), SCOPES)
        except Exception as exc:
            raise RuntimeError(f"Could not unlock the saved Gmail token with this Windows account: {exc}") from exc
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save_token(creds)
    if creds and creds.valid:
        return creds
    if not interactive:
        raise RuntimeError("Gmail is not connected. Run the Gmail 'connect' operation first.")
    if not CLIENT_SECRET.exists():
        raise RuntimeError("Add your Google OAuth Desktop app JSON as config/gmail_client_secret.json, then run Gmail connect. Do not commit this file.")
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
    creds = flow.run_local_server(host="127.0.0.1", port=0, open_browser=True, timeout_seconds=180)
    if not creds or not creds.valid:
        raise RuntimeError("Google OAuth did not return valid credentials.")
    _save_token(creds)
    return creds


def service(interactive: bool = False):
    from googleapiclient.discovery import build
    creds = _credentials(interactive=interactive)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def connected() -> bool:
    if not TOKEN_FILE.exists():
        return False
    try:
        _credentials(interactive=False)
        return True
    except Exception:
        return False


def _decode(value: str) -> str:
    if not value:
        return ""
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("utf-8", errors="replace")


def _body(payload: dict) -> str:
    mime = payload.get("mimeType", "")
    data = (payload.get("body") or {}).get("data")
    if data and (mime == "text/plain" or not mime):
        return _decode(data)
    parts = payload.get("parts") or []
    for part in parts:
        found = _body(part)
        if found:
            return found
    if data and mime == "text/html":
        import re
        return re.sub(r"<[^>]+>", " ", _decode(data)).strip()
    return ""


def _headers(payload: dict) -> dict:
    return {str(h.get("name", "")).lower(): str(h.get("value", ""))
            for h in (payload.get("headers") or [])}


def list_messages(query: str = "in:inbox", limit: int = 10) -> list[dict]:
    api = service()
    limit = max(1, min(int(limit), 30))
    result = api.users().messages().list(userId="me", q=query or "in:inbox", maxResults=limit).execute()
    rows = []
    for item in result.get("messages", []):
        msg = api.users().messages().get(userId="me", id=item["id"], format="full").execute()
        headers = _headers(msg.get("payload") or {})
        rows.append({"id": msg.get("id", ""), "thread_id": msg.get("threadId", ""),
                     "from": headers.get("from", ""), "to": headers.get("to", ""),
                     "subject": headers.get("subject", "(no subject)"),
                     "date": headers.get("date", ""), "snippet": msg.get("snippet", ""),
                     "body": _body(msg.get("payload") or {})[:12000],
                     "labels": msg.get("labelIds", [])})
    return rows


def read_message(message_id: str) -> dict:
    api = service()
    msg = api.users().messages().get(userId="me", id=message_id, format="full").execute()
    headers = _headers(msg.get("payload") or {})
    return {"id": msg.get("id", ""), "thread_id": msg.get("threadId", ""),
            "from": headers.get("from", ""), "to": headers.get("to", ""),
            "cc": headers.get("cc", ""), "subject": headers.get("subject", "(no subject)"),
            "date": headers.get("date", ""), "snippet": msg.get("snippet", ""),
            "body": _body(msg.get("payload") or {})[:30000],
            "labels": msg.get("labelIds", [])}


def _message_raw(to: str, subject: str, body: str, attachment_paths: list[str]) -> str:
    if not to.strip() or "@" not in to:
        raise ValueError("A valid recipient address is required.")
    if not subject.strip():
        raise ValueError("An email subject is required.")
    if len(body) > 50000:
        raise ValueError("Email body is limited to 50,000 characters.")
    mail = EmailMessage()
    mail["To"] = to.strip()
    mail["Subject"] = subject.strip()
    mail.set_content(body)
    total = 0
    for raw_path in attachment_paths or []:
        path = Path(raw_path).expanduser().resolve(strict=True)
        if not path.is_file():
            raise ValueError(f"Attachment is not a file: {path}")
        size = path.stat().st_size
        total += size
        if total > 20 * 1024 * 1024:
            raise ValueError("Attachments exceed the 20 MB combined limit.")
        mime, _ = mimetypes.guess_type(path.name)
        main_type, sub_type = (mime or "application/octet-stream").split("/", 1)
        mail.add_attachment(path.read_bytes(), maintype=main_type, subtype=sub_type, filename=path.name)
    return base64.urlsafe_b64encode(mail.as_bytes()).decode("ascii").rstrip("=")


def create_draft(to: str, subject: str, body: str, attachment_paths: list[str] | None = None) -> dict:
    api = service()
    raw = _message_raw(to, subject, body, attachment_paths or [])
    result = api.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
    if not result.get("id"):
        raise RuntimeError("Gmail did not return a draft ID; draft creation is unverified.")
    return {"draft_id": result["id"], "to": to, "subject": subject,
            "attachment_count": len(attachment_paths or [])}


def draft_details(draft_id: str) -> dict:
    api = service()
    draft = api.users().drafts().get(userId="me", id=draft_id, format="full").execute()
    msg = draft.get("message") or {}
    payload = msg.get("payload") or {}
    headers = _headers(payload)
    names = []
    def walk(part):
        filename = part.get("filename")
        if filename:
            names.append(filename)
        for child in part.get("parts") or []:
            walk(child)
    walk(payload)
    return {"draft_id": draft_id, "message_id": msg.get("id", ""),
            "to": headers.get("to", ""), "cc": headers.get("cc", ""),
            "subject": headers.get("subject", "(no subject)"),
            "snippet": msg.get("snippet", ""), "attachments": names}


def send_draft(draft_id: str) -> dict:
    api = service()
    result = api.users().drafts().send(userId="me", body={"id": draft_id}).execute()
    message_id = result.get("id")
    if not message_id:
        raise RuntimeError("Gmail accepted the send request without returning a message ID; delivery is unverified.")
    verified = api.users().messages().get(userId="me", id=message_id, format="metadata", metadataHeaders=["To", "Subject"]).execute()
    headers = _headers(verified.get("payload") or {})
    return {"message_id": message_id, "to": headers.get("to", ""),
            "subject": headers.get("subject", ""), "verified": bool(verified.get("id"))}


_monitor_lock = threading.Lock()
_monitor_thread = None
_monitor_stop = threading.Event()
_monitor_state = {"running": False, "last_check": "", "last_error": ""}


def monitor_status() -> dict:
    with _monitor_lock:
        return dict(_monitor_state)


def start_monitor(player=None, interval_seconds: int = 300) -> str:
    global _monitor_thread
    interval = max(60, min(int(interval_seconds), 3600))
    with _monitor_lock:
        if _monitor_thread and _monitor_thread.is_alive():
            return "Gmail monitoring is already running."
        _monitor_stop.clear()
        _monitor_state.update(running=True, last_error="")

        def worker():
            known = set()
            first = True
            while not _monitor_stop.is_set():
                try:
                    rows = list_messages("in:inbox is:unread", 20)
                    current = {r["id"] for r in rows}
                    new_ids = set() if first else current - known
                    first = False
                    known = current
                    _monitor_state["last_check"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    _monitor_state["last_error"] = ""
                    if new_ids and player:
                        for row in rows:
                            if row["id"] in new_ids:
                                notice = f"New unread Gmail from {row['from']}: {row['subject']}"
                                try:
                                    player.write_log("MAIL: " + notice)
                                except Exception:
                                    pass
                    _monitor_stop.wait(interval)
                except Exception as exc:
                    _monitor_state["last_error"] = str(exc)[:300]
                    _monitor_stop.wait(min(interval, 120))
            with _monitor_lock:
                _monitor_state["running"] = False

        _monitor_thread = threading.Thread(target=worker, name="pax-gmail-monitor", daemon=True)
        _monitor_thread.start()
    return f"Gmail monitoring started. Inbox is checked every {interval} seconds; alerts appear in PAX activity log."


def stop_monitor() -> str:
    _monitor_stop.set()
    with _monitor_lock:
        _monitor_state["running"] = False
    return "Gmail monitoring stop requested."
