"""Gmail inbox, search, drafts, approval-gated sending, and inbox monitoring."""
from __future__ import annotations

PLUGIN = {
    "name": "gmail_control",
    "description": ("Manage the user's Gmail account only after Google OAuth is connected. Connect, list inbox messages, search Gmail, read a message, create a draft with optional attachments, start/stop inbox monitoring, or send an existing draft. Sending is ALWAYS held behind the PAX on-screen human confirmation gate; never claim a message was sent while confirmation is pending."),
    "tier": "core",
    "brief": "Read/search Gmail, create drafts, monitor unread mail, and send only after approval.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "operation": {"type": "STRING", "enum": ["status", "connect", "inbox", "search", "read", "create_draft", "send_draft", "monitor_start", "monitor_stop", "monitor_status"]},
            "query": {"type": "STRING"}, "message_id": {"type": "STRING"},
            "draft_id": {"type": "STRING"}, "limit": {"type": "INTEGER"},
            "to": {"type": "STRING"}, "subject": {"type": "STRING"},
            "body": {"type": "STRING"},
            "attachment_paths": {"type": "ARRAY", "items": {"type": "STRING"}},
            "interval_seconds": {"type": "INTEGER"},
        },
        "required": ["operation"],
    },
}


def _send_after_approval(draft_id: str, expected: dict) -> str:
    from plugins._gmail_core import draft_details, send_draft
    current = draft_details(draft_id)
    fields = ("to", "cc", "subject", "snippet", "attachments")
    if any(current.get(key) != expected.get(key) for key in fields):
        raise RuntimeError("The Gmail draft changed after approval was requested. Nothing was sent; review it and approve again.")
    result = send_draft(draft_id)
    if not result.get("verified"):
        raise RuntimeError("Gmail send could not be verified.")
    return f"Sent and verified Gmail message {result['message_id']} to {result['to']} with subject '{result['subject']}'."


def run(parameters: dict, player=None, session_memory=None) -> str:
    del session_memory
    op = parameters.get("operation", "status")
    try:
        from plugins import _gmail_core as gmail
        if op == "status":
            return "Gmail is connected." if gmail.connected() else "Gmail is not connected. Place your Google OAuth Desktop client JSON at config/gmail_client_secret.json, then run connect."
        if op == "connect":
            gmail.service(interactive=True)
            return "Google OAuth completed. Gmail connection is ready; the refresh token is encrypted with Windows DPAPI for this Windows account."
        if op in {"inbox", "search"}:
            query = parameters.get("query", "in:inbox") if op == "search" else "in:inbox"
            rows = gmail.list_messages(query, parameters.get("limit", 10))
            if not rows:
                return "No Gmail messages matched that query."
            lines = [f"{r['date']} | From: {r['from']} | Subject: {r['subject']} | {r['snippet']}" for r in rows]
            return f"Found {len(rows)} Gmail message(s):\n" + "\n".join(lines)
        if op == "read":
            message_id = str(parameters.get("message_id", "")).strip()
            if not message_id:
                return "Provide the Gmail message ID to read."
            row = gmail.read_message(message_id)
            return (f"From: {row['from']}\nTo: {row['to']}\nDate: {row['date']}\nSubject: {row['subject']}\n\n"
                    f"{row['body'] or row['snippet']}")
        if op == "create_draft":
            result = gmail.create_draft(parameters.get("to", ""), parameters.get("subject", ""),
                                        parameters.get("body", ""), parameters.get("attachment_paths", []))
            return (f"Gmail draft created and verified. Draft ID: {result['draft_id']}. "
                    f"To: {result['to']}; subject: {result['subject']}; attachments: {result['attachment_count']}. Nothing was sent.")
        if op == "send_draft":
            draft_id = str(parameters.get("draft_id", "")).strip()
            if not draft_id:
                return "Provide the Gmail draft ID to send."
            details = gmail.draft_details(draft_id)
            from core import confirm
            if confirm.pending_title():
                return "Another action is awaiting confirmation. Resolve that request before asking to send this draft."
            attachment_text = ", ".join(details["attachments"]) if details["attachments"] else "none"
            detail = (f"Recipient: {details['to']}\nCC: {details['cc'] or 'none'}\n"
                      f"Subject: {details['subject']}\nPreview: {details['snippet'][:500]}\n"
                      f"Attachments: {attachment_text}\n\nApprove only if these details are correct.")
            return confirm.request(
                key="gmail-send-" + draft_id[:24],
                title="Send Gmail draft", detail=detail,
                run=lambda: _send_after_approval(draft_id, details),
            )
        if op == "monitor_start":
            return gmail.start_monitor(player, parameters.get("interval_seconds", 300))
        if op == "monitor_stop":
            return gmail.stop_monitor()
        if op == "monitor_status":
            return str(gmail.monitor_status())
        return f"Unknown Gmail operation: {op}"
    except Exception as exc:
        return f"Gmail operation failed: {exc}"
