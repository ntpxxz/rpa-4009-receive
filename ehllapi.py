r"""
EHLLAPI Session — อ่าน/เขียนจอ 5250 ของ IBM i Access ตรงจาก presentation-space buffer
(ไม่ใช้ OCR / ไม่ต้องโฟกัสหน้าต่าง / ไม่ต้องคลิกพิกเซล)

⚠ ต้องรันด้วย Python 32-bit — ehlapi32.dll เป็น 32-bit (ดู py32\ ในโปรเจกต์)

พื้นฐาน:
    ss = Session(); ss.connect("A")
    ss.wait_for("DTN07122")               # sync แทน sleep
    r, c = ss.find("W/H")                  # (row, col) 1-based
    ss.put(r + 1, c, "I")                  # เขียนลง field ที่ตำแหน่งตรงๆ
    ss.enter()
"""
import os, sys, struct, ctypes
from ctypes import c_int, byref, create_string_buffer

DLL_DIR = r"C:\Program Files (x86)\IBM\Client Access\Emulator"

# EHLLAPI function numbers
_CONNECT, _DISCONNECT, _SENDKEY, _WAIT, _COPYPS = 1, 2, 3, 4, 5
_QUERY_STATUS, _SET_CURSOR = 22, 40


class EHLLAPIError(RuntimeError):
    pass


