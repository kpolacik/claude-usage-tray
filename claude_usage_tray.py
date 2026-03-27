"""
Claude Code Usage - Windows System Tray App
Shows session (5h) and weekly (7d) usage in the notification area.
"""

import json
import math
import os
import sys
import threading
import time
import tkinter as tk
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

import requests
import pystray
from PIL import Image, ImageDraw, ImageFont
import ctypes

# ── Config ────────────────────────────────────────────────────────────────────
POLL_INTERVAL_SECONDS = 300         # how often to refresh usage data
WARN_THRESHOLD = 75                 # % at which tray icon turns yellow
CRIT_THRESHOLD = 90                 # % at which tray icon turns red
NOTIFY_ON_WARN = True               # Windows toast when crossing warn threshold
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"

# ── Popup style constants ─────────────────────────────────────────────────────
POPUP_W, POPUP_H = 340, 240
BG_COLOR       = "#1e1f22"
TEXT_PRIMARY    = "#ffffff"
TEXT_SECONDARY  = "#a0a0a0"
DIVIDER_COLOR  = "#2e2f33"
TRACK_COLOR    = "#3a3b3e"
GREEN          = "#4caf7d"
AMBER          = "#e6a817"
RED            = "#e05252"
BAR_ANIM_MS    = 400       # total animation duration
BAR_ANIM_STEP  = 16        # ms per frame


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


def _bar_fill_color(pct: float) -> str:
    if pct >= CRIT_THRESHOLD:
        return RED
    if pct >= WARN_THRESHOLD:
        return AMBER
    return GREEN


# ── Icon drawing ──────────────────────────────────────────────────────────────

