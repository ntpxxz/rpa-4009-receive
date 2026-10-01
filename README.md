# AS400 RPA — EHLLAPI version (วิธีใช้งานทั้งระบบ)

พอร์ตการโต้ตอบจอ 5250 จาก **OCR + คลิกพิกเซล + นับ Tab** มาเป็น **EHLLAPI** (อ่าน/เขียน
presentation-space buffer ตรง) — ตัดปัญหา OCR อ่านเพี้ยน (W/H→WIH, DIIQ1687→DI1IQ1687,
1,176.0→176.8) และคลิก/นับ Tab พลาด

## 1. ภาพรวมระบบ
```
[คน/ระบบต้นทาง] INSERT ลง as400_queue (status=PENDING)
                       ↓ poll ทุก POLL_INTERVAL วินาที
as400_rpa_ehllapi.py ──(pyodbc)── MSSQL Warehouse_F5.as400_queue
        │
        └──(EHLLAPI / ehlapi32.dll)── จอ 5250 (IBM i Access, pcsws)
             MHT53201 → MHT53202 → DHT53203 → DTN07122 → UPDATE status=SENT
```
- อ่านคิวจาก MSSQL โดยตรง (ไม่มี backend)
- ขับจอ 5250 ผ่าน EHLLAPI: อ่าน text ที่ row/col เป๊ะ + เขียนลง field ตรงตำแหน่ง
- ผู้ใช้เปิดจอ 5250 + sign-in เอง (SKIP_LOGIN=true) RPA เริ่มที่หน้า MHT53201

## 2. ข้อกำหนด (ทุกอย่างต้อง 32-bit เพราะ ehlapi32.dll เป็น 32-bit)
ติดตั้งครบแล้วในเครื่องนี้:
- **Python 32-bit** → `py32\` (embeddable 3.13, `._pth` เปิด `import site` + path `..` แล้ว)
- **pyodbc 5.3.0 (win32)** + **python-dotenv** → ลงใน `py32` แล้ว
- **ODBC Driver 17 for SQL Server (32-bit)** → มีในเครื่อง
- **ehlapi32.dll** → `C:\Program Files (x86)\IBM\Client Access\Emulator\`

## 3. ตั้งค่า `.env`
```
DB_SERVER=localhost
DB_NAME=Warehouse_F5
DB_USER=DATALYZER
DB_PASSWORD=****
POLL_INTERVAL=5
AS400_WINDOW=PBFD5YD1:A35BP
SKIP_LOGIN=true            # เปิดจอ+signin เอง เริ่มที่ MHT53201
DRY_RUN=true               # เทสต์: หยุดก่อน ACCEPT/confirm (เอาออกเมื่อพร้อมจริง)
# knobs (row/col) จูนถ้าจำเป็น:
# AS400_SESSION_ID=A
# MHT53202_OPT_COL=2
# DTN07122_KGS_TO_VNAME_GAP=1
# WAIT_TIMEOUT=15
```
ถ้าต้องให้ RPA sign-in เอง (SKIP_LOGIN=false) ต้องมี AS400_USER/PASS, PBASMT_USER/PASS,
AS400_MENU_OPTION, PBASMT_CHECK_PASS, PBASMT_SECTION_CODE

## 4. วิธีรัน
เปิดจอ 5250 (pcsws) ให้ sign-in ไปถึงหน้า **MHT53201** ก่อน แล้ว:
```powershell
cd D:\TJ825\S_APP\as400-rpa
.\run-ehllapi.bat
```
หยุด: `Ctrl-C`

**ทดสอบอ่านจออย่างเดียว (ปลอดภัย ไม่เขียน):**
```powershell
.\py32\python.exe ehllapi.py       # พิมพ์จอ 5250 24×80 + พิกัด field
```

## 5. โหมด DRY_RUN (เทสต์ก่อนใช้จริง)
`DRY_RUN=true`: เดิน MHT53201 → MHT53202 → หยุดที่ DHT53203 (เลือก D/O + verify แล้ว)
**ไม่กด ACCEPT / ไม่ confirm stock-in** record ค้าง PENDING — ใช้ยืนยันว่า vendor เข้าถูกช่อง
และเลือก D/O ถูกใบ พอมั่นใจแล้วลบ `DRY_RUN` ออกจาก .env

## 6. flow ต่อ record
| จอ | ทำอะไร (EHLLAPI) |
|---|---|
| MHT53201 | `find("VENDOR CODE")` → set_cursor field → พิมพ์ VENDOR_CODE → Enter |
| MHT53202 | `find(MATLOT)` **exact** → set_cursor(row, OPT) → `1` → Enter |
| DHT53203 | verify vendor/matlot/item exact → `A` (ACCEPT) → Enter×2 |
| DTN07122 | อ่าน header row → set_cursor เขียน W/H=I, S/C=2, VENDOR, MATL LOT, VENDOR_NAME → Enter×2 |

ผิดพลาด → popup, record ค้าง PENDING, `SS.reset()` ปลด input-inhibit, retry รอบถัดไปอัตโนมัติ

## 7. DB schema (as400_queue)
`id, VENDOR_CODE, MATLOT(=TAX INV.NO), ITEM_NO, STOCK_QTY, VENDOR_NAME, status, error, created_at, updated_at`
เพิ่ม record ทดสอบ: `python _check_db.py --setup` (ใช้ Python ตัวไหนก็ได้ที่ต่อ DB ได้)

## 8. Troubleshooting
| อาการ | สาเหตุ/แก้ |
|---|---|
| `No module named 'ehllapi'` | `py32\python313._pth` ต้องมีบรรทัด `..` (แก้แล้ว) |
| `ต่อ session ไม่ได้` (rc=1) | จอ 5250 ไม่ได้เปิด/ยังไม่ sign-in — เปิด pcsws ก่อน |
| `ต้องรันด้วย Python 32-bit` | อย่าใช้ `python` เฉยๆ ใช้ `py32\python.exe` เท่านั้น |
| `Data source name not found` | ODBC Driver 17 (32-bit) ไม่มี — ลง 32-bit |
| field เขียนเลื่อน | จูน `MHT53202_OPT_COL` / `DTN07122_KGS_TO_VNAME_GAP` ใน .env |

## ไฟล์
| ไฟล์ | หน้าที่ |
|---|---|
| `ehllapi.py` | Session class (connect/read/find/set_cursor/send/wait_for) + self-check |
| `as400_rpa_ehllapi.py` | main พอร์ตทุกหน้าจอ |
| `ehllapi_proto.py` | สคริปต์ทดลองอ่านจอ |
| `run-ehllapi.bat` | launcher (py32) |
| `as400-rpa.py` | เวอร์ชัน OCR เดิม (อ้างอิง) |
