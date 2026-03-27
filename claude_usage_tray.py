"""
Claude Code Usage - Windows System Tray App
Shows session (5h) and weekly (7d) usage in the notification area.
"""

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import pystray
from PIL import Image, ImageDraw, ImageFont
import winreg
import ctypes

# ── Config ────────────────────────────────────────────────────────────────────
POLL_INTERVAL_SECONDS = 300         # how often to refresh usage data
WARN_THRESHOLD = 75                 # % at which tray icon turns yellow
CRIT_THRESHOLD = 90                 # % at which tray icon turns red
NOTIFY_ON_WARN = True               # Windows toast when crossing warn threshold
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"


# ── Token loading ─────────────────────────────────────────────────────────────

def load_token() -> str | None:
    """Try to load the OAuth access token from ~/.claude/.credentials.json"""
    try:
        if CREDENTIALS_PATH.exists():
            data = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
            token = (
                data.get("claudeAiOauth", {}).get("accessToken")
                or data.get("oauthToken")
                or data.get("accessToken")
            )
            if token:
                return token
    except Exception as e:
        print(f"[token] Failed to read credentials: {e}")

    # Fallback: check environment variable
    env_token = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("CLAUDE_TOKEN")
    if env_token:
        return env_token

    return None


# ── API fetch ─────────────────────────────────────────────────────────────────

class ApiError(Exception):
    def __init__(self, message: str):
        self.message = message


def fetch_usage(token: str) -> dict:
    try:
        resp = requests.get(
            USAGE_URL,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "claude-code/2.0.32",
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
            },
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 401:
            raise ApiError("Token expired — restart Claude Code")
        if resp.status_code == 429:
            raise ApiError("Rate limited — retrying soon")
        raise ApiError(f"HTTP {resp.status_code}")
    except ApiError:
        raise
    except Exception as e:
        raise ApiError(f"Network error: {e}")


# ── Time helpers ──────────────────────────────────────────────────────────────

