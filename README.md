# Claude Code Usage Tray

A Windows system tray app and VS Code extension that shows your Claude Code usage at a glance.

## Tray App

Left-click the tray icon to see a popup with your **5-hour session** and **7-day weekly** usage, complete with progress bars, reset countdowns, and a live "last updated" timer. Right-click for quick actions.

![screenshot](https://raw.githubusercontent.com/kpolacik/claude-usage-tray/master/screenshot.png)

### Features

- Session (5h) and weekly (7d) usage bars with percentages
- Live countdown to session reset, date for weekly reset
- Dark and light mode (toggle persists across restarts)
- Windows toast notifications at 75% and 90% thresholds
- Refreshes automatically every 5 minutes
- Auto-refreshes OAuth token — no manual re-login needed
- Exponential backoff on rate limits
- Single-instance guard (won't run twice)

### Requirements

- **Python 3.11+** (64-bit)
- **Windows 10 or 11**
- An active **Claude Pro or Max** subscription with Claude Code installed

### Quick start

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

### Auto-start with Windows

Double-click `launch.vbs` to start the tray without a console window. To make it launch automatically on every login:

1. Press **Win + R**, type `shell:startup`, Enter
2. Copy `launch.vbs` into that folder

### Token not found?

If the popup shows "Token not found", set the token manually:

```bat
set ANTHROPIC_AUTH_TOKEN=sk-ant-oat01-...
pythonw claude_usage_tray.py
```

Or add `ANTHROPIC_AUTH_TOKEN` permanently via **System Properties > Environment Variables**.

### Build a standalone .exe

```bat
pip install pyinstaller
pyinstaller --noconsole --onefile --add-data "icon.png;." --name ClaudeUsage claude_usage_tray.py
```

The `.exe` appears in `dist\`. No Python needed to run it.

### Configuration

These constants are at the top of `claude_usage_tray.py`:

| Variable | Default | Description |
|----------|---------|-------------|
| `POLL_INTERVAL_SECONDS` | `300` | How often usage data is refreshed (seconds) |
| `WARN_THRESHOLD` | `75` | % that triggers an amber notification |
| `CRIT_THRESHOLD` | `90` | % that triggers a red notification |
| `NOTIFY_ON_WARN` | `True` | Enable/disable Windows toast notifications |

Theme preference (dark/light) is saved to `%USERPROFILE%\.claude\tray_prefs.json`.

---

## VS Code Extension

A companion extension for VS Code-compatible IDEs (including Antigravity). Shows usage in the status bar and opens a detail panel on click.

### Features

- Status bar item: `⊙ 5h: 42% │ 7d: 18%`
- Color-coded: yellow at ≥75%, red at ≥90%
- Click to open a detail panel with progress bars and reset times
- Same auto token-refresh and backoff logic as the tray app
- No build step required

### Installation

1. Download `vscode-extension/claude-usage-1.0.0.vsix` from this repo
2. In your IDE: Extensions panel → `...` menu → **Install from VSIX**
3. Select the `.vsix` file and reload the IDE

The extension activates automatically on startup and reads credentials from the same `~/.claude/.credentials.json` file as the tray app.

---

## Disclaimer

This app uses an **unofficial, undocumented** Anthropic endpoint (`/api/oauth/usage`). It may break if Anthropic changes the API. Your token is read locally and only sent to Anthropic's servers.

## License

MIT
