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
import tkinter.font as tk_font
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
ICON_PATH = Path(__file__).parent / "icon.png"

# ── Popup style constants ─────────────────────────────────────────────────────
POPUP_W, POPUP_H = 440, 290
CORNER_RADIUS  = 16          # popup window corner radius
FONT_FAMILY    = "Manrope"   # primary font, falls back to Segoe UI
BAR_ANIM_MS    = 400         # total animation duration
BAR_ANIM_STEP  = 16          # ms per frame
PREFS_PATH     = Path.home() / ".claude" / "tray_prefs.json"

THEMES = {
    "dark": {
        "bg":        "#1e1f22",
        "on_surface": "#a8a49e",
        "micro":     "#919191",
        "hover":     "#2a2b2f",
    },
    "light": {
        "bg":        "#f5f0e8",
        "on_surface": "#4a453e",
        "micro":     "#8a847c",
        "hover":     "#ebe5dc",
    },
}

def _load_prefs() -> dict:
    try:
        return json.loads(PREFS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _save_prefs(prefs: dict):
    try:
        PREFS_PATH.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
    except Exception:
        pass

_current_theme = _load_prefs().get("theme", "dark")

def theme() -> dict:
    return THEMES[_current_theme]


# ── Token loading ─────────────────────────────────────────────────────────────

def load_token() -> str | None:
    """Try to load the OAuth access token from ~/.claude/.credentials.json"""
    try:
        data = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
        token = (
            data.get("claudeAiOauth", {}).get("accessToken")
            or data.get("oauthToken")
            or data.get("accessToken")
        )
        if token:
            return token
    except Exception:
        pass

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
        self._after_ids: list[str] = []

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
            self._cancel_afters()
            try:
                self.win.destroy()
            except Exception:
                pass
            self.win = None

    def _cancel_afters(self):
        for aid in self._after_ids:
            try:
                self.win.after_cancel(aid)
            except Exception:
                pass
        self._after_ids.clear()

    # ── build ─────────────────────────────────────────────────────────────────

    def open(self):
        if self.is_open:
            self.close()

        root = self.app._tk_root
        win = tk.Toplevel(root)
        self.win = win

        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=theme()["bg"])
        win.resizable(False, False)

        # Position near the tray (bottom-right, above taskbar)
        screen_w = win.winfo_screenwidth()
        screen_h = win.winfo_screenheight()
        x = screen_w - POPUP_W - 12
        y = screen_h - POPUP_H - 60
        win.geometry(f"{POPUP_W}x{POPUP_H}+{x}+{y}")

        # Rounded corners via DWM on Windows 11
        try:
            win.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
            if not hwnd:
                hwnd = win.winfo_id()
            # DWMWCP_ROUND = 2
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 33, ctypes.byref(ctypes.c_int(2)), 4
            )
            # Remove the default thin border that appears with DWM rounding
            # by setting DWMWA_BORDER_COLOR (34) to DWMWA_COLOR_NONE (0xFFFFFFFE)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 34, ctypes.byref(ctypes.c_uint(0xFFFFFFFE)), 4
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
        t = theme()
        bg = t["bg"]
        fg = t["on_surface"]
        micro = t["micro"]
        hover = t["hover"]

        win.configure(bg=bg)

        pad = 16
        inner_w = POPUP_W - pad * 2

        # ── Header ────────────────────────────────────────────────────────────
        header = tk.Frame(win, bg=bg)
        header.pack(fill="x", padx=pad, pady=(12, 0))

        tk.Label(
            header, text="Claude code usage", bg=bg,
            fg=fg, font=(FONT_FAMILY, 11, "bold"), anchor="w"
        ).pack(side="left")

        btn_open = tk.Label(
            header, text="\u29c9", bg=bg, fg=fg,
            font=(FONT_FAMILY, 13), cursor="hand2"
        )
        btn_open.pack(side="right", padx=(4, 0))
        btn_open.bind("<Button-1>", lambda e: webbrowser.open("https://claude.ai/settings/usage"))
        btn_open.bind("<Enter>", lambda e: btn_open.config(bg=hover))
        btn_open.bind("<Leave>", lambda e: btn_open.config(bg=bg))

        btn_theme = tk.Label(
            header, text="\u263e" if _current_theme == "dark" else "\u2600",
            bg=bg, fg=fg, font=(FONT_FAMILY, 13), cursor="hand2"
        )
        btn_theme.pack(side="right", padx=(0, 4))
        btn_theme.bind("<Button-1>", lambda e: self._toggle_theme())
        btn_theme.bind("<Enter>", lambda e: btn_theme.config(bg=hover))
        btn_theme.bind("<Leave>", lambda e: btn_theme.config(bg=bg))

        btn_refresh = tk.Label(
            header, text="\u21bb", bg=bg, fg=fg,
            font=(FONT_FAMILY, 13), cursor="hand2"
        )
        btn_refresh.pack(side="right", padx=(0, 4))
        btn_refresh.bind("<Button-1>", lambda e: self._do_refresh())
        btn_refresh.bind("<Enter>", lambda e: btn_refresh.config(bg=hover))
        btn_refresh.bind("<Leave>", lambda e: btn_refresh.config(bg=bg))

        # ── Data ──────────────────────────────────────────────────────────────
        with self.app._lock:
            data = self.app.last_data
            err  = self.app.error_msg

        self._anim_bars = []

        main = tk.Frame(win, bg=bg)
        main.pack(fill="both", expand=True, padx=pad, pady=(10, 0))

        if err:
            tk.Label(
                main, text=err, bg=bg, fg=fg,
                font=(FONT_FAMILY, 10), anchor="w"
            ).pack(fill="x")
        elif data is None:
            tk.Label(
                main, text="Loading\u2026", bg=bg, fg=fg,
                font=(FONT_FAMILY, 10), anchor="w"
            ).pack(fill="x")
        else:
            fh = data.get("five_hour")
            if fh:
                pct = fh.get("utilization", 0)
                reset = parse_reset(fh.get("resets_at"))
                bar = self._make_bar_section(
                    main, "5h Session", pct,
                    f"Resets in {fmt_countdown(reset)}",
                    inner_w, bg, fg, reset_dt=reset
                )
                self._anim_bars.append({"canvas": bar, "target": pct, "width": inner_w})

            tk.Frame(main, bg=bg, height=16).pack()

            sd = data.get("seven_day")
            if sd:
                pct = sd.get("utilization", 0)
                reset = parse_reset(sd.get("resets_at"))
                bar = self._make_bar_section(
                    main, "7-day Weekly", pct,
                    f"Resets {fmt_reset_date(reset)}",
                    inner_w, bg, fg
                )
                self._anim_bars.append({"canvas": bar, "target": pct, "width": inner_w})

        # ── Footer ────────────────────────────────────────────────────────────
        footer = tk.Frame(win, bg=bg)
        footer.pack(fill="x", padx=pad, pady=(8, 10))

        self._footer_label = tk.Label(
            footer, text="", bg=bg, fg=micro,
            font=(FONT_FAMILY, 9), anchor="w"
        )
        self._footer_label.pack(side="left")
        self._update_relative_time()

        links_frame = tk.Frame(footer, bg=bg)
        links_frame.pack(side="right")

        for text, url in [("DOCS", "https://docs.anthropic.com"), ("SUPPORT", "https://support.anthropic.com")]:
            lbl = tk.Label(
                links_frame, text=text, bg=bg, fg=micro,
                font=(FONT_FAMILY, 9), cursor="hand2"
            )
            lbl.pack(side="left", padx=(8, 0))
            lbl.bind("<Button-1>", lambda e, u=url: webbrowser.open(u))
            lbl.bind("<Enter>", lambda e, l=lbl: l.config(fg=fg))
            lbl.bind("<Leave>", lambda e, l=lbl: l.config(fg=micro))

        # ── Start bar animation ───────────────────────────────────────────────
        if self._anim_bars:
            self._anim_start_time = time.time()
            self._anim_fill_color = fg
            self._animate_bars()

    # ── live "last updated" ticker ──────────────────────────────────────────

    def _update_relative_time(self):
        if not self.is_open:
            return
        with self.app._lock:
            ts = self.app.last_updated
        if ts:
            delta = (datetime.now() - ts).total_seconds()
            if delta < 5:
                rel = "just now"
            elif delta < 60:
                rel = f"{int(delta)}s ago"
            elif delta < 3600:
                m = int(delta // 60)
                rel = f"{m} minute{'s' if m != 1 else ''} ago"
            else:
                h = int(delta // 3600)
                rel = f"{h} hour{'s' if h != 1 else ''} ago"
            self._footer_label.config(text=f"Updated {rel}")
        else:
            self._footer_label.config(text="Updated —")
        aid = self._footer_label.after(1000, self._update_relative_time)
        self._after_ids.append(aid)

    # ── progress bar section ──────────────────────────────────────────────────

    def _make_bar_section(self, parent, title: str, pct: float,
                          caption: str, width: int, bg: str, fg: str,
                          reset_dt: datetime | None = None) -> tk.Canvas:

        # Title row: label left, percentage right
        title_row = tk.Frame(parent, bg=bg)
        title_row.pack(fill="x")

        tk.Label(
            title_row, text=title, bg=bg, fg=fg,
            font=(FONT_FAMILY, 11, "normal"), anchor="w"
        ).pack(side="left")

        pct_label = tk.Label(
            title_row, text=f"{int(pct)}%", bg=bg, fg=fg,
            font=(FONT_FAMILY, 11, "bold"), anchor="e"
        )
        pct_label.pack(side="right")

        # Outlined progress bar (18px tall, 1px border, 3px inner padding)
        bar_container_h = 18
        canvas = tk.Canvas(
            parent, width=width, height=bar_container_h,
            bg=bg, highlightthickness=0
        )
        canvas.pack(fill="x", pady=(4, 0))

        # Outer border (draw as filled rect then inner bg rect to fake border)
        self._draw_rounded_rect(canvas, 0, 0, width, bar_container_h, 6, fg)
        self._draw_rounded_rect(canvas, 1, 1, width - 1, bar_container_h - 1, 5, bg)

        canvas._bar_h = bar_container_h
        canvas._bar_max_w = width
        canvas._pct_label = pct_label

        # Caption
        cap = tk.Label(
            parent, text=caption, bg=bg, fg=fg,
            font=(FONT_FAMILY, 9), anchor="w"
        )
        cap.pack(fill="x", pady=(3, 0))

        # Live countdown for session bar
        if reset_dt is not None:
            self._schedule_countdown(cap, reset_dt, caption)

        return canvas

    def _schedule_countdown(self, label: tk.Label, reset_dt: datetime, caption_prefix: str):
        def update():
            if not self.is_open:
                return
            try:
                label.config(text=f"Resets in {fmt_countdown(reset_dt)}")
                aid = label.after(1000, update)
                self._after_ids.append(aid)
            except Exception:
                pass
        if self.is_open:
            aid = label.after(1000, update)
            self._after_ids.append(aid)

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
        eased = 1 - (1 - t) ** 3   # ease-out cubic

        inset = 3   # px from border to fill edge
        for bar in self._anim_bars:
            canvas = bar["canvas"]
            target = bar["target"]
            max_w = canvas._bar_max_w
            bar_h = canvas._bar_h

            current_pct = target * eased
            inner_w = max_w - inset * 2
            fill_w = max(0, int(inner_w * min(current_pct, 100) / 100))

            canvas.delete("bar_fill")
            if fill_w > 0:
                self._draw_rounded_rect(
                    canvas, inset, inset, inset + fill_w, bar_h - inset,
                    4, self._anim_fill_color, tag="bar_fill"
                )
            canvas._pct_label.config(text=f"{int(current_pct)}%")

        if t < 1.0:
            self.win.after(BAR_ANIM_STEP, self._animate_bars)

    # ── theme toggle ─────────────────────────────────────────────────────────

    def _toggle_theme(self):
        global _current_theme
        _current_theme = "light" if _current_theme == "dark" else "dark"
        _save_prefs({"theme": _current_theme})
        self._rebuild()

    # ── refresh from popup ────────────────────────────────────────────────────

    def _do_refresh(self):
        def refresh_and_update():
            self.app._refresh_once()
            if self.is_open and self.win:
                self.win.after(0, self._rebuild)

        threading.Thread(target=refresh_and_update, daemon=True).start()

    def _rebuild(self):
        """Rebuild popup content in-place after a refresh."""
        if not self.is_open:
            return
        self._cancel_afters()
        for w in self.win.winfo_children():
            w.destroy()
        self._build_content(self.win)
        self.win.focus_force()


# ── Main app state ────────────────────────────────────────────────────────────

class UsageApp:
    def __init__(self):
        self.token: str | None = None
        self.last_data: dict | None = None
        self.error_msg: str | None = None
        self.last_updated: datetime | None = None
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
                self.last_updated = datetime.now()
        except ApiError as e:
            with self._lock:
                self.error_msg = e.message
                self.last_data = None
            self._update_icon()
            return

        self._update_icon()

        # Notifications
        if data and NOTIFY_ON_WARN and self.icon:
            for bucket, label in [
                (data.get("five_hour"), "Session (5h)"),
                (data.get("seven_day"), "Weekly (7d)"),
            ]:
                if not bucket:
                    continue
                pct = bucket.get("utilization", 0)
                if pct >= WARN_THRESHOLD:
                    maybe_notify(self.icon, label, pct)
                elif pct < WARN_THRESHOLD:
                    # Usage dropped (e.g. after reset) — allow re-notification
                    _notified.discard((label, True, True))
                    _notified.discard((label, False, True))

    def _update_icon(self):
        pass  # icon is static; data is shown in the popup

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
        self._tk_root.tk.call("tk", "scaling", self._tk_root.winfo_fpixels("1i") / 72)
        self._tk_root.withdraw()

        # Detect available font
        global FONT_FAMILY
        available = set(tk_font.families(self._tk_root))
        if "Manrope" in available:
            FONT_FAMILY = "Manrope"
        elif "Segoe UI" in available:
            FONT_FAMILY = "Segoe UI"
        else:
            FONT_FAMILY = "TkDefaultFont"

        self._popup = UsagePopup(self)

        tray_icon = Image.open(ICON_PATH).resize((64, 64), Image.LANCZOS)

        self.icon = pystray.Icon(
            name="claude_usage",
            icon=tray_icon,
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
        ctypes.windll.user32.SetProcessDPIAware()
        ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
        _mutex = _single_instance_check()

    app = UsageApp()
    app.run()
