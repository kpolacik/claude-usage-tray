# Spec: Claude Code Usage Tray App — UI Redesign

## Overview

Redesign the tray app UI. Replace the plain pystray right-click context menu with:
- **Left-click** → opens a custom floating popup window (the main UI)
- **Right-click** → minimal native context menu with only: Refresh, Open Usage Page, Quit

---

## Left-click: Floating Popup Window

### Behaviour
- Appears near the system tray icon (bottom-right of screen), above the taskbar
- Closes when the user clicks anywhere outside the popup
- Closes when the user presses Escape
- Does NOT have a title bar or standard Windows chrome — completely borderless
- Implemented as a `tkinter.Toplevel` window with `overrideredirect(True)`
- Position: calculate from `GetCursorPos()` or fixed offset from bottom-right corner so it never goes off-screen

### Dimensions
- **340 × 240 px** (fixed size, non-resizable)

### Visual style
- Background color: `#1e1f22` (slightly darker than Claude desktop dark mode `#242424`)
- Text color: `#ffffff` (primary), `#a0a0a0` (secondary/labels)
- Font: **Segoe UI** throughout
  - Title: Segoe UI, 11px, weight normal, secondary color
  - Values: Segoe UI, 20px, bold, white
  - Labels/captions: Segoe UI, 9px, secondary color
- Corner rounding: **8px** radius (use a canvas-drawn rounded rectangle as background, or `_DWMWA_WINDOW_CORNER_PREFERENCE` via ctypes on Windows 11)
- Drop shadow: subtle — enable via `DwmExtendFrameIntoClientArea` or simply use `wm_attributes('-transparentcolor')` trick so Windows draws the shadow naturally
- No border

### Layout (top to bottom, with padding 20px on all sides)

```
┌─────────────────────────────────────────┐
│  CLAUDE CODE USAGE          ↻  [↗]     │  ← header row
│ ─────────────────────────────────────── │
│  5h Session                             │
│  ████████████░░░░░░░░  68%             │  ← progress bar
│  Resets in  2h 14m                      │  ← caption
│                                         │
│  7-day Weekly                           │
│  ████░░░░░░░░░░░░░░░░  23%             │  ← progress bar
│  Resets  Thu 3 Apr, 08:00              │  ← caption
│ ─────────────────────────────────────── │
│  Updated 14:32:05                       │  ← footer
└─────────────────────────────────────────┘
```

#### Header row
- Left: "CLAUDE CODE USAGE" — Segoe UI 9px, letter-spacing uppercase, secondary color
- Right: two icon buttons (Unicode or small canvas icons):
  - `↻` Refresh — clicking triggers an immediate API refresh
  - `↗` Open usage page — opens `https://claude.ai/settings/usage` in default browser
- Buttons: no background, white icon, subtle hover state (icon brightens or small highlight circle appears)

#### Progress bars
- Height: **8px**, full width minus 40px padding
- Track color: `#3a3b3e`
- Fill color: status-based:
  - < 75%: `#4caf7d` (green)
  - 75–89%: `#e6a817` (amber)
  - ≥ 90%: `#e05252` (red)
- Rounded caps on both ends (radius = 4px)
- **Animation**: on popup open, fill animates from 0 → actual % over **400ms** using an easing curve (ease-out). Use `after()` loop in tkinter, stepping ~16ms per frame.

#### Section labels
- Section title (e.g. "5h Session"): Segoe UI 10px, white
- Percentage: displayed as text to the right of the bar, e.g. "68%", Segoe UI 10px, white
- Caption below bar: Segoe UI 9px, secondary color (`#a0a0a0`)
  - Session: "Resets in Xh Ym" (countdown, updates live every second via `after(1000, ...)`)
  - Weekly: "Resets Thu 3 Apr, 08:00" (formatted local time)

#### Dividers
- Thin 1px horizontal line, color `#2e2f33`, full width

#### Footer
- "Updated HH:MM:SS" — Segoe UI 9px, secondary color, left-aligned

---

## Right-click: Native context menu

Keep the pystray default menu but strip it down to only:
1. Refresh now
2. Open usage page
3. ─── separator ───
4. Quit

No usage data in this menu — that all lives in the left-click popup.

---

## Implementation notes

- Use **tkinter** for the popup (already in Python stdlib, no extra install)
- The popup must appear above the taskbar. Use `wm_attributes('-topmost', True)`
- To make the window borderless AND keep the shadow on Windows 11, try:
  ```python
  hwnd = ctypes.windll.user32.GetParent(popup.winfo_id())
  # Set DWMWA_WINDOW_CORNER_PREFERENCE = DWMWCP_ROUND (3) — Windows 11 only
  ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(ctypes.c_int(2)), 4)
  ```
- Click-outside-to-close: bind `<FocusOut>` on the popup or use `grab_set()` + bind click on a full-screen transparent overlay
- Escape-to-close: `popup.bind('<Escape>', lambda e: popup.destroy())`
- Only one popup instance open at a time — if the user left-clicks while popup is open, close it instead of opening a second one

---

## Files to modify

- `claude_usage_tray.py` — all changes go here
- No new dependencies beyond what's already installed (`tkinter` is stdlib)

---

## Out of scope for this task

- Settings/config screen
- Changing the tray icon design
- macOS or Linux support