class Session:
    def __init__(self, dll_dir=DLL_DIR):
        if 8 * struct.calcsize("P") != 32:
            raise EHLLAPIError("ต้องรันด้วย Python 32-bit (ehlapi32.dll เป็น 32-bit) — "
                               "ตอนนี้เป็น %d-bit" % (8 * struct.calcsize("P")))
        if hasattr(os, "add_dll_directory") and os.path.isdir(dll_dir):
            os.add_dll_directory(dll_dir)
        last = None
        for name in ("ehlapi32", "pcshll32"):
            for path in (os.path.join(dll_dir, name), name):
                try:
                    dll = ctypes.WinDLL(path)
                    if hasattr(dll, "hllapi"):
                        self._hllapi = dll.hllapi
                        self.dll_path = path
                        break
                except OSError as e:
                    last = e
            else:
                continue
            break
        else:
            raise EHLLAPIError(f"โหลด ehlapi32/pcshll32 ไม่ได้: {last}")
        self.psid = None
        self.rows, self.cols = 24, 80
        self._stops_cache = None

    # ── low-level ────────────────────────────────────────────────────────────
    def _call(self, func, data=b"", length=None):
        """void hllapi(int* func, char* buf, int* len, int* rc) — standard interface"""
        if length is None:
            length = len(data)
        buf = create_string_buffer(data if data else b"\x00", max(length, 1))
        f, ln, rc = c_int(func), c_int(length), c_int(0)
        self._hllapi(byref(f), buf, byref(ln), byref(rc))
        return buf.raw[:ln.value], rc.value

    # ── connection ───────────────────────────────────────────────────────────
    def connect(self, psid="A"):
        _, rc = self._call(_CONNECT, psid.encode(), 1)
        if rc != 0:
            raise EHLLAPIError(f"connect session '{psid}' rc={rc} "
                               f"(1=ไม่มี session นั้น/ยังไม่เปิด, 9=system)")
        self.psid = psid
        self.rows, self.cols = self._dims(psid)
        self._stops_cache = None
        return self

    def connect_any(self, prefer="A", candidates="ABCDEFGH"):
        order = [prefer] + [c for c in candidates if c != prefer]
        for s in order:
            _, rc = self._call(_CONNECT, s.encode(), 1)
            if rc == 0:
                self.psid = s
                self.rows, self.cols = self._dims(s)
                self._stops_cache = None
                return s
            self._call(_DISCONNECT, s.encode(), 1)
        raise EHLLAPIError("ต่อ session ไม่ได้เลย — เปิดจอ 5250 (pcsws) ให้ sign-in ก่อน")

    def disconnect(self):
        if self.psid:
            self._call(_DISCONNECT, self.psid.encode(), 1)
            self.psid = None

    def _dims(self, psid):
        raw, rc = self._call(_QUERY_STATUS, psid.encode() + b" " * 19, 20)
        if rc == 0 and len(raw) >= 15:
            rows = struct.unpack_from("<H", raw, 11)[0]
            cols = struct.unpack_from("<H", raw, 13)[0]
            if 20 <= rows <= 27 and 80 <= cols <= 132:
                return rows, cols
        return 24, 80

    # ── read ─────────────────────────────────────────────────────────────────
    def read(self):
        """ทั้งจอเป็น string ยาว rows*cols (nulls → space)"""
        size = self.rows * self.cols
        raw, _ = self._call(_COPYPS, b"\x00" * size, size)
        return raw.decode("latin-1", "replace").replace("\x00", " ")

    def lines(self, s=None):
        s = s or self.read()
        return [s[i * self.cols:(i + 1) * self.cols] for i in range(self.rows)]

    def contains(self, needle, s=None):
        return needle.upper() in (s or self.read()).upper()

    def find(self, needle, occurrence=1, s=None):
        """คืน (row, col) 1-based ของ needle (นับครั้งที่ occurrence) — None ถ้าไม่เจอ"""
        s = s or self.read()
        idx, n = -1, 0
        while n < occurrence:
            idx = s.find(needle, idx + 1)
            if idx < 0:
                return None
            n += 1
        return idx // self.cols + 1, idx % self.cols + 1

    def text_at(self, row, col, length, s=None):
        s = s or self.read()
        i = (row - 1) * self.cols + (col - 1)
        return s[i:i + length]

    # ── sync ─────────────────────────────────────────────────────────────────
    def wait(self):
        """รอ host ปลดล็อกคีย์บอร์ด (func 4) — rc 0=พร้อม, 5=ยัง busy/timeout"""
        _, rc = self._call(_WAIT)
        return rc

    def wait_for(self, needle, timeout=10.0, poll=0.25):
        import time
        end = time.time() + timeout
        while time.time() < end:
            self.wait()
            if needle.upper() in self.read().upper():
                return True
            time.sleep(poll)
        return False

    # ── cursor / write ─────────────────────────────────────────────────────────
    # หมายเหตุ: Set Cursor (func 40) ของ ehlapi32 ตัวนี้เสีย (rc=7 ทุกตำแหน่ง) และลูกศร
    # (@u/@v/@d) ไม่ขยับ — เลยเลื่อน cursor ด้วย Home(@0)+Tab(@T) แล้วยืนยันด้วย QueryCursor(7)
    def query_cursor(self):
        """คืน (row, col) 1-based ของ cursor host ปัจจุบัน (func 7)"""
        buf = create_string_buffer(8)
        f, ln, rc = c_int(7), c_int(0), c_int(0)
        self._hllapi(byref(f), buf, byref(ln), byref(rc))
        p = ln.value
        return (p - 1) // self.cols + 1, (p - 1) % self.cols + 1

    def send(self, keys, _retries=3):
        """Send Key (func 3) — ข้อความ + mnemonics เช่น @E=Enter @T=Tab @0=Home @F=EraseEOF @R=Reset
        rc=5 (input inhibited) จะ auto-retry: Reset → Wait → retry (สูงสุด _retries ครั้ง)"""
        import time
        _, rc = self._call(_SENDKEY, keys.encode(), len(keys))
        if rc == 0:
            return
        if rc in (4, 5) and _retries > 0:  # 4=busy, 5=input inhibited — recoverable
            # Reset ปลด input-inhibit แล้วรอ host → retry
            self._call(_SENDKEY, b"@R", 2)   # Reset (raw call — ห้ามเรียก self.send() เพราะ recursive)
            time.sleep(0.3)
            self._call(_WAIT)
            time.sleep(0.3)
            return self.send(keys, _retries=_retries - 1)
        raise EHLLAPIError(f"send {keys!r} rc={rc} (2=พารามิเตอร์ผิด, 4=busy, 5=input inhibited)")

    def field_stops(self, max_fields=120):
        """[(row,col)] ของ input field ทุกช่องตามลำดับ Tab (Home แล้ว Tab จนวนกลับ)"""
        if self._stops_cache is not None:
            return self._stops_cache
        self.send("@0")
        first = self.query_cursor()
        stops = [first]
        for _ in range(max_fields):
            self.send("@T")
            cur = self.query_cursor()
            if cur == first:
                break
            stops.append(cur)
        self._stops_cache = stops
        return stops

    def goto(self, row, col):
        """เลื่อน cursor ไป input field ที่ตรง (row, ใกล้ col ที่สุด) ด้วย Home+Tab — คืน (row,col) จริงที่ไปถึง"""
        stops = self.field_stops()
        same_row = [s for s in stops if s[0] == row] or stops
        target = min(same_row, key=lambda s: (abs(s[0] - row), abs(s[1] - col)))
        idx = stops.index(target)
        self.send("@0")
        for _ in range(idx):
            self.send("@T")
        return self.query_cursor()

    def put(self, row, col, text, clear=True):
        """ไป field ที่ (row,col) แล้วพิมพ์ text (clear=ล้างค่าเดิมด้วย Erase-EOF ก่อน)"""
        landed = self.goto(row, col)
        if clear:
            self.send("@F")            # Erase to End of Field (cursor อยู่หัว field)
        self.send(str(text))
        return landed

    def enter(self):
        self._stops_cache = None   # screen transitions after Enter
        self.send("@E")

    def tab(self):
        self.send("@T")

    def reset(self):
        self.send("@R")     # ปลด input-inhibit


# ── self-check: อ่านจอปัจจุบันออกมาโชว์ (เปิดจอ 5250 ให้ sign-in ก่อน) ───────────
if __name__ == "__main__":
    for st in (sys.stdout, sys.stderr):
        try: st.reconfigure(encoding="utf-8", errors="replace")
        except Exception: pass
    ss = Session()
    print("dll:", ss.dll_path)
    sid = ss.connect_any()
    print(f"connected '{sid}'  {ss.rows}x{ss.cols}\n" + "─" * ss.cols)
    for ln in ss.lines():
        print(ln)
    print("─" * ss.cols)
    for kw in ("DTN07122", "W/H", "MATL LOT", "STK-IN QTY", "VENDOR"):
        pos = ss.find(kw)
        print(f"  {kw!r}: {'row %d col %d' % pos if pos else 'ไม่พบ'}")
    ss.disconnect()
