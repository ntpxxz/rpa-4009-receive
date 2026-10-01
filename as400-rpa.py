r"""
AS400 RPA — EHLLAPI version (พอร์ตจาก as400-rpa.py)

อ่าน/เขียนจอ 5250 ผ่าน EHLLAPI (buffer ตรง) แทน OCR + คลิกพิกเซล + นับ Tab:
  • อ่าน exact  → verify / เลือกแถว D/O ไม่ต้อง OCR/fuzzy/soft-key อีก
  • set_cursor(row,col) ตรงช่อง → เลิกนับ Tab / คลิก
  • wait_for(text)  → sync จริง แทน time.sleep เดา

⚠ ต้องรันด้วย Python 32-bit (ehlapi32.dll เป็น 32-bit) + pyodbc/ODBC driver 32-bit
   ดู py32\ ในโปรเจกต์ (embeddable) — ต้องลง pyodbc 32-bit เพิ่มถ้าจะรันจริง

ฟิลด์ที่ยังต้องจูนกับจอจริง (ตั้งใน .env, เป็น row/col เสถียรกว่า pixel/tab เดิม):
   DTN07122_KGS_TO_VNAME_GAP, MHT53202_OPT_COL
"""
import os, sys, time, logging, logging.handlers, subprocess, pyodbc, ctypes
from dotenv import load_dotenv

import ehllapi

_base = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
load_dotenv(os.path.join(_base, ".env"), override=True)

_log_dir = os.path.join(_base, "logs")
os.makedirs(_log_dir, exist_ok=True)
_fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

_fh = logging.handlers.RotatingFileHandler(
    os.path.join(_log_dir, "as400-rpa.log"), maxBytes=5*1024*1024, backupCount=5, encoding="utf-8")
_fh.setFormatter(_fmt)
_sh = logging.StreamHandler()
_sh.setFormatter(_fmt)
logging.basicConfig(level=logging.INFO, handlers=[_fh, _sh])
log = logging.getLogger("as400-rpa-ehllapi")

# Activity log — one line per record event; readable by supervisor without technical noise
_afh = logging.handlers.RotatingFileHandler(
    os.path.join(_log_dir, "activity.log"), maxBytes=5*1024*1024, backupCount=10, encoding="utf-8")
_afh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
alog = logging.getLogger("as400-rpa.activity")
alog.setLevel(logging.INFO)
alog.addHandler(_afh)
alog.propagate = False

