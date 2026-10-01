r"""
EHLLAPI prototype — อ่าน/เขียนจอ 5250 ของ IBM i Access ตรงจาก buffer (ไม่ใช้ OCR)

ทำไม: ตัวหลักตอนนี้ screen-scrape ด้วย Tesseract OCR + คลิกพิกเซล ซึ่งเพี้ยนบ่อย
(W/H→WIH, DIIQ1687→DI1IQ1687, 1,176.0→176.8). EHLLAPI อ่าน text ที่ row/col ได้เป๊ะ
และเขียนลง field ได้ตรงๆ ไม่ต้องเดา

⚠ ต้องรันด้วย Python 32-bit เพราะ ehlapi32.dll เป็น 32-bit
   ตัวเครื่องนี้มีแต่ Python 64-bit — ลง Python 32-bit ก่อน (python.org → "Windows installer (32-bit)")
   แล้วรัน:  C:\path\to\python32\python.exe ehllapi_proto.py
   (อ่านอย่างเดียว ปลอดภัย ไม่เขียนอะไรลงจอ)

เขียนทดสอบ (ระวัง เขียนลงจอจริง):
   python32 ehllapi_proto.py --write "I" --row 21 --col 2
"""
import sys, os, struct, ctypes, argparse
from ctypes import c_int, byref, create_string_buffer

DLL_DIR = r"C:\Program Files (x86)\IBM\Client Access\Emulator"

# ── EHLLAPI function numbers ──────────────────────────────────────────────────
CONNECT_PS, DISCONNECT_PS, SEND_KEY = 1, 2, 3
COPY_PS, SEARCH_PS = 5, 6
SET_SESSION_PARMS = 9
QUERY_SESSION_STATUS = 22
SET_CURSOR = 40


def load_hllapi():
    if 8 * struct.calcsize("P") != 32:
        sys.exit("❌ ต้องรันด้วย Python 32-bit (ehlapi32.dll เป็น 32-bit) — ตอนนี้เป็น %d-bit"
                 % (8 * struct.calcsize("P")))
    if hasattr(os, "add_dll_directory") and os.path.isdir(DLL_DIR):
        os.add_dll_directory(DLL_DIR)     # ให้ ehlapi32 หา DLL ที่มันพึ่งพาในโฟลเดอร์เดียวกันเจอ
    last = None
    for name in ("ehlapi32", "pcshll32"):
        for path in (os.path.join(DLL_DIR, name), name):
            try:
                dll = ctypes.WinDLL(path)            # WinDLL = stdcall (WINAPI)
                if hasattr(dll, "hllapi"):
                    print(f"✓ loaded {path}.dll")
                    return dll.hllapi
            except OSError as e:
                last = e
    sys.exit(f"❌ โหลด ehlapi32/pcshll32 ไม่ได้: {last}")


def make_call(hllapi):
    def call(func, data=b"", length=None):
        """standard interface: void hllapi(int* func, char* buf, int* len, int* rc)"""
        if length is None:
            length = len(data)
        buf = create_string_buffer(data if data else b"\x00", max(length, 1))
        f, ln, rc = c_int(func), c_int(length), c_int(0)
        hllapi(byref(f), buf, byref(ln), byref(rc))
        return buf.raw[:ln.value], ln.value, rc.value
    return call


def query_dims(call, psid):
    """Query Session Status (22) → (rows, cols); fallback 24x80"""
    data = psid.encode() + b" " * 19
    raw, _, rc = call(QUERY_SESSION_STATUS, data, len(data))
    if rc == 0 and len(raw) >= 15:
        rows = struct.unpack_from("<H", raw, 11)[0]
        cols = struct.unpack_from("<H", raw, 13)[0]
        if 20 <= rows <= 27 and 80 <= cols <= 132:
            return rows, cols
    return 24, 80


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default="A", help="EHLLAPI short session id (default A)")
    ap.add_argument("--write", help="ข้อความที่จะพิมพ์ลงจอ (ต้องมี --row --col)")
    ap.add_argument("--row", type=int)
    ap.add_argument("--col", type=int)
    args = ap.parse_args()

    for stream in (sys.stdout, sys.stderr):   # console ไทย (cp874) encode ✓/❌ ไม่ได้
        try: stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception: pass

    hllapi = load_hllapi()
    call = make_call(hllapi)

    # หา session ที่ต่อได้ — ลอง arg ก่อน แล้วสแกน A..H (rc: 0=ok 1=PS ไม่มี/ไม่พร้อม 9=system)
    tried = [args.session] + [c for c in "ABCDEFGH" if c != args.session]
    psid = None
    for s in tried:
        _, _, rc = call(CONNECT_PS, s.encode(), 1)
        print(f"  connect '{s}' -> rc={rc}")
        if rc == 0:
            psid = s
            print(f"✓ connected to session '{s}'")
            break
        call(DISCONNECT_PS, s.encode(), 1)
    if not psid:
        sys.exit("❌ ต่อ session ไม่ได้ — เปิดจอ 5250 (pcsws) ให้ sign-in ไว้ก่อน "
                 "(rc=1 ทุกตัว = ไม่มี session นั้นเปิดอยู่)")

    rows, cols = query_dims(call, psid)
    size = rows * cols
    print(f"  PS size: {rows} x {cols} = {size}\n")

    # ── อ่านทั้งจอ (Copy Presentation Space, func 5) ──
    raw, ln, rc = call(COPY_PS, b"\x00" * size, size)
    if rc not in (0, 1):   # 0=ok, 1=ok แต่ input inhibited
        print(f"⚠ Copy PS rc={rc}")
    text = raw.decode("latin-1", "replace")   # Copy PS คืน text ที่ต่ออยู่แล้ว (ไม่ใช่ EBCDIC ดิบ)
    print("─" * cols)
    for r in range(rows):
        print(text[r * cols:(r + 1) * cols])
    print("─" * cols)

    # ── หา field สำคัญ พร้อมตำแหน่ง row/col (พิสูจน์ว่าเล็งได้เป๊ะ) ──
    print("\nพิกัดที่หาเจอ (row, col) — เทียบกับ OCR ที่เคยเพี้ยน:")
    for needle in ("DTN07122", "W/H", "MATL LOT", "STK-IN QTY", "VENDOR"):
        pos = text.find(needle)
        if pos >= 0:
            print(f"  '{needle}': row {pos // cols + 1}, col {pos % cols + 1}")
        else:
            print(f"  '{needle}': ไม่พบ")

    # ── เขียนทดสอบ (ออปชัน) ──
    if args.write and args.row and args.col:
        pos = (args.row - 1) * cols + args.col      # 1-based PS position
        _, _, rc = call(SET_CURSOR, b"", pos)
        print(f"\nSet cursor row {args.row} col {args.col} (pos {pos}) rc={rc}")
        _, _, rc = call(SEND_KEY, args.write.encode(), len(args.write))
        print(f"Send Key {args.write!r} rc={rc}  (rc=0 สำเร็จ)")

    call(DISCONNECT_PS, psid.encode(), 1)
    print("\n✓ done (disconnected)")


if __name__ == "__main__":
    main()