def make_icon(session_pct: float | None, weekly_pct: float | None) -> Image.Image:
    """
    Render a 64×64 tray icon with two stacked horizontal bars.
    Black & white, minimalist design using Segoe UI Light.
    """
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=6, fill=(20, 20, 20))

    try:
        font_label = ImageFont.truetype("segoeuil.ttf", 11)
        font_pct   = ImageFont.truetype("segoeuil.ttf", 9)
    except Exception:
        font_label = ImageFont.load_default()
        font_pct   = font_label

    white = (255, 255, 255)
    grey_track = (50, 50, 50)
    grey_text  = (160, 160, 160)

    def draw_bar(y_top: int, pct: float | None, label: str):
        bar_h = 6
        bar_w = size - 14
        x0 = 7

        lbl = f"{label} {int(pct or 0)}%"
        d.text((x0, y_top - 11), lbl, font=font_pct, fill=grey_text)

        d.rounded_rectangle([x0, y_top, x0 + bar_w, y_top + bar_h],
                             radius=3, fill=grey_track)
        if pct is not None:
            fill_w = max(2, int(bar_w * min(pct, 100) / 100))
            d.rounded_rectangle([x0, y_top, x0 + fill_w, y_top + bar_h],
                                 radius=3, fill=white)

    d.text((size // 2, 9), "CC", font=font_label, fill=white, anchor="mm")

    draw_bar(28, session_pct, "5h")
    draw_bar(50, weekly_pct,  "7d")

    return img


# ── Windows toast notification ────────────────────────────────────────────────

_notified: set = set()

def maybe_notify(icon: pystray.Icon, label: str, pct: float):
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


# ── Popup window ─────────────────────────────────────────────────────────────

class UsagePopup:
    """Borderless floating panel that shows usage details."""

    def __init__(self, app: "UsageApp"):
        self.app = app
        self.win: tk.Toplevel | None = None
        self._anim_bars: list[dict] = []

    @property
    def is_open(self) -> bool:
        return self.win is not None and self.win.winfo_exists()

    def toggle(self):
        if self.is_open:
            self.close()
        else:
            self.open()

    def close(self):
        if self.win:
            try:
                self.win.destroy()
            except Exception:
                pass
            self.win = None

    # ── build ─────────────────────────────────────────────────────────────────

    def open(self):
        if self.is_open:
            self.close()

        root = self.app._tk_root
        win = tk.Toplevel(root)
        self.win = win

        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=BG_COLOR)
        win.resizable(False, False)

        # Position near the tray (bottom-right, above taskbar)
        screen_w = win.winfo_screenwidth()
        screen_h = win.winfo_screenheight()
        x = screen_w - POPUP_W - 12
        y = screen_h - POPUP_H - 60
        win.geometry(f"{POPUP_W}x{POPUP_H}+{x}+{y}")

        # Rounded corners on Windows 11
        try:
            hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 33, ctypes.byref(ctypes.c_int(2)), 4
            )
        except Exception:
            pass

        # Close on click-outside or Escape
        win.bind("<FocusOut>", lambda e: self._on_focus_out(e))
        win.bind("<Escape>", lambda e: self.close())

        self._build_content(win)

        win.after(10, lambda: win.focus_force())

    def _on_focus_out(self, event):
        if self.win and event.widget == self.win:
            self.win.after(50, self._check_focus)

    def _check_focus(self):
        if not self.is_open:
            return
        try:
            focused = self.win.focus_get()
            if focused is None or not str(focused).startswith(str(self.win)):
                self.close()
        except Exception:
            self.close()

    # ── content ───────────────────────────────────────────────────────────────

    def _build_content(self, win: tk.Toplevel):
        pad = 20
        inner_w = POPUP_W - pad * 2

        container = tk.Frame(win, bg=BG_COLOR)
        container.pack(fill="both", expand=True, padx=pad, pady=(pad, pad))

        # ── Header row ────────────────────────────────────────────────────────
        header = tk.Frame(container, bg=BG_COLOR)
        header.pack(fill="x")

        tk.Label(
            header, text="CLAUDE CODE USAGE", bg=BG_COLOR,
            fg=TEXT_SECONDARY, font=("Segoe UI", 9), anchor="w"
        ).pack(side="left")

        btn_open = tk.Label(
            header, text="\u2197", bg=BG_COLOR, fg=TEXT_SECONDARY,
            font=("Segoe UI", 12), cursor="hand2"
        )
        btn_open.pack(side="right", padx=(4, 0))
        btn_open.bind("<Button-1>", lambda e: webbrowser.open("https://claude.ai/settings/usage"))
        btn_open.bind("<Enter>", lambda e: btn_open.config(fg=TEXT_PRIMARY))
        btn_open.bind("<Leave>", lambda e: btn_open.config(fg=TEXT_SECONDARY))

        btn_refresh = tk.Label(
            header, text="\u21bb", bg=BG_COLOR, fg=TEXT_SECONDARY,
            font=("Segoe UI", 12), cursor="hand2"
        )
        btn_refresh.pack(side="right")
        btn_refresh.bind("<Button-1>", lambda e: self._do_refresh())
        btn_refresh.bind("<Enter>", lambda e: btn_refresh.config(fg=TEXT_PRIMARY))
        btn_refresh.bind("<Leave>", lambda e: btn_refresh.config(fg=TEXT_SECONDARY))

        # ── Divider ───────────────────────────────────────────────────────────
        tk.Frame(container, bg=DIVIDER_COLOR, height=1).pack(fill="x", pady=(10, 12))

        # ── Data ──────────────────────────────────────────────────────────────
        with self.app._lock:
            data = self.app.last_data
            err  = self.app.error_msg

        self._anim_bars = []

        if err:
            tk.Label(
                container, text=err, bg=BG_COLOR, fg=RED,
                font=("Segoe UI", 10), anchor="w"
            ).pack(fill="x")
        elif data is None:
            tk.Label(
                container, text="Loading\u2026", bg=BG_COLOR, fg=TEXT_SECONDARY,
                font=("Segoe UI", 10), anchor="w"
            ).pack(fill="x")
        else:
            fh = data.get("five_hour")
            if fh:
                pct = fh.get("utilization", 0)
                reset = parse_reset(fh.get("resets_at"))
                bar = self._make_bar_section(
                    container, "5h Session", pct,
                    f"Resets in  {fmt_countdown(reset)}",
                    inner_w, reset_dt=reset
                )
                self._anim_bars.append({"canvas": bar, "target": pct, "current": 0, "width": inner_w})

            fh_frame_spacer = tk.Frame(container, bg=BG_COLOR, height=8)
            fh_frame_spacer.pack()

            sd = data.get("seven_day")
            if sd:
                pct = sd.get("utilization", 0)
                reset = parse_reset(sd.get("resets_at"))
                bar = self._make_bar_section(
                    container, "7-day Weekly", pct,
                    f"Resets  {fmt_reset_date(reset)}",
                    inner_w
                )
                self._anim_bars.append({"canvas": bar, "target": pct, "current": 0, "width": inner_w})

        # ── Bottom divider + footer ───────────────────────────────────────────
        tk.Frame(container, bg=DIVIDER_COLOR, height=1).pack(fill="x", pady=(12, 8))

        self._footer_label = tk.Label(
            container,
            text=f"Updated {datetime.now().strftime('%H:%M:%S')}",
            bg=BG_COLOR, fg=TEXT_SECONDARY, font=("Segoe UI", 9), anchor="w"
        )
        self._footer_label.pack(fill="x")

        # ── Start bar animation ───────────────────────────────────────────────
        if self._anim_bars:
            self._anim_start_time = time.time()
            self._animate_bars()

    # ── progress bar section ──────────────────────────────────────────────────

    def _make_bar_section(self, parent, title: str, pct: float,
                          caption: str, width: int,
                          reset_dt: datetime | None = None) -> tk.Canvas:

        # Section title
        tk.Label(
            parent, text=title, bg=BG_COLOR, fg=TEXT_PRIMARY,
            font=("Segoe UI", 10), anchor="w"
        ).pack(fill="x")

        # Bar row: canvas + percentage label
        bar_row = tk.Frame(parent, bg=BG_COLOR)
        bar_row.pack(fill="x", pady=(4, 0))

        bar_h = 8
        canvas = tk.Canvas(
            bar_row, width=width - 40, height=bar_h,
            bg=BG_COLOR, highlightthickness=0
        )
        canvas.pack(side="left")

        # Draw track
        self._draw_rounded_rect(canvas, 0, 0, width - 40, bar_h, 4, TRACK_COLOR)
        # Fill tag will be animated
        canvas._fill_tag = "bar_fill"
        canvas._bar_h = bar_h
        canvas._bar_max_w = width - 40
        canvas._fill_color = _bar_fill_color(pct)

        pct_label = tk.Label(
            bar_row, text=f"{int(pct)}%", bg=BG_COLOR, fg=TEXT_PRIMARY,
            font=("Segoe UI", 10), anchor="e", width=4
        )
        pct_label.pack(side="right")
        canvas._pct_label = pct_label

        # Caption
        cap = tk.Label(
            parent, text=caption, bg=BG_COLOR, fg=TEXT_SECONDARY,
            font=("Segoe UI", 9), anchor="w"
        )
        cap.pack(fill="x", pady=(2, 0))

        # Live countdown update for session bar
        if reset_dt is not None:
            self._schedule_countdown(cap, reset_dt)

        return canvas

    def _schedule_countdown(self, label: tk.Label, reset_dt: datetime):
        def update():
            if not self.is_open:
                return
            try:
                label.config(text=f"Resets in  {fmt_countdown(reset_dt)}")
                label.after(1000, update)
            except Exception:
                pass
        if self.is_open:
            label.after(1000, update)

    # ── rounded rect helper ───────────────────────────────────────────────────

    @staticmethod
    def _draw_rounded_rect(canvas: tk.Canvas, x0, y0, x1, y1, r, color, tag=""):
        points = [
            x0 + r, y0,  x1 - r, y0,
            x1, y0,  x1, y0 + r,
            x1, y1 - r,  x1, y1,
            x1 - r, y1,  x0 + r, y1,
            x0, y1,  x0, y1 - r,
            x0, y0 + r,  x0, y0,
        ]
        canvas.create_polygon(points, fill=color, smooth=True, tags=tag)

    # ── bar animation ─────────────────────────────────────────────────────────

    def _animate_bars(self):
        if not self.is_open:
            return

        elapsed = (time.time() - self._anim_start_time) * 1000
        t = min(elapsed / BAR_ANIM_MS, 1.0)
        # ease-out cubic
        eased = 1 - (1 - t) ** 3

        for bar in self._anim_bars:
            canvas = bar["canvas"]
            target = bar["target"]
            max_w = canvas._bar_max_w
            bar_h = canvas._bar_h

            current_pct = target * eased
            fill_w = max(2, int(max_w * min(current_pct, 100) / 100))

            canvas.delete("bar_fill")
            self._draw_rounded_rect(
                canvas, 0, 0, fill_w, bar_h, 4,
                canvas._fill_color, tag="bar_fill"
            )
            canvas._pct_label.config(text=f"{int(current_pct)}%")

        if t < 1.0:
            self.win.after(BAR_ANIM_STEP, self._animate_bars)

    # ── refresh from popup ────────────────────────────────────────────────────

    def _do_refresh(self):
        self.close()
        threading.Thread(target=self.app._refresh_once, daemon=True).start()


