# PAX Windows packaging

Build on **Windows 10/11 x64** with 64-bit Python 3.11. PyInstaller cannot produce a native Windows executable when run on Linux or macOS.

## Outputs

- Portable folder: `dist/PAX/` containing `PAX.exe` and its runtime files (onedir; recommended for Qt and audio stability).
- Optional single-file executable: `dist/PAX.exe` (onefile; slower cold start due to extraction).
- Installer: `dist/installer/PAX-Setup.exe` (Inno Setup 6).

## Build

From PowerShell at the repository root:

```powershell
.\packaging\windows\build.ps1 -Mode onedir -Installer
```

Use `-Mode onefile` for a single-file executable. The installer can package either layout.

The GitHub Actions workflow builds on Windows and uploads the output artifact. Run it from Actions → PAX Windows build → Run workflow. The workflow does not receive or bundle local API keys, Gmail OAuth secrets, DPAPI tokens, or user memory.

## User data and first launch

PAX stores mutable data under `%LOCALAPPDATA%\PAX` by default: `config\api_keys.json`, `config\gmail_client_secret.json`, encrypted Gmail token, memory, and logs. The installer writes application files under Program Files; user data stays separate and survives upgrades/uninstall.

For a portable folder that keeps data alongside the executable, set `PAX_DATA_DIR` to a writable directory before launch (for example, `PAX_DATA_DIR=%~dp0Data` in a companion .cmd file). PAX will not write credentials into Program Files.

## Runtime requirements

- Internet access for cloud Gemini AI and Gmail API. No model weights are bundled.
- Gmail OAuth Desktop credentials are supplied by the user as `config\gmail_client_secret.json` inside the PAX data directory; first connection opens a browser sign-in.
- Playwright browser automation needs its browser runtime. Install Chromium/Firefox with the packaged Playwright version when that feature is used; browsers are not bundled.
- Microphone, speaker and screen-control features depend on Windows device/privacy permissions.
- Diagnostic log: `%LOCALAPPDATA%\PAX\logs\pax.log`.

The current pipeline prepares the executable and installer; a successful build is not a substitute for live Gmail, Gemini, microphone, and desktop-automation validation on a Windows 10 machine.
