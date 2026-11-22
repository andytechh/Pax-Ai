# PAX Windows 10 + Gmail Upgrade

This branch adds a Windows desktop operator and a Gmail API integration while preserving the existing PAX action/plugin loaders.

## Windows desktop control

The `desktop_control` action uses Windows UI Automation (pywinauto) to enumerate, focus, minimize, maximize, and close windows, and click named controls. Clipboard-assisted text entry, keyboard shortcuts, drag/scroll, and screenshot capture are also available. Named UIA controls should be preferred over screen coordinates. Closing windows, coordinate-only clicks, risky named controls (send/submit/delete/purchase/etc.), and consequential shortcuts require the PAX confirmation UI. Desktop operations are intended for the interactive user's Windows session and are not a substitute for application-specific APIs.

## Gmail setup

1. In Google Cloud Console, create or select a project and enable the Gmail API.
2. Configure the OAuth consent screen and create an OAuth client of type **Desktop app**.
3. Download the client JSON and save it locally as `config/gmail_client_secret.json`. Do not commit it or share it.
4. Start PAX and ask it to connect Gmail. Complete Google's consent flow in the browser.
5. The refresh token is stored as `config/gmail_token.dpapi`, encrypted with Windows DPAPI and bound to the Windows user account. It is not stored in source control.

The integration supports inbox listing, Gmail search syntax, message reading, draft creation, attachments, and optional unread-inbox polling. Sending an existing draft always displays the recipient, CC, subject, preview, and attachment names in the PAX confirmation UI. The Gmail send API is not called until the user approves that prompt. Draft creation does not send mail.

## Cloud AI policy

PAX's Gemini Live conversation and Gemini reasoning ladder are cloud services. Local model endpoints are not added as a fallback for these new features. Existing provider settings remain part of the upstream application; choose a hosted HTTPS provider in the provider settings if you use a sidecar provider. Do not configure localhost, private-LAN, or plain-HTTP endpoints when operating in cloud-only mode.

## Permission model

- Normal local operations (opening/focusing apps, reading/searching email, composing drafts, ordinary UI interaction) execute without a confirmation prompt.
- Sending Gmail drafts is always gated by the actual PAX confirmation UI. The model cannot approve it by passing a parameter.
- File sharing, purchases, software installation, credential changes, security changes, destructive file operations, and power operations should continue to use the existing confirmation gate or a more restrictive policy.

## Security notes

- Never commit `config/gmail_client_secret.json`, `config/gmail_token.dpapi`, API keys, or OAuth tokens.
- The Gmail integration requests read-only, compose, and send scopes. Review Google's consent screen carefully.
- Monitoring is opt-in and polls unread inbox messages at a configurable interval (minimum 60 seconds); it is not a push-notification service.
- PAX must report only observed outcomes. A pending send approval is not a sent email.

## Validation

Run from a Windows 10 machine with Python 3.11–3.13:

```powershell
py -3.11 setup.py
py -3.11 -m compileall -q actions core plugins
py -3.11 -m unittest discover -s tests
py -3.11 main.py
```

Complete Gmail OAuth and test with a draft addressed to an account you control before using it for external recipients.