# ── Main app state ────────────────────────────────────────────────────────────

class UsageApp:
    def __init__(self):
        self.token: str | None = None
        self.last_data: dict | None = None
        self.error_msg: str | None = None
        self._lock = threading.Lock()
        self.icon: pystray.Icon | None = None
        self._tk_root: tk.Tk | None = None
        self._popup: UsagePopup | None = None

    # ── Menu (right-click only — minimal) ─────────────────────────────────────

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem("Show Usage", self._on_left_click, default=True, visible=False),
            pystray.MenuItem("Refresh now", self._on_refresh),
            pystray.MenuItem("Open Usage Settings\u2026", self._open_browser),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._on_quit),
        )

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
                self.error_msg = "Token not found \u2014 see README"
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

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_left_click(self, icon, item):
        if self._tk_root:
            self._tk_root.after(0, self._popup.toggle)

    def _on_refresh(self, icon, item):
        threading.Thread(target=self._refresh_once, daemon=True).start()

    def _open_browser(self, icon, item):
        webbrowser.open("https://claude.ai/settings/usage")

    def _on_quit(self, icon, item):
        if self._tk_root:
            self._tk_root.after(0, self._tk_root.destroy)
        icon.stop()

    # ── Entry point ───────────────────────────────────────────────────────────

    def run(self):
        # Hidden tkinter root for popup windows
        self._tk_root = tk.Tk()
        self._tk_root.withdraw()

        self._popup = UsagePopup(self)

        placeholder = make_icon(None, None)

        self.icon = pystray.Icon(
            name="claude_usage",
            icon=placeholder,
            title="Claude Code Usage",
            menu=self._build_menu(),
        )

        # Initial fetch
        threading.Thread(target=self._refresh_once, daemon=True).start()
        # Background poll
        threading.Thread(target=self._poll, daemon=True).start()

        # Run pystray in a thread so tkinter mainloop can run on the main thread
        threading.Thread(target=self.icon.run, daemon=True).start()

        self._tk_root.mainloop()


# ── Entry ─────────────────────────────────────────────────────────────────────

def _single_instance_check():
    """Return a mutex handle; exit if another instance is already running."""
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "ClaudeUsageTrayMutex")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        sys.exit(0)
    return mutex


if __name__ == "__main__":
    if sys.platform == "win32":
        ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
        _mutex = _single_instance_check()

    app = UsageApp()
    app.run()
