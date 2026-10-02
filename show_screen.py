"""
Real-terminal dry-run viewer.
Reads the live 5250 screen buffer via EHLLAPI and mirrors it to CMD
(green-on-black ANSI), stepping through the RPA flow for each PENDING
record — stops before ACCEPT on DHT53203 (no commits).

Run: py32\python.exe show_screen.py
     (needs ehlapi32.dll, 32-bit Python, open 5250 session at MHT53201)
"""
import os, sys, time, ctypes
from dotenv import load_dotenv
load_dotenv()

# ── ANSI / console setup ──────────────────────────────────────────────────────
def enable_ansi():
    """Enable ANSI escape processing on Windows CMD."""
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)          # STD_OUTPUT_HANDLE
        mode = ctypes.c_ulong()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass

enable_ansi()

GREEN      = "\033[32m"
BGREEN     = "\033[92m"
WHITE      = "\033[97m"
YELLOW     = "\033[93m"
CYAN       = "\033[96m"
RED        = "\033[91m"
DIM        = "\033[2m"
RESET      = "\033[0m"
CLEAR      = "\033[2J\033[H"
REVERSE    = "\033[7m"

# ── DB ────────────────────────────────────────────────────────────────────────
import pyodbc
_SQL_DRV = next((d for d in sorted(pyodbc.drivers(), reverse=True) if "SQL Server" in d), "ODBC Driver 17 for SQL Server")

_CS = (f"DRIVER={{{_SQL_DRV}}};"
       f"SERVER={os.getenv('DB_SERVER','localhost')};"
       f"DATABASE={os.getenv('DB_NAME','')};"
       f"UID={os.getenv('DB_USER','')};"
       f"PWD={os.getenv('DB_PASSWORD','')}")

def fetch_pending():
    rows = pyodbc.connect(_CS).execute(
        "SELECT TOP 20 id,VENDOR_CODE,MATLOT,ITEM_NO,STOCK_QTY,VENDOR_NAME "
        "FROM as400_queue WHERE status='PENDING' ORDER BY created_at"
    ).fetchall()
    return [{"id": r[0], "VENDOR_CODE": r[1] or "", "MATLOT": r[2] or "",
             "ITEM_NO": r[3] or "", "STOCK_QTY": float(r[4] or 0),
             "VENDOR_NAME": r[5] or ""} for r in rows]

# ── EHLLAPI ───────────────────────────────────────────────────────────────────
import ehllapi

SESSION_ID  = os.getenv("AS400_SESSION_ID", "A")
WAIT_TIMEOUT = float(os.getenv("WAIT_TIMEOUT", "15"))
MHT53202_OPT_COL = int(os.getenv("MHT53202_OPT_COL", "2"))

SS = None

def connect():
    global SS
    if SS is None:
        SS = ehllapi.Session()
    sid = SS.connect_any(prefer=SESSION_ID)
    print(f"{GREEN}Connected session '{sid}' ({SS.rows}×{SS.cols}){RESET}")

# ── Screen display ────────────────────────────────────────────────────────────
def show(action="", highlight=None, typed=None):
    """Clear CMD and render the current 5250 buffer."""
    raw = SS.read()
    cols = SS.cols
    lines = [raw[i*cols:(i+1)*cols] for i in range(SS.rows)]

    print(CLEAR, end="")
    print(GREEN + "┌" + "─" * cols + "┐" + RESET)
    for i, ln in enumerate(lines):
        rendered = ""
        ln_upper = ln.upper()

        # highlight the matched row (contains the TAX INV.NO we're selecting)
        if highlight and highlight.upper() in ln_upper:
            rendered = BGREEN + REVERSE + ln + RESET
        # dim blank lines
        elif ln.strip() == "":
            rendered = DIM + ln + RESET
        # status/function-key lines (last 3 rows)
        elif i >= SS.rows - 3:
            rendered = DIM + ln + RESET
        else:
            rendered = GREEN + ln + RESET

        print(GREEN + "│" + RESET + rendered + GREEN + "│" + RESET)

    print(GREEN + "└" + "─" * cols + "┘" + RESET)

    if action:
        print(f"\n  {CYAN}►{RESET} {WHITE}{action}{RESET}")
    if typed:
        print(f"  {YELLOW}  typed: {typed}{RESET}")
    print()

def pause(msg="Press Enter to continue..."):
    print(f"  {DIM}{msg}{RESET}", end="", flush=True)
    input()

