"""diagnose EHLLAPI cursor-movement mnemonics — run: py32\\python.exe _wr_test.py (จอ MHT53201)"""
import sys; sys.path.insert(0, ".")
from ctypes import c_int, byref, create_string_buffer
import ehllapi

for st in (sys.stdout, sys.stderr):
    try: st.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

ss = ehllapi.Session(); ss.connect_any()
cols = ss.cols
print(f"connected {ss.psid} {ss.rows}x{cols}")

def send(keys):
    b = keys.encode(); f, ln, rc = c_int(3), c_int(len(b)), c_int(0)
    buf = create_string_buffer(b, len(b) + 1)
    ss._hllapi(byref(f), buf, byref(ln), byref(rc)); return rc.value

def qpos():
    buf = create_string_buffer(8); f, ln, rc = c_int(7), c_int(0), c_int(0)
    ss._hllapi(byref(f), buf, byref(ln), byref(rc)); return ln.value

def rc_row_col(p): return f"pos {p} (r{(p-1)//cols+1} c{(p-1)%cols+1})"

print("start:", rc_row_col(qpos()))
# ทดสอบทีละ mnemonic ดู pos ก่อน/หลัง — d=+cols คือ Down, +1 คือ Right, -cols คือ Up
for k in ("@0", "@T", "@T", "@B", "@u", "@v", "@d", "@l", "@z", "@N"):
    before = qpos(); rc = send(k); after = qpos()
    print(f"  {k!r:6} rc={rc}  {rc_row_col(before)} -> {rc_row_col(after)}  d={after-before}")

ss.disconnect(); print("done")
