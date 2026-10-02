# Release Notes — AS400 RPA (EHLLAPI)

---

## Unreleased

### System Tray — Interactive (this session)

- **Tray icon now appears** in the notification area when running as a frozen `.exe` (`console=False`).  
  Previously `GetConsoleWindow()` returned NULL for no-console builds, so the icon was never registered.  
  Fix: a hidden Win32 window with its own message loop (`AS400RPATrayHost`) now owns the tray icon.

- **Left-click tray icon** — allocates a live log console on first click (`AllocConsole`), redirects `stdout`/`stderr` and adds a `StreamHandler` to the logger so output streams in real time. Subsequent clicks toggle the console window visible/hidden.

- **Right-click tray icon** — popup menu:
  - Show / Hide Console
  - Open Log File (opens `as400-rpa.log` in default viewer)
  - Exit

- **Startup state visible in tray tooltip** — `splash.update()` now also pushes state to the tray tip, so hovering the icon during startup shows the current phase (connecting DB / launching PC5250 / signing in to AS400).

---

## 4f6b5c7 — Add system tray icon, session disconnect alerts, and startup splash

- System tray icon (`black-box.ico`) added; console window hidden from taskbar.
- Session disconnect detected → `MessageBox` alert on a daemon thread; auto-dismissed when session recovers.
- Startup splash window (`cover_splash.bmp`) with animated loading text.
- Reconnect success → 3-second toast notification.

## 92f5bcf — Improve error handling, performance, and session recovery

- `activity.log` separate from main log — one clean line per record event for supervisor review.
- DB connection retried indefinitely on startup with 5-second timeout `MessageBox`.
- Screen-validation errors now mark record `FAILED` with reason rather than crashing the loop.
- Session timeout mid-run detected; triggers re-login without restarting the process.

## 34077dd — Fix login flow

- Dismiss lingering program messages before PBASMT login screen.
- Wait for Sign On screen before sending credentials.
- `.lnk` shortcut launch support (`PCOMM_EXE` can point to a `.lnk`).
- Thai-locale DB error `MessageBox` auto-closes after 5 s.

## edd1062 — Replace PCOMM_WORKSPACE with PCOMM_ARGS

- `PCOMM_ARGS` replaces `PCOMM_WORKSPACE`; value is split and appended to `pcsws.exe` command line.

## c3ff73d — Build tooling

- `build.bat` added; `release/` excluded from git.
- PyInstaller installed into `py32\`.

## 1255e76 — Hardcode CONFIRM_MODE

- `CONFIRM_MODE=True` by default — operator must confirm every submit via DB flag before final Enter.

## 7332f38 — Activity log + DB logging

- `logs/activity.log` (RotatingFileHandler 5 MB × 10).
- DB connection details logged at startup.

## b777d00 — Logging improvements

- Startup banner with config summary.
- Poll-cycle spam suppressed; per-record timing logged.

## c1675cc — Auto-launch PC5250

- If EHLLAPI session is not open on startup, `pcsws.exe` is launched automatically and the bot waits `PCOMM_START_WAIT` seconds.