def step(action, typed=None, highlight=None, delay=0.3):
    time.sleep(delay)
    show(action=action, typed=typed, highlight=highlight)
    pause()

# ── Dry-run flow per record ───────────────────────────────────────────────────
def dry_run_record(rec, idx, total):
    vc = rec["VENDOR_CODE"]
    ml = rec["MATLOT"]

    header = (f"{CYAN}{'─'*60}{RESET}\n"
              f"  Record {idx}/{total}  "
              f"{WHITE}vendor={vc}  matlot={ml}  item={rec['ITEM_NO']}{RESET}\n"
              f"{CYAN}{'─'*60}{RESET}\n")

    # ── Screen 1: MHT53201 ────────────────────────────────────────────────────
    show(action=f"[MHT53201] Ready — will type VENDOR CODE = {vc}")
    print(header)
    pause("Press Enter to type VENDOR CODE...")

    pos = SS.find("VENDOR CODE")
    if not pos:
        print(f"{RED}✗ MHT53201 not found — is terminal at the right screen?{RESET}")
        pause("Press Enter to skip this record.")
        return
    row, col = pos
    SS.put(row, col, vc)
    step(f"[MHT53201] Typed VENDOR CODE = {vc} → pressing Enter...", typed=vc)

    SS.enter()
    if not SS.wait_for("TAX INV", WAIT_TIMEOUT):
        show(action="[MHT53201] ERROR: MHT53202 did not load (no 'TAX INV' found)")
        pause("Press Enter to skip.")
        return

    # ── Screen 2: MHT53202 ────────────────────────────────────────────────────
    show(action=f"[MHT53202] D/O list loaded — searching for TAX INV.NO = {ml}",
         highlight=ml)
    pause("Press Enter to select the matched row...")

    pos = SS.find(ml)
    if not pos:
        show(action=f"[MHT53202] ERROR: '{ml}' not found on this page")
        pause("Press Enter to skip (will press F3 to go back).")
        SS.send("@F3")
        SS.wait()
        return

    drow, _ = pos
    SS.put(drow, MHT53202_OPT_COL, "1")
    step(f"[MHT53202] Typed '1' in OPT col {MHT53202_OPT_COL} row {drow} → pressing Enter...",
         typed="1", highlight=ml)

    SS.enter()
    if not SS.wait_for("JUDGEMENT", WAIT_TIMEOUT):
        show(action="[MHT53202] ERROR: DHT53203 did not load")
        pause("Press Enter to skip.")
        return

    # ── Screen 3: DHT53203 — DRY RUN STOPS HERE ───────────────────────────────
    show(action=f"[DHT53203] D/O detail loaded — verifying payload...")
    scr = SS.read().upper()
    missing = [k for k in ("VENDOR_CODE", "MATLOT", "ITEM_NO")
               if str(rec.get(k, "")).strip() and str(rec[k]).upper() not in scr]
    if missing:
        print(f"  {RED}✗ VERIFY FAILED — missing on screen: {missing}{RESET}\n")
    else:
        print(f"  {BGREEN}✓ Verified: VENDOR_CODE, MATLOT, ITEM_NO all present on screen{RESET}\n")

    print(f"  {YELLOW}⏸  DRY RUN — stopping before ACCEPT (cursor on JUDGEMENT field){RESET}")
    print(f"  {DIM}   Would send: A → Enter × 2 → DTN07122{RESET}\n")
    pause("Press Enter to press F3 (cancel) and move to next record...")

    SS.send("@F3")   # cancel back without accepting
    SS.wait()
    if not SS.wait_for("MHT53201", WAIT_TIMEOUT):
        # Some terminals need two F3s to get back
        SS.send("@F3")
        SS.wait()

    show(action="[MHT53201] Back at start screen — ready for next record")
    print(f"  {GREEN}✓ Record dry-run complete (no changes committed){RESET}\n")
    time.sleep(0.5)

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    for st in (sys.stdout, sys.stderr):
        try: st.reconfigure(encoding="utf-8", errors="replace")
        except Exception: pass

    connect()
    records = fetch_pending()
    if not records:
        print(f"{YELLOW}No PENDING records in queue.{RESET}")
        return

    print(f"{GREEN}Found {len(records)} PENDING record(s).{RESET}")
    pause("Press Enter to start dry-run walk-through...")

    for i, rec in enumerate(records, 1):
        dry_run_record(rec, i, len(records))

    print(CLEAR + f"{BGREEN}Done — {len(records)} record(s) walked through (no commits).{RESET}\n")

if __name__ == "__main__":
    main()