# ─── config ──────────────────────────────────────────────────────────────────
DB_SERVER   = os.getenv("DB_SERVER", "localhost")
DB_NAME     = os.getenv("DB_NAME", "")
DB_USER     = os.getenv("DB_USER", "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "5"))

SESSION_ID    = os.getenv("AS400_SESSION_ID", "A")   # EHLLAPI short session id
AS400_USER    = os.getenv("AS400_USER", "")
AS400_PASS    = os.getenv("AS400_PASS", "")
PBASMT_USER   = os.getenv("PBASMT_USER", "")
PBASMT_PASS   = os.getenv("PBASMT_PASS", "")
AS400_MENU_OPTION   = os.getenv("AS400_MENU_OPTION", "")
PBASMT_CHECK_PASS   = os.getenv("PBASMT_CHECK_PASS", "")
PBASMT_SECTION_CODE = os.getenv("PBASMT_SECTION_CODE", "71DM")  # ponytail: fixed value, override via .env if needed
SKIP_LOGIN = os.getenv("SKIP_LOGIN", "false").lower() == "true"

PCOMM_EXE        = os.getenv("PCOMM_EXE", r"C:\Program Files (x86)\IBM\Client Access\Emulator\pcsws.exe")
PCOMM_ARGS       = os.getenv("PCOMM_ARGS", "").split()  # args ต่อท้าย pcsws.exe เช่น "PBFD5YD1 A35BP"
PCOMM_START_WAIT = int(os.getenv("PCOMM_START_WAIT", "15"))  # seconds to wait for session after launch

# knobs (row/col based — เสถียรกว่า pixel/tab เดิม) ปรับใน .env ได้ไม่ต้อง build
DTN07122_KGS_TO_VNAME_GAP = int(os.getenv("DTN07122_KGS_TO_VNAME_GAP", "1"))  # cols ถัดจาก KGS ถึงช่อง VENDOR_NAME
MHT53202_OPT_COL          = int(os.getenv("MHT53202_OPT_COL", "2"))           # คอลัมน์ OPT (ซ้ายสุดของแถว D/O)
MHT53205_OPT_COL          = int(os.getenv("MHT53205_OPT_COL", "2"))           # คอลัมน์ OPT บนหน้า MHT53205 (จูนกับจอจริง)
WAIT_TIMEOUT              = float(os.getenv("WAIT_TIMEOUT", "8"))
# เทสต์ DTN07122: กรอกครบแล้วหยุดก่อน Enter ยืนยัน (ตรวจ field ก่อน) / หน่วงกี่วิก่อน confirm
DTN07122_STOP_BEFORE_CONFIRM = os.getenv("DTN07122_STOP_BEFORE_CONFIRM", "false").lower() == "true"
DTN07122_REVIEW_PAUSE        = float(os.getenv("DTN07122_REVIEW_PAUSE", "0"))
DTN07122_DRY_RUN             = os.getenv("DTN07122_DRY_RUN", "false").lower() == "true"
DTN07122_CONFIRM_MODE        = True  # ponytail: always on — operator must confirm before every submit
CONFIRM_TIMEOUT_SEC          = int(os.getenv("CONFIRM_TIMEOUT_SEC", "120"))


# ─── DB (เหมือน as400-rpa.py) ─────────────────────────────────────────────────
_DB_CONN_STR = (f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                f"SERVER={DB_SERVER};DATABASE={DB_NAME};UID={DB_USER};PWD={DB_PASSWORD}")

_db_conn       = None
_db_connected  = False  # track first connect vs reconnect

def _get_db():
    global _db_conn, _db_connected
    try:
        if _db_conn:
            _db_conn.execute("SELECT 1")
            return _db_conn
    except Exception:
        log.warning("DB connection lost — reconnecting to %s/%s", DB_SERVER, DB_NAME)
        _db_connected = False
    try:
        _db_conn = pyodbc.connect(_DB_CONN_STR, autocommit=False)
    except Exception as e:
        log.error("DB connect failed: %s/%s — %s", DB_SERVER, DB_NAME, e)
        alog.error("DB_FAIL  %s/%s — %s", DB_SERVER, DB_NAME, e)
        raise
    if not _db_connected:
        log.info("DB connected: %s / %s  user=%s", DB_SERVER, DB_NAME, DB_USER)
        alog.info("DB_CONN  %s/%s  user=%s", DB_SERVER, DB_NAME, DB_USER)
        _db_connected = True
    else:
        log.warning("DB reconnected: %s / %s", DB_SERVER, DB_NAME)
        alog.warning("DB_RECONN  %s/%s", DB_SERVER, DB_NAME)
    return _db_conn

def fetch_pending():
    conn = _get_db()
    rows = conn.execute(
        "SELECT q.id, q.VENDOR_CODE, q.MATLOT, q.ITEM_NO, q.STOCK_QTY, q.VENDOR_NAME, "
        "       vs.shortname "
        "FROM as400_queue q "
        "LEFT JOIN vendor_shortname vs ON vs.vendor_code = q.VENDOR_CODE "
        "WHERE q.status = 'PENDING' ORDER BY q.created_at, q.id"
    ).fetchall()
    return [{"id": r[0], "VENDOR_CODE": r[1] or "", "MATLOT": r[2] or "",
             "ITEM_NO": r[3] or "", "STOCK_QTY": r[4] or 0, "VENDOR_NAME": r[5] or "",
             "shortname": r[6] or ""}
            for r in rows]

def mark_done(record_id, status, error=None):
    conn = _get_db()
    conn.execute("UPDATE as400_queue SET status=?, error=?, updated_at=GETDATE() WHERE id=?",
                 status, error, record_id)
    conn.commit()

def mark_awaiting_confirm(record_id, screen_text):
    conn = _get_db()
    conn.execute("UPDATE as400_queue SET status='AWAITING_CONFIRM', screenshot=?, updated_at=GETDATE() WHERE id=?",
                 screen_text, record_id)
    conn.commit()

def get_queue_status(record_id):
    conn = _get_db()
    row = conn.execute("SELECT status FROM as400_queue WHERE id=?", record_id).fetchone()
    return row[0] if row else "FAILED"

# ─── EHLLAPI session (global) ─────────────────────────────────────────────────
SS = None        # ehllapi.Session
_last_sid = None # track session state to suppress repeated log lines

def connect_session():
    """คืน True ถ้าต่อ session 5250 ได้ (เปิดจอ + sign-in ไว้แล้ว)"""
    global SS, _last_sid
    if SS is None:
        SS = ehllapi.Session()
        log.info("EHLLAPI dll: %s", SS.dll_path)
    try:
        sid = SS.connect_any(prefer=SESSION_ID)
        if sid != _last_sid:
            log.info("EHLLAPI session '%s' connected (%dx%d)", sid, SS.rows, SS.cols)
            _last_sid = sid
        return True
    except ehllapi.EHLLAPIError as e:
        if _last_sid is not None:
            log.warning("EHLLAPI session lost: %s", e)
            _last_sid = None
        return False

def launch_terminal():
    """เปิด PC5250 ถ้ายังไม่รัน — no-op ถ้า session พร้อมแล้ว"""
    if connect_session():
        return   # already running
    if not os.path.exists(PCOMM_EXE):
        log.error("ไม่พบ PCOMM_EXE=%s — เปิด PC5250 เองก่อนรัน", PCOMM_EXE)
        return
    cmd = [PCOMM_EXE] + PCOMM_ARGS
    log.info("เปิด PC5250: %s", " ".join(cmd))
    subprocess.Popen(cmd)
    for _ in range(PCOMM_START_WAIT * 2):
        time.sleep(0.5)
        if connect_session():
            log.info("PC5250 พร้อม — session '%s'", SS.psid)
            return
    log.warning("รอ PC5250 %ds แต่ EHLLAPI session ยังไม่พร้อม", PCOMM_START_WAIT)

def _verify_on_screen(record, keys=("VENDOR_CODE", "MATLOT", "ITEM_NO"), screen="", s=None):
    """เช็ค exact ว่าค่าใน record ปรากฏบนจอ — raise ถ้าไม่ครบ (อ่าน buffer ตรง ไม่มี OCR เพี้ยน)"""
    scr = (s or SS.read()).upper()
    missing = [k for k in keys if str(record.get(k, "")).strip()
               and str(record[k]).upper().rstrip("-") not in scr]
    if missing:
        raise ValueError(f"{screen}: จอไม่ตรงกับ payload — ขาด {missing} "
                         f"(vendor={record.get('VENDOR_CODE')} matlot={record.get('MATLOT')} "
                         f"item={record.get('ITEM_NO')})")

# ─── Login / menu ─────────────────────────────────────────────────────────────
def ensure_signed_in():
    if SKIP_LOGIN:
        log.info("SKIP_LOGIN=true — เริ่มจากหน้าจอปัจจุบัน")
        return
    screen_signon()
    screen_pbasmt_login()
    navigate_menu()
    screen_password_check()
    screen_mht53205_barcode_entry()

def screen_signon():
    if not SS.contains("Sign On"):
        return
    if not (AS400_USER and AS400_PASS):
        log.warning("→ Sign On: ไม่มี AS400_USER/PASS ใน .env — ข้าม")
        return
    SS.send(AS400_USER); SS.tab(); SS.send(AS400_PASS); SS.enter()
    log.info("→ Sign On: %s", AS400_USER)
    SS.wait()

def screen_pbasmt_login():
    if not SS.contains("XDP05301"):
        return
    if not (PBASMT_USER and PBASMT_PASS):
        log.warning("→ PBASMT: ไม่มี PBASMT_USER/PASS ใน .env — ข้าม")
        return
    SS.send(PBASMT_USER); SS.tab(); SS.send(PBASMT_PASS); SS.enter()
    log.info("→ PBASMT: %s", PBASMT_USER)
    SS.wait()

def navigate_menu():
    if not AS400_MENU_OPTION:
        return
    if not SS.contains("TPBAS00W1"):
        log.warning("→ Menu: ไม่พบ TPBAS00W1 บนหน้าจอ — ข้าม")
        return
    for step in filter(None, AS400_MENU_OPTION.split("-")):
        SS.send(step); SS.enter()
        log.info("→ Menu: %s", step)
        SS.wait()

def screen_password_check():
    if not SS.contains("XDP05001"):
        return
    if not PBASMT_CHECK_PASS:
        log.warning("→ PASSWORD CHECK: ไม่มี PBASMT_CHECK_PASS — ข้าม")
        return
    SS.send(PBASMT_CHECK_PASS); SS.tab()
    if PBASMT_SECTION_CODE:
        SS.send("@F")                 # Erase EOF เคลียร์ค่าเดิมในช่อง SECTION CODE
        SS.send(PBASMT_SECTION_CODE)
    SS.enter()
    log.info("→ PASSWORD CHECK: section=%s", PBASMT_SECTION_CODE)
    SS.wait()

# ─── Screen 0: MHT53205 — เลือก BARCODE WITHOUT MAT LOT ──────────────────────
def screen_mht53205_barcode_entry():
    if not SS.contains("MHT53205"):
        return
    pos = SS.find("BARCODE")
    if not pos:
        raise ValueError("MHT53205: หา 'BARCODE' บนหน้าจอไม่เจอ")
    row, _ = pos
    SS.goto(row, MHT53205_OPT_COL)
    SS.enter()
    log.info("→ MHT53205: เลือก BARCODE WITHOUT MAT LOT แถว %d", row)
    if not SS.wait_for("MHT53201", WAIT_TIMEOUT):
        raise ValueError("MHT53205: ไม่ขึ้นหน้า MHT53201 — ตรวจ OPT column (MHT53205_OPT_COL)")

# ─── Screen 1: MHT53201 — กรอก vendor code ────────────────────────────────────
def screen_mht53201_input_vendor(vendor_code):
    pos = SS.find("VENDOR CODE")
    if not pos:
        raise ValueError("MHT53201: หา label 'VENDOR CODE' ไม่เจอ — ไม่อยู่หน้า MHT53201?")
    row, col = pos
    SS.put(row, col, str(vendor_code))   # goto input field ในแถวนั้น + ล้าง + พิมพ์
    SS.enter()
    log.info("→ MHT53201: vendor=%s", vendor_code)
    if not SS.wait_for("TAX INV", WAIT_TIMEOUT):
        raise ValueError("MHT53201: กรอก vendor แล้วไม่ขึ้นหน้า D/O list (MHT53202)")

# ─── Screen 2: MHT53202 — เลือก D/O ที่ TAX INV.NO == MATLOT ───────────────────
MHT53202_MAX_PAGES = 10  # ponytail: upper bound; raise if vendor has huge D/O lists

def screen_mht53202_select_do(tax_inv_no):
    if not tax_inv_no:
        raise ValueError("MHT53202: record ไม่มี MATLOT")
    pos = None
    for page in range(MHT53202_MAX_PAGES):
        pos = SS.find(tax_inv_no)
        if pos:
            break
        log.info("→ MHT53202: ไม่เจอ %s หน้า %d — PF8 ดูหน้าถัดไป", tax_inv_no, page + 1)
        SS.send("@8"); SS.wait()   # PF8 = Page Forward on MHT53202
    if not pos:
        raise ValueError(f"MHT53202: หา D/O ที่ TAX INV.NO={tax_inv_no} ไม่เจอใน {MHT53202_MAX_PAGES} หน้า")
    row, _ = pos
    SS.put(row, MHT53202_OPT_COL, "1")   # OPT ของแถวนั้น = 1 (accept)
    SS.enter()
    log.info("→ MHT53202: เลือกแถว %d (TAX INV.NO=%s)", row, tax_inv_no)
    if not SS.wait_for("JUDGEMENT", WAIT_TIMEOUT):
        raise ValueError("MHT53202: เลือก D/O แล้วไม่ขึ้นหน้า DHT53203")

# ─── Screen 3: DHT53203 — ACCEPT D/O ──────────────────────────────────────────
def screen_dht53203_accept_do(record):
    _verify_on_screen(record, screen="DHT53203")   # exact ทั้ง vendor/matlot/item
    # เข้าหน้านี้ cursor อยู่ที่ช่อง D/O JUDGEMENT แล้ว — พิมพ์ A ตรงตำแหน่งเดิม (อย่า goto:
    # goto จะเลื่อนไปโดนช่องตัวเลข P/O NO. → "requires numeric" rc=5)
    r, c = SS.query_cursor()
    log.info("→ DHT53203: cursor เข้าหน้าที่ r%d c%d — พิมพ์ A", r, c)
    SS.send("A"); SS.enter(); SS.wait(); SS.enter()
    log.info("→ DHT53203: ACCEPT (A) + Enter x2")
    if not SS.wait_for("DTN07122", WAIT_TIMEOUT):
        raise ValueError("DHT53203: accept แล้วไม่ขึ้นหน้า DTN07122")

# ─── Screen 4: DTN07122 — stock-in confirm ────────────────────────────────────
def _dtn07122_header(lines):
    """คืน (index บรรทัด header, ข้อความ header) ของแถว W/H (บรรทัดที่มีทั้ง KGS และ W/H/LOC)"""
    for i, ln in enumerate(lines):
        if "KGS" in ln and ("W/H" in ln or "LOC" in ln or "MATL LOT" in ln):
            return i, ln
    raise ValueError("DTN07122: หา header row (W/H..KGS) บนจอไม่เจอ")

def screen_dtn07122_confirm(record):
    s = SS.read()  # single read — reused for verify and screen_text below
    _verify_on_screen(record, keys=("VENDOR_CODE", "MATLOT", "ITEM_NO"), screen="DTN07122", s=s)

    lines = SS.lines(s)
    hi, hl = _dtn07122_header(lines)
    vr = hi + 2                       # lines 0-based → value row = header_row+1 (1-based +1) = hi+2

    def col(sub):
        c = hl.find(sub)
        if c < 0:
            raise ValueError(f"DTN07122: หา header {sub!r} บนแถวไม่เจอ")
        return c + 1                  # 1-based

    SS.put(vr, col("W/H"),  "I")
    SS.put(vr, col("LOC"),  "S1")
    SS.put(vr, col("S/C"),  "2")
    SS.put(vr, col("VENDOR"), str(record.get("VENDOR_CODE", "")))
    SS.put(vr, col("MATL"), str(record.get("MATLOT", "")))
    
    short_name = str(record.get("shortname", "")).strip()
    if short_name:
        SS.tab(); SS.tab()
        SS.send(short_name)
        
    log.info("→ DTN07122: W/H=I LOC=S1 S/C=2 vendor=%s matlot=%s shortname=%s",
             record.get("VENDOR_CODE"), record.get("MATLOT"), short_name)

    if DTN07122_STOP_BEFORE_CONFIRM:
        raise DryRunStop("DTN07122: กรอกครบแล้ว หยุดก่อน Enter ยืนยัน — ตรวจ field บนจอ")
    if DTN07122_REVIEW_PAUSE > 0:
        log.info("→ DTN07122: กรอกครบ รอ %.0fs ให้ตรวจก่อน confirm...", DTN07122_REVIEW_PAUSE)
        time.sleep(DTN07122_REVIEW_PAUSE)

    if DTN07122_CONFIRM_MODE:
        screen_text = "\n".join(s[i*SS.cols:(i+1)*SS.cols] for i in range(SS.rows))
        log.info("→ DTN07122: CONFIRM_MODE — set AWAITING_CONFIRM, รอ operator (%ds)", CONFIRM_TIMEOUT_SEC)
        mark_awaiting_confirm(record["id"], screen_text)

        deadline = time.time() + CONFIRM_TIMEOUT_SEC
        while time.time() < deadline:
            status = get_queue_status(record["id"])
            if status == "CONFIRMED":
                SS.enter(); SS.wait(); SS.enter()
                log.info("→ DTN07122: confirmed by operator (Enter x2)")
                if not SS.wait_for("MHT53201", WAIT_TIMEOUT):
                    raise ValueError("DTN07122: confirm แล้วไม่กลับหน้า MHT53201")
                mark_done(record["id"], "SENT")
                return
            if status == "FAILED":
                log.info("→ DTN07122: rejected by operator")
                raise DryRunStop("DTN07122: ผู้ใช้ยกเลิกผ่าน web — ไม่ submit")
            time.sleep(1)
        mark_done(record["id"], "FAILED", f"Confirm timeout after {CONFIRM_TIMEOUT_SEC}s")
        raise DryRunStop(f"DTN07122: timeout {CONFIRM_TIMEOUT_SEC}s — ไม่ได้รับการยืนยัน")

    msg = (
        f"ข้อมูลถูกต้อง — ยืนยัน Submit?\n\n"
        f"  VENDOR   : {record.get('VENDOR_CODE', '')}"
        f"{f'  ({short_name})' if short_name else ''}\n"
        f"  MATLOT   : {record.get('MATLOT', '')}\n"
        f"  ITEM     : {record.get('ITEM_NO', '')}\n"
        f"  QTY      : {record.get('STOCK_QTY', '')}\n\n"
        f"  W/H      : I\n"
        f"  LOC      : S1\n"
        f"  S/C      : 2"
    )
    ok = ctypes.windll.user32.MessageBoxW(0, msg, "ยืนยัน Stock-In", 0x44004) == 6  # ponytail: MB_YESNO|MB_ICONQUESTION|MB_TOPMOST, IDYES=6
    if not ok:
        raise DryRunStop("DTN07122: ผู้ใช้ยกเลิก — ไม่ submit (record ยัง PENDING)")

    if DTN07122_DRY_RUN:
        log.info("→ DTN07122: DRY RUN — ข้าม Enter (ไม่ส่งจริง)")
        return
    SS.enter(); SS.wait(); SS.enter()
    log.info("→ DTN07122: confirmed (Enter x2)")
    if not SS.wait_for("MHT53201", WAIT_TIMEOUT):
        raise ValueError("DTN07122: confirm แล้วไม่กลับหน้า MHT53201 — ตรวจ error message บนจอ")

# ─── flow / main loop ─────────────────────────────────────────────────────────
class DryRunStop(Exception):
    """หยุดกลางทางแบบตั้งใจ (DRY_RUN) — ไม่ใช่ error ไม่ต้อง popup ปล่อย record ค้าง PENDING"""

def input_to_as400(record):
    if not record.get("VENDOR_CODE"):
        raise ValueError("record ไม่มี VENDOR_CODE")
    screen_mht53201_input_vendor(record["VENDOR_CODE"])
    screen_mht53202_select_do(record.get("MATLOT", ""))
    screen_dht53203_accept_do(record)
    screen_dtn07122_confirm(record)
    log.info("→ done: vendor=%s item=%s", record["VENDOR_CODE"], record.get("ITEM_NO"))

def _f1_to_mht53201():
    """กด F1 ตรงๆ จาก DTN07122 (DRY_RUN / ยกเลิก) — ไม่ reset ก่อน เพราะ reset รบกวน"""
    time.sleep(0.3)   # ให้ EHLLAPI settle หลัง Windows popup ปิด
    SS.send("@1"); SS.wait()
    SS.send("@1")
    if not SS.wait_for("MHT53201", WAIT_TIMEOUT):
        log.warning("_f1_to_mht53201: F1 ไม่กลับ MHT53201 — ตรวจจอ")

def _back_to_mht53201(max_tries=6):
    """Reset แล้ว F1 ซ้ำจนเห็น MHT53201 — ไม่กด Enter ก่อน (Enter บน DTN07122 จะ submit)"""
    for i in range(max_tries):
        try:
            scr = SS.read().upper()
        except Exception:
            time.sleep(0.5)
            continue
        if "MHT53201" in scr or "VENDOR CODE" in scr:
            log.info("_back_to_mht53201: กลับถึง MHT53201 (attempt %d)", i + 1)
            return True
        log.info("_back_to_mht53201: attempt %d — Reset + F1", i + 1)
        try:
            SS.reset(); SS.wait()
            SS.send("@1"); SS.wait()   # F1 = Back บน terminal นี้
        except Exception as e:
            log.warning("_back_to_mht53201: attempt %d error: %s", i + 1, e)
        time.sleep(0.5)
    log.warning("_back_to_mht53201: ไม่กลับ MHT53201 หลัง %d ครั้ง — ต้องนำทางเอง", max_tries)
    return False

def process_record(record):
    label = record.get("shortname") or record.get("VENDOR_CODE", "?")
    log.info("► id=%-4s [%-8s] item=%-15s matlot=%s",
             record["id"], label, record.get("ITEM_NO"), record.get("MATLOT"))
    t0 = time.time()
    try:
        input_to_as400(record)
        if not DTN07122_CONFIRM_MODE:
            mark_done(record["id"], "SENT")
        elapsed = time.time() - t0
        log.info("✓ SENT id=%-4s [%-8s] %s %s  (%.1fs)",
                 record["id"], label, record.get("ITEM_NO"), record.get("MATLOT"), elapsed)
        alog.info("SENT   id=%-4s vendor=%-8s item=%-15s matlot=%-15s qty=%s  %.1fs",
                  record["id"], label, record.get("ITEM_NO"), record.get("MATLOT"),
                  record.get("STOCK_QTY"), elapsed)
        if DTN07122_DRY_RUN:
            _f1_to_mht53201()
    except DryRunStop as e:
        log.info("⏸ id=%s %s (ยังค้าง PENDING)", record["id"], e)
        _f1_to_mht53201()
    except Exception as e:
        elapsed = time.time() - t0
        log.error("✗ FAIL id=%-4s [%-8s] %s — %s  (%.1fs)",
                  record["id"], label, record.get("ITEM_NO"), e, elapsed)
        alog.error("FAIL   id=%-4s vendor=%-8s item=%-15s matlot=%-15s error=%s  %.1fs",
                   record["id"], label, record.get("ITEM_NO"), record.get("MATLOT"), e, elapsed)
        try:
            SS.reset()
        except Exception:
            pass
        mark_done(record["id"], "FAILED", str(e))
        _back_to_mht53201()

_had_pending = False

def poll():
    global _had_pending
    pending = fetch_pending()
    if not pending:
        if _had_pending:
            log.info("Queue empty")
            _had_pending = False
        return
    if not _had_pending:
        _had_pending = True
    log.info("Queue: %d pending", len(pending))
    for i, record in enumerate(pending, 1):
        log.info("─── %d/%d ─────────────────────────────", i, len(pending))
        process_record(record)

def main():
    for st in (sys.stdout, sys.stderr):
        try: st.reconfigure(encoding="utf-8", errors="replace")
        except Exception: pass
    log.info("=" * 56)
    log.info("AS400 RPA (EHLLAPI) starting")
    log.info("  DB      : %s / %s  user=%s", DB_SERVER, DB_NAME, DB_USER)
    log.info("  Session : %s   Poll: %ds   SKIP_LOGIN: %s", SESSION_ID, POLL_INTERVAL, SKIP_LOGIN)
    log.info("  DRY_RUN : %s   CONFIRM_MODE: %s", DTN07122_DRY_RUN, DTN07122_CONFIRM_MODE)
    log.info("=" * 56)
    alog.info("START  session=%s db=%s/%s skip_login=%s dry_run=%s confirm_mode=%s",
              SESSION_ID, DB_SERVER, DB_NAME, SKIP_LOGIN, DTN07122_DRY_RUN, DTN07122_CONFIRM_MODE)
    launch_terminal()
    signed_in = False
    while True:
        try:
            if not connect_session():
                signed_in = False
            else:
                if not signed_in:
                    ensure_signed_in()
                    signed_in = True
                poll()
        except KeyboardInterrupt:
            log.info("Stopped")
            alog.info("STOP   (KeyboardInterrupt)")
            break
        except Exception as e:
            log.error("Loop error: %s", e, exc_info=True)
            signed_in = False
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
