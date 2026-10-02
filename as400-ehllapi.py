"""
AS400 EHLLAPI RPA — polls as400_queue and processes via direct 5250 buffer.
No OCR, no window focus, no mouse clicks.

Must run with 32-bit Python:
    py32\python.exe as400-ehllapi.py

Screen flow (same as as400-rpa.py, but EHLLAPI instead of pyautogui+Tesseract):
  1. MHT53201 — type VENDOR CODE → Enter
  2. MHT53202 — find D/O row by TAX INV.NO (MATLOT) → type "1" → Enter
  3. DHT53203 — type "A" (ACCEPT) → Enter → Enter
  4. DTN07122 — fill S/C=2, VENDOR=shortname
                if CONFIRM_MODE: store screen text → AWAITING_CONFIRM → poll → Enter×2 or F3
                else: Enter → Enter

EHLLAPI AID keys in SEND_KEY:
  @E = Enter   @3 = F3   @T = Tab   @F = Erase EOF   @B = Backtab
"""

import os, sys, struct, time, ctypes, logging, pyodbc
from ctypes import c_int, byref, create_string_buffer
from dotenv import load_dotenv

_base = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_base, ".env"), override=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("as400-ehllapi")

# ── Config ─────────────────────────────────────────────────────────────────────

DB_SERVER   = os.getenv("DB_SERVER",   "localhost")
DB_NAME     = os.getenv("DB_NAME",     "")
DB_USER     = os.getenv("DB_USER",     "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "5"))

AS400_SESSION         = os.getenv("AS400_SESSION_ID", "A")  # EHLLAPI short session id
DLL_DIR               = os.getenv("AS400_DLL_DIR",
                                   r"C:\Users\bptj825\Desktop\PBFD5YD1 A35BP.lnk")
CONFIRM_TIMEOUT_SEC   = int(os.getenv("CONFIRM_TIMEOUT_SEC", "120"))
DTN07122_CONFIRM_MODE = os.getenv("DTN07122_CONFIRM_MODE", "false").lower() == "true"
DTN07122_DRY_RUN      = os.getenv("DTN07122_DRY_RUN", "false").lower() == "true"

# EHLLAPI function numbers
CONNECT_PS, DISCONNECT_PS = 1, 2
SEND_KEY   = 3
COPY_PS    = 5
SET_CURSOR = 40

PS_ROWS, PS_COLS = 24, 80
PS_SIZE = PS_ROWS * PS_COLS

# ── EHLLAPI layer ──────────────────────────────────────────────────────────────

def _load_hllapi():
    if 8 * struct.calcsize("P") != 32:
        sys.exit("Must use 32-bit Python — ehlapi32.dll is 32-bit only")
    if hasattr(os, "add_dll_directory") and os.path.isdir(DLL_DIR):
        os.add_dll_directory(DLL_DIR)
    for name in ("ehlapi32", "pcshll32"):
        for path in (os.path.join(DLL_DIR, name), name):
            try:
                dll = ctypes.WinDLL(path)
                if hasattr(dll, "hllapi"):
                    log.info("EHLLAPI loaded: %s", path)
                    return dll.hllapi
            except OSError:
                pass
    sys.exit("Cannot load ehlapi32 / pcshll32 from " + DLL_DIR)


def _make_call(hllapi_fn):
    def call(func, data=b"", length=None):
        if length is None:
            length = len(data)
        buf = create_string_buffer(data if data else b"\x00", max(length, 1))
        f, ln, rc = c_int(func), c_int(length), c_int(0)
        hllapi_fn(byref(f), buf, byref(ln), byref(rc))
        return buf.raw[:ln.value], ln.value, rc.value
    return call


_call = None   # set in main()
_psid = None   # connected session id


def ehllapi_connect():
    global _call, _psid
    hllapi = _load_hllapi()
    _call  = _make_call(hllapi)
    tried  = [AS400_SESSION] + [c for c in "ABCDEFGH" if c != AS400_SESSION]
    for s in tried:
        _, _, rc = _call(CONNECT_PS, s.encode(), 1)
        if rc == 0:
            _psid = s
            log.info("EHLLAPI connected to session '%s'", s)
            return
        _call(DISCONNECT_PS, s.encode(), 1)
    sys.exit("Cannot connect to any EHLLAPI session — open 5250 terminal and sign in first")


def ehllapi_disconnect():
    if _psid:
        _call(DISCONNECT_PS, _psid.encode(), 1)
        log.info("EHLLAPI disconnected")


def read_ps() -> str:
    """Read full 24×80 presentation space as a string."""
    raw, _, rc = _call(COPY_PS, b"\x00" * PS_SIZE, PS_SIZE)
    if rc not in (0, 1):
        log.warning("COPY_PS rc=%d", rc)
    return raw.decode("latin-1", "replace")


def ps_row(screen: str, row: int) -> str:
    """Return row text (1-based row index)."""
    return screen[(row - 1) * PS_COLS : row * PS_COLS]


def set_cursor(row: int, col: int):
    """Move cursor to 1-based (row, col)."""
    pos = (row - 1) * PS_COLS + col
    _call(SET_CURSOR, b"", pos)


def _safe(value: str) -> str:
    """Strip @ from DB-sourced field values so they cannot inject EHLLAPI AID mnemonics."""
    return str(value).replace("@", "")


def send_key(text: str):
    """Send text + AID key mnemonics (@E=Enter, @3=F3, @T=Tab, @F=ErasEOF, @B=Backtab)."""
    enc = text.encode("latin-1", "replace")
    _, _, rc = _call(SEND_KEY, enc, len(enc))
    if rc not in (0, 1, 2):  # 0=ok 1=input inhibited (screen updating) 2=operator error
        log.warning("SEND_KEY rc=%d text=%r", rc, text)


def wait_for(needle: str, timeout: float = 15.0, interval: float = 0.5) -> bool:
    """Wait until needle appears anywhere in the PS."""
    end = time.time() + timeout
    while time.time() < end:
        if needle in read_ps():
            return True
        time.sleep(interval)
    return False


def find_field_pos(screen: str, label: str, offset: int = 1) -> int:
    """
    Find the start of an input field that immediately follows label.
    Returns 0-based PS position (= pos_of_label + len(label) + offset - 1).
    Returns -1 if label not found.
    """
    pos = screen.find(label)
    if pos < 0:
        return -1
    return pos + len(label) + offset - 1


def set_cursor_at_pos(ps_pos: int):
    """SET_CURSOR using a 0-based PS offset."""
    _call(SET_CURSOR, b"", ps_pos + 1)  # EHLLAPI is 1-based


# ── DB layer ───────────────────────────────────────────────────────────────────

_CONN_STR = (
    f"DRIVER={{ODBC Driver 17 for SQL Server}};"
    f"SERVER={DB_SERVER};DATABASE={DB_NAME};"
    f"UID={DB_USER};PWD={DB_PASSWORD}"
)


def _db():
    return pyodbc.connect(_CONN_STR, autocommit=False)


def fetch_pending():
    with _db() as conn:
        rows = conn.execute(
            "SELECT q.id, q.VENDOR_CODE, q.MATLOT, q.ITEM_NO, q.STOCK_QTY, "
            "       q.VENDOR_NAME, q.ITEM_TYPE, vs.shortname "
            "FROM as400_queue q "
            "LEFT JOIN vendor_shortname vs ON vs.vendor_code = q.VENDOR_CODE "
            "WHERE q.status = 'PENDING' ORDER BY q.created_at"
        ).fetchall()
    return [{"id": r[0], "VENDOR_CODE": r[1] or "", "MATLOT": r[2] or "",
             "ITEM_NO": r[3] or "", "STOCK_QTY": r[4] or 0,
             "VENDOR_NAME": r[5] or "", "ITEM_TYPE": r[6] or "",
             "shortname": r[7] or ""}
            for r in rows]


def mark_done(record_id: int, status: str, error: str = None):
    with _db() as conn:
        conn.execute(
            "UPDATE as400_queue SET status=?, error=?, updated_at=GETDATE() WHERE id=?",
            status, error, record_id
        )
        conn.commit()


def mark_awaiting_confirm(record_id: int, screen_text: str):
    with _db() as conn:
        conn.execute(
            "UPDATE as400_queue SET status='AWAITING_CONFIRM', screenshot=?, updated_at=GETDATE() WHERE id=?",
            screen_text, record_id
        )
        conn.commit()


def get_queue_status(record_id: int) -> str:
    with _db() as conn:
        row = conn.execute("SELECT status FROM as400_queue WHERE id=?", record_id).fetchone()
    return row[0] if row else "FAILED"


# ── Screen helpers ─────────────────────────────────────────────────────────────

def screen_text_display(screen: str) -> str:
    """Format 1920-char PS as 24 lines for DB/display."""
    return "\n".join(screen[r * PS_COLS:(r + 1) * PS_COLS] for r in range(PS_ROWS))


def screen_id(screen: str) -> str:
    """Program name is usually in the first 8 chars of the screen."""
    return screen[:8].strip()


# ── Screen: MHT53201 — input VENDOR CODE ──────────────────────────────────────
#
# Layout:
#   BUYER GROUP...:  3102
#   SECTION CODE :   71DM
#   VENDOR CODE...:  [_____________________]  ← Tab to reach this field
#   SHOW TYPE.....:  ...
#
# Tab order from top: BUYER GROUP → SECTION CODE → INCHARGER → VENDOR CODE
# Default: 4 tabs (VENDOR_CODE_TAB_COUNT in old rpa)

VENDOR_CODE_TABS = int(os.getenv("VENDOR_CODE_TAB_COUNT", "4"))


def screen_mht53201(vendor_code: str):
    if not wait_for("MHT53201", timeout=10):
        raise RuntimeError("Timeout waiting for MHT53201")
    log.info("[MHT53201] on screen — tab x%d → type vendor_code=%r", VENDOR_CODE_TABS, vendor_code)
    # Tab to the VENDOR CODE field, clear it, type vendor code, Enter
    tabs = "@T" * VENDOR_CODE_TABS
    send_key(f"{tabs}@F{_safe(vendor_code)}@E")
    time.sleep(1.5)


# ── Screen: MHT53202 — D/O list, select row by TAX INV.NO ────────────────────
#
# Each D/O row occupies 2 lines:
#   [OPT]  D/O NO.     TAX INV.NO.   DELY DATE   ...
#          (VENDOR subline — not an input row)
#
# OPT field is at col 2 of each D/O row.  Find the row whose TAX INV.NO matches
# MATLOT (invoiceNo), then place cursor at OPT col, type "1", Enter.

def screen_mht53202(tax_inv_no: str):
    if not wait_for("MHT53202", timeout=10):
        raise RuntimeError("Timeout waiting for MHT53202")
    screen = read_ps()
    log.info("[MHT53202] on screen — searching for TAX INV.NO=%r", tax_inv_no)

    # Find the 0-based PS position of the MATLOT string in the screen
    inv_pos = screen.find(tax_inv_no)
    if inv_pos < 0:
        # Try trimmed suffix (last part after /) in case of truncation
        suffix = tax_inv_no.split("/")[-1] if "/" in tax_inv_no else tax_inv_no[-6:]
        inv_pos = screen.find(suffix)
        if inv_pos < 0:
            raise ValueError(
                f"MHT53202: TAX INV.NO {tax_inv_no!r} not found in D/O list — "
                "D/O may be on a different page or not yet released"
            )
        log.warning("[MHT53202] exact match not found, using suffix %r at pos %d", suffix, inv_pos)

    # OPT field is at col 2 of the same row
    row_of_inv = inv_pos // PS_COLS          # 0-based row
    opt_pos = row_of_inv * PS_COLS + 1       # col 2 (0-based col = 1)
    log.info("[MHT53202] found at row %d — placing cursor at OPT col 2", row_of_inv + 1)

    set_cursor_at_pos(opt_pos)
    send_key("1@E")
    time.sleep(1.5)


# ── Screen: DHT53203 — accept D/O ─────────────────────────────────────────────
#
# Layout:
#   VENDOR CODE ...  TAX INV.NO. ...
#   ITEM NO. ...
#   D/O JUDGEMENT [_]   (A = ACCEPT)   ← cursor starts here on screen entry
#
# Just verify screen ID then type "A" → Enter → Enter.

def screen_dht53203(record: dict):
    if not wait_for("DHT53203", timeout=10):
        raise RuntimeError("Timeout waiting for DHT53203")
    screen = read_ps()
    log.info("[DHT53203] on screen")

    # Quick safety: check vendor code and item are on the screen
    vc = record.get("VENDOR_CODE", "")
    item = record.get("ITEM_NO", "")
    if vc and vc not in screen:
        raise ValueError(f"DHT53203: VENDOR_CODE {vc!r} not visible on screen — wrong D/O selected?")
    if item and item not in screen:
        log.warning("[DHT53203] ITEM_NO %r not visible (may be truncated) — proceeding", item)

    # Cursor is already at D/O JUDGEMENT field on screen entry
    send_key("A@E")
    time.sleep(0.8)
    send_key("@E")
    log.info("[DHT53203] accepted (A → Enter → Enter)")
    time.sleep(1.5)


# ── Screen: DTN07122 — fill fields and confirm ────────────────────────────────
#
# Target fields on this screen:
#   W/H        → "I"     (warehouse code)
#   S/C        → "2"     (storage class)
#   MATL LOT   → invoiceNo (already filled by AS400 from D/O; verify only)
#   VENDOR     → shortname (short vendor name)
#
# Field finding strategy:
#   1. Scan screen for "W/H " or " W/H" label → input col is immediately after
#   2. Same for "S/C ", "VENDOR"
#   3. Use SET_CURSOR + @F (Erase EOF) + type value
#
# If CONFIRM_MODE: read screen text → AWAITING_CONFIRM → poll → Enter×2 or F3
# If DRY_RUN:      stop before Enter

def _find_and_fill(screen: str, label: str, value: str, label_offset: int = 0):
    """
    Find label in screen, set cursor at label_pos + len(label) + label_offset,
    erase EOF, type value.  Returns True if label found and filled.
    """
    pos = screen.find(label)
    if pos < 0:
        return False
    field_pos = pos + len(label) + label_offset
    set_cursor_at_pos(field_pos)
    send_key("@F" + _safe(value))   # @F = Erase EOF, then type value
    time.sleep(0.2)
    return True


def screen_dtn07122(record: dict):
    if not wait_for("DTN07122", timeout=15):
        raise RuntimeError("Timeout waiting for DTN07122")
    screen = read_ps()
    log.info("[DTN07122] on screen")

    shortname = record.get("shortname", "").strip()

    # Fill W/H = I (warehouse)
    # ponytail: screen typically has " W/H" with the input immediately after
    if not _find_and_fill(screen, "W/H", "I"):
        log.warning("[DTN07122] W/H label not found — skipping")

    time.sleep(0.3)
    screen = read_ps()   # refresh after fill

    # Fill S/C = 2 (storage class)
    if not _find_and_fill(screen, "S/C", "2"):
        log.warning("[DTN07122] S/C label not found — skipping")

    time.sleep(0.3)
    screen = read_ps()

    # Fill VENDOR shortname
    if shortname:
        # "VENDOR" label is usually followed by spaces then the input field
        if not _find_and_fill(screen, "VENDOR", shortname):
            log.warning("[DTN07122] VENDOR label not found — skipping shortname fill")
    else:
        log.warning("[DTN07122] no shortname for vendor %r — leaving VENDOR field as-is",
                    record.get("VENDOR_CODE"))

    time.sleep(0.3)
    log.info("[DTN07122] filled: W/H=I S/C=2 VENDOR=%r", shortname)

    # Re-read screen for confirm display
    screen = read_ps()
    display_text = screen_text_display(screen)

    if DTN07122_CONFIRM_MODE:
        confirm_timeout = CONFIRM_TIMEOUT_SEC
        log.info("[DTN07122] CONFIRM_MODE — storing screen text, waiting for operator (%ds)", confirm_timeout)
        mark_awaiting_confirm(record["id"], display_text)

        deadline = time.time() + confirm_timeout
        while time.time() < deadline:
            status = get_queue_status(record["id"])
            if status == "CONFIRMED":
                send_key("@E")
                time.sleep(1.0)
                send_key("@E")
                log.info("[DTN07122] confirmed by operator (Enter x2)")
                time.sleep(1.5)
                mark_done(record["id"], "SENT")
                return
            if status == "FAILED":
                send_key("@3")  # F3 = back
                log.info("[DTN07122] rejected by operator (F3)")
                time.sleep(1.0)
                return
            time.sleep(3)

        mark_done(record["id"], "FAILED",
                  f"Confirm timeout after {confirm_timeout}s — no operator response")
        send_key("@3")
        log.warning("[DTN07122] confirm timeout — F3 to exit")
        return

    if DTN07122_DRY_RUN:
        log.info("[DTN07122] DRY_RUN — skip Enter (screen not submitted)")
        log.info("[DTN07122] screen:\n%s", display_text)
        return

    send_key("@E")
    time.sleep(1.0)
    send_key("@E")
    log.info("[DTN07122] confirmed (Enter x2)")
    time.sleep(1.5)


# ── Main flow ──────────────────────────────────────────────────────────────────

def process_record(record: dict):
    """Run all 4 screen steps for one queue record."""
    log.info("Processing id=%d vendor=%s item=%s matlot=%s qty=%s",
             record["id"], record["VENDOR_CODE"], record["ITEM_NO"],
             record["MATLOT"], record["STOCK_QTY"])
    try:
        screen_mht53201(record["VENDOR_CODE"])
        screen_mht53202(record["MATLOT"])
        screen_dht53203(record)
        screen_dtn07122(record)
        if not DTN07122_CONFIRM_MODE:
            mark_done(record["id"], "SENT")
            log.info("SENT: id=%d item=%s", record["id"], record["ITEM_NO"])
    except Exception as exc:
        log.error("FAILED: id=%d — %s", record["id"], exc)
        mark_done(record["id"], "FAILED", str(exc))
        # Navigate back to MHT53201 for next record
        log.info("Pressing F3 to return to base screen...")
        for _ in range(5):
            send_key("@3")
            time.sleep(0.5)
            if "MHT53201" in read_ps():
                break


def poll():
    pending = fetch_pending()
    if not pending:
        return
    log.info("%d pending record(s)", len(pending))
    for record in pending:
        process_record(record)


def main():
    ehllapi_connect()
    try:
        log.info("Polling every %ds — Ctrl+C to stop", POLL_INTERVAL)
        while True:
            poll()
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        log.info("Stopped by user")
    finally:
        ehllapi_disconnect()


if __name__ == "__main__":
    main()
