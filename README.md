# Claude Code Usage — Windows Tray App

A lightweight system-tray app that shows your Claude Code **session (5h)** and
**weekly (7d)** usage in real time, right in the Windows notification area.

---

## What it shows

| Indicator | Meaning |
|-----------|---------|
| Top bar (5h) | % of your current 5-hour rolling session used |
| Bottom bar (7d) | % of your 7-day weekly limit used |
| Tooltip | Exact %s, countdown to session reset, weekly reset date |
| Toast notification | Fires once when either bar crosses 75% or 90% |

Icon colours: 🟢 green < 75% · 🟡 amber 75–89% · 🔴 red 90%+

---

## Requirements

- Python 3.11+ (64-bit)
- Windows 10/11
- An active Claude Pro/Max subscription with Claude Code installed

---

## Quick start

```bat
pip install pystray Pillow requests
python claude_usage_tray.py
```

The app finds your token automatically from:
```
%USERPROFILE%\.claude\.credentials.json
```
That file is created when you log into Claude Code for the first time.

---

## Token not found?

If the app shows "Token not found", set this environment variable instead:

```bat
set ANTHROPIC_AUTH_TOKEN=sk-ant-oat01-...
python claude_usage_tray.py
```

Or add it permanently via **System Properties → Environment Variables**.

---

## Package as a standalone .exe (no Python needed)

Install PyInstaller, then run:

```bat
pip install pyinstaller
pyinstaller --noconsole --onefile --name ClaudeUsage claude_usage_tray.py
```

The `.exe` ends up in the `dist\` folder. Double-click it — no installation needed.

---

## Auto-start with Windows

1. Press **Win + R**, type `shell:startup`, press Enter
2. Create a shortcut to `ClaudeUsage.exe` (or `claude_usage_tray.pyw`) in that folder
3. It will launch silently every time you log in

---

## Configuration (top of the script)

| Variable | Default | Description |
|----------|---------|-------------|
| `POLL_INTERVAL_SECONDS` | `60` | How often to refresh usage data |
| `WARN_THRESHOLD` | `75` | % that turns the icon amber + sends a notification |
| `CRIT_THRESHOLD` | `90` | % that turns the icon red + sends a notification |
| `NOTIFY_ON_WARN` | `True` | Enable/disable Windows toast notifications |

---

## Disclaimer

This app uses an **unofficial, undocumented** Anthropic endpoint
(`/api/oauth/usage`). It may break if Anthropic changes the API.
The token is read locally and never sent anywhere except Anthropic's servers.