def parse_reset(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def fmt_countdown(reset_dt: datetime | None) -> str:
    if not reset_dt:
        return "unknown"
    now = datetime.now(timezone.utc)
    delta = reset_dt - now
    if delta.total_seconds() <= 0:
        return "resetting..."
    total_s = int(delta.total_seconds())
    h, rem = divmod(total_s, 3600)
    m = rem // 60
    if h > 0:
        return f"{h}h {m}m"
    return f"{m}m"


def fmt_reset_date(reset_dt: datetime | None) -> str:
    if not reset_dt:
        return "unknown"
    local_dt = reset_dt.astimezone()
    return local_dt.strftime("%a %d %b, %H:%M")


# ── Icon drawing ──────────────────────────────────────────────────────────────

def _bar_color(pct: float) -> tuple:
    """Return RGB fill colour based on utilisation percentage."""
    if pct >= CRIT_THRESHOLD:
        return (220, 60, 60)      # red
    if pct >= WARN_THRESHOLD:
        return (230, 160, 30)     # amber
    return (80, 185, 120)         # green


def make_icon(session_pct: float | None, weekly_pct: float | None) -> Image.Image:
    """
    Render a 64×64 tray icon with two stacked progress bars.
    Top bar = 5-hour session, bottom bar = 7-day weekly.
    """
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    bg = (30, 30, 30, 230)
    radius = 8
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=bg)

    # ── "CC" label ────────────────────────────────────────────────────────────
    try:
        font_label = ImageFont.truetype("arial.ttf", 13)
        font_pct   = ImageFont.truetype("arial.ttf", 10)
    except Exception:
        font_label = ImageFont.load_default()
        font_pct   = font_label

    d.text((size // 2, 10), "CC", font=font_label, fill=(200, 200, 200), anchor="mm")

    # ── Bar helper ────────────────────────────────────────────────────────────
    def draw_bar(y_top: int, pct: float | None, label: str):
        bar_h = 10
        bar_w = size - 12
        x0 = 6
        # track
        d.rounded_rectangle([x0, y_top, x0 + bar_w, y_top + bar_h],
                             radius=3, fill=(70, 70, 70))
        # fill
        if pct is not None:
            fill_w = max(2, int(bar_w * min(pct, 100) / 100))
            d.rounded_rectangle([x0, y_top, x0 + fill_w, y_top + bar_h],
                                 radius=3, fill=_bar_color(pct))
        # label
        lbl = f"{label} {int(pct or 0)}%"
        d.text((size // 2, y_top + bar_h + 4), lbl,
               font=font_pct, fill=(180, 180, 180), anchor="mm")

    draw_bar(22, session_pct, "5h")
    draw_bar(44, weekly_pct,  "7d")

    return img


# ── Windows toast notification ────────────────────────────────────────────────

_notified: set = set()

def maybe_notify(icon: pystray.Icon, label: str, pct: float):
    """Fire a Windows balloon tip once per threshold crossing."""
    key = (label, pct >= CRIT_THRESHOLD, pct >= WARN_THRESHOLD)
    if key in _notified:
        return
    _notified.add(key)

    if pct >= CRIT_THRESHOLD:
        msg = f"{label} usage at {pct:.0f}% — almost out!"
    else:
        msg = f"{label} usage at {pct:.0f}% — getting high."

    try:
        icon.notify(msg, "Claude Code Usage")
    except Exception:
        pass


# ── Main app state ────────────────────────────────────────────────────────────

class UsageApp:
    def __init__(self):
        self.token: str | None = None
        self.last_data: dict | None = None
        self.error_msg: str | None = None
        self._lock = threading.Lock()
        self.icon: pystray.Icon | None = None

    # ── Tooltip / menu text ───────────────────────────────────────────────────

    def _status_lines(self) -> list[str]:
        with self._lock:
            data = self.last_data
            err  = self.error_msg

        if err:
            return [f"⚠  {err}"]

        if data is None:
            return ["Loading…"]

        lines = ["Claude Code Usage"]

        fh = data.get("five_hour")
        if fh:
            pct   = fh.get("utilization", 0)
            reset = parse_reset(fh.get("resets_at"))
            lines.append(f"Session (5h):  {pct:.0f}%  —  resets in {fmt_countdown(reset)}")

        sd = data.get("seven_day")
        if sd:
            pct   = sd.get("utilization", 0)
            reset = parse_reset(sd.get("resets_at"))
            lines.append(f"Weekly  (7d):  {pct:.0f}%  —  resets {fmt_reset_date(reset)}")

        op = data.get("seven_day_opus")
        if op and op.get("utilization") is not None:
            lines.append(f"Opus    (7d):  {op['utilization']:.0f}%")

        lines.append(f"Updated: {datetime.now().strftime('%H:%M:%S')}")
        return lines

    # ── Menu ──────────────────────────────────────────────────────────────────

    def _build_menu(self) -> pystray.Menu:
        lines = self._status_lines()

        items = [pystray.MenuItem(line, None, enabled=False) for line in lines]
        items += [
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Refresh now", self._on_refresh, default=True),
            pystray.MenuItem("Open Usage Settings…", self._open_browser),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._on_quit),
        ]
        return pystray.Menu(*items)

    # ── Poll loop ─────────────────────────────────────────────────────────────

    def _poll(self):
        while True:
            time.sleep(POLL_INTERVAL_SECONDS)
            self._refresh_once()

    def _refresh_once(self):
        if not self.token:
            self.token = load_token()

        if not self.token:
            with self._lock:
                self.error_msg = "Token not found — see README"
                self.last_data = None
            self._update_icon()
            return

        try:
            data = fetch_usage(self.token)
            with self._lock:
                self.last_data = data
                self.error_msg = None
        except ApiError as e:
            with self._lock:
                self.error_msg = e.message
                self.last_data = None
            self._update_icon()
            return

        self._update_icon()

        # Notifications
        if data and NOTIFY_ON_WARN and self.icon:
            fh = data.get("five_hour")
            sd = data.get("seven_day")
            if fh:
                pct = fh.get("utilization", 0)
                if pct >= WARN_THRESHOLD:
                    maybe_notify(self.icon, "Session (5h)", pct)
            if sd:
                pct = sd.get("utilization", 0)
                if pct >= WARN_THRESHOLD:
                    maybe_notify(self.icon, "Weekly (7d)", pct)

    def _update_icon(self):
        if not self.icon:
            return
        with self._lock:
            data = self.last_data

        s_pct = w_pct = None
        if data:
            if data.get("five_hour"):
                s_pct = data["five_hour"].get("utilization")
            if data.get("seven_day"):
                w_pct = data["seven_day"].get("utilization")

        self.icon.icon = make_icon(s_pct, w_pct)
        self.icon.menu = self._build_menu()

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_refresh(self, icon, item):
        threading.Thread(target=self._refresh_once, daemon=True).start()

    def _open_browser(self, icon, item):
        import webbrowser
        webbrowser.open("https://claude.ai/settings/usage")

    def _on_quit(self, icon, item):
        icon.stop()

    # ── Entry point ───────────────────────────────────────────────────────────

    def run(self):
        placeholder = make_icon(None, None)

        self.icon = pystray.Icon(
            name="claude_usage",
            icon=placeholder,
            title="Claude Code Usage",
            menu=self._build_menu(),
        )

        # Initial fetch before showing icon
        threading.Thread(target=self._refresh_once, daemon=True).start()
        # Background poll
        threading.Thread(target=self._poll, daemon=True).start()

        self.icon.run()


# ── Entry ─────────────────────────────────────────────────────────────────────

def _single_instance_check():
    """Return a mutex handle; exit if another instance is already running."""
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "ClaudeUsageTrayMutex")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        sys.exit(0)
    return mutex  # keep reference alive for process lifetime


if __name__ == "__main__":
    # Hide console window when run as .pyw or packaged .exe
    if sys.platform == "win32":
        ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
        _mutex = _single_instance_check()

    app = UsageApp()
    app.run()
