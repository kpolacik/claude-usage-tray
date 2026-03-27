# Claude Code Usage Tray

A Windows system tray app that shows your Claude Code usage at a glance.

Left-click the tray icon to see a popup with your **5-hour session** and **7-day weekly** usage, complete with progress bars, reset countdowns, and a live "last updated" timer. Right-click for quick actions.

![screenshot](https://raw.githubusercontent.com/kpolacik/claude-usage-tray/master/screenshot.png)

## Features

- Session (5h) and weekly (7d) usage bars with percentages
- Live countdown to session reset, date for weekly reset
- Dark and light mode (toggle persists across restarts)
- Windows toast notifications at 75% and 90% thresholds
- Refreshes automatically every 5 minutes
- Single-instance guard (won't run twice)
- Starts with Windows (optional, see below)

## Requirements

- **Python 3.11+** (64-bit)
- **Windows 10 or 11**
- An active **Claude Pro or Max** subscription with Claude Code installed

## Quick start

```bat
git clone https://github.com/kpolacik/claude-usage-tray.git
cd claude-usage-tray
pip install -r requirements.txt
pythonw claude_usage_tray.py
```

The app reads your OAuth token automatically from:

```
%USERPROFILE%\.claude\.credentials.json
```

This file is created the first time you log into Claude Code.

## Token not found?

If the popup shows "Token not found", you can set the token manually:

```bat
set ANTHROPIC_AUTH_TOKEN=sk-ant-oat01-...
pythonw claude_usage_tray.py
```

Or add `ANTHROPIC_AUTH_TOKEN` permanently via **System Properties > Environment Variables**.

## Auto-start with Windows

1. Press **Win + R**, type `shell:startup`, Enter
2. Create a shortcut in that folder pointing to:
   ```
   pythonw.exe "C:\path\to\claude_usage_tray.py"
   ```
3. The app will launch silently on every login

## Build a standalone .exe

```bat
pip install pyinstaller
pyinstaller --noconsole --onefile --add-data "icon.png;." --name ClaudeUsage claude_usage_tray.py
```

The `.exe` appears in `dist\`. No Python needed to run it.

## Configuration

These constants are at the top of `claude_usage_tray.py`:

| Variable | Default | Description |
|----------|---------|-------------|
| `POLL_INTERVAL_SECONDS` | `300` | How often usage data is refreshed (seconds) |
| `WARN_THRESHOLD` | `75` | % that triggers an amber notification |
| `CRIT_THRESHOLD` | `90` | % that triggers a red notification |
| `NOTIFY_ON_WARN` | `True` | Enable/disable Windows toast notifications |

Theme preference (dark/light) is saved to `%USERPROFILE%\.claude\tray_prefs.json`.

## Disclaimer

This app uses an **unofficial, undocumented** Anthropic endpoint (`/api/oauth/usage`). It may break if Anthropic changes the API. Your token is read locally and only sent to Anthropic's servers.

## License

MIT
