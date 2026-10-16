# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bat
:: ต้องรันด้วย 32-bit Python (ehlapi32.dll เป็น 32-bit)
py32\python.exe -m pip install -r requirements.txt
run-ehllapi.bat                 :: หรือ: py32\python.exe as400-rpa.py
```

`py32\` ไม่อยู่ใน git (gitignore) — สร้างโดย download Python 3.13 embeddable win32 แล้วรัน:
```
py32\python.exe -m pip install pyodbc python-dotenv
```

Requires ODBC Driver 17 for SQL Server 32-bit และ pyodbc 32-bit ที่ match กัน

## Architecture

`as400-rpa.py` polls `as400_queue` ใน MSSQL และ drive IBM i (AS400) terminal ผ่าน EHLLAPI
(อ่าน/เขียน 5250 presentation-space buffer ตรง — ไม่ใช้ OCR ไม่ต้องโฟกัสหน้าต่าง)

```
[upstream] INSERT into as400_queue (PENDING)
                    ↑ poll every N seconds
        as400-rpa.py → EHLLAPI (ehlapi32.dll) → AS400 5250 buffer → UPDATE status SENT/FAILED
```

**Module:** `ehllapi.py` — `Session` class: connect, read, find, put, wait_for, send, enter, tab, reset

**Main loop** (`main()`): เปิด PCOMM/PC5250 ก่อนรัน; ถ้า connect ไม่ได้จะ retry ทุก poll cycle:
```
while True:
    connect_session()  → ไม่ได้ → retry
                       → ได้    → ensure_signed_in() (ครั้งแรก) → poll()
```

**4-screen flow per record** (`input_to_as400`):
1. `screen_mht53201_input_vendor` — `SS.find("VENDOR CODE")` → `SS.put(row, col, vendor)` → Enter
2. `screen_mht53202_select_do` — `SS.find(doNo)` (PF8 ถ้าไม่เจอ) → `SS.put(row, OPT_COL, "1")` → Enter
3. `screen_dht53203_accept_do` — verify exact + `SS.send("A")` → Enter × 2
4. `screen_dtn07122_confirm` — verify → fill W/H=I LOC=S1 S/C=2 VENDOR MATLOT shortname → Enter × 2

## Configuration (ENV)

| Variable | Default | Notes |
|---|---|---|
| `DB_SERVER` | `localhost` | MSSQL server |
| `DB_NAME` | `""` | Database name |
| `DB_USER` | `""` | SQL login |
| `DB_PASSWORD` | `""` | SQL password |
| `POLL_INTERVAL` | `5` | วินาทีระหว่าง poll |
| `AS400_SESSION_ID` | `"A"` | EHLLAPI short session id (สแกน A–H อัตโนมัติ) |
| `AS400_USER` | `""` | Sign On username |
| `AS400_PASS` | `""` | Sign On password |
| `PBASMT_USER` | `""` | PBASMT app login |
| `PBASMT_PASS` | `""` | PBASMT app password |
| `AS400_MENU_OPTION` | `""` | เมนูนำทาง เช่น `"1-3"` |
| `PBASMT_CHECK_PASS` | `""` | Password Check screen |
| `PBASMT_SECTION_CODE` | `""` | Section code |
| `SKIP_LOGIN` | `false` | `true` = เริ่มจากหน้า MHT53201 เลย |
| `WAIT_TIMEOUT` | `8` | วินาทีรอหน้าจอเปลี่ยน |
| `MHT53202_OPT_COL` | `2` | คอลัมน์ OPT บนหน้า D/O list (จูนกับจอจริง) |
| `DTN07122_REVIEW_PAUSE` | `0` | หยุดกี่วิหลังกรอกก่อน confirm |
| `DTN07122_DRY_RUN` | `false` | `true` = ข้าม Enter สุดท้าย (ไม่ submit) |
| `DTN07122_CONFIRM_MODE` | `false` | `true` = รอ operator ยืนยันผ่าน DB ก่อน submit |
| `CONFIRM_TIMEOUT_SEC` | `120` | timeout รอ operator |
| `NOTIFY_URL` | `""` | webhook URL แจ้งเตือน mobile ก่อน confirm — ntfy.sh: `https://ntfy.sh/TOPIC`, LINE Notify: `https://notify-api.line.me/api/notify` |
| `NOTIFY_TOKEN` | `""` | Bearer token (LINE Notify token / ntfy private topic token) |

## DB schema

```sql
CREATE TABLE as400_queue (
    id          INT IDENTITY PRIMARY KEY,
    VENDOR_CODE NVARCHAR(50),
    MATLOT      NVARCHAR(100),
    ITEM_NO     NVARCHAR(50),
    STOCK_QTY   DECIMAL(18,3),
    VENDOR_NAME NVARCHAR(200) NULL,
    doNo        NVARCHAR(100) NULL,               -- TAX invoice no. กรอกหน้า invoice input (หลัง vendor code)
    status      NVARCHAR(20)  DEFAULT 'PENDING',  -- PENDING / SENT / FAILED / AWAITING_CONFIRM / CONFIRMED
    screenshot  NVARCHAR(MAX) NULL,               -- screen text เมื่อ AWAITING_CONFIRM
    error       NVARCHAR(MAX) NULL,
    created_at  DATETIME2     DEFAULT GETUTCDATE(),
    updated_at  DATETIME2     DEFAULT GETUTCDATE()
);

CREATE TABLE vendor_shortname (
    vendor_code NVARCHAR(50) PRIMARY KEY,
    shortname   NVARCHAR(50)
);
```

## EHLLAPI DLL quirks (critical)

ใช้ `ehlapi32.dll` จาก IBM i Access — มีข้อจำกัดสำคัญ:
- **Set Cursor (func 40) broken** — return rc=7 ทุก position; ห้ามใช้ `goto()`
- **Cursor arrow mnemonics (@u/@v/@d/@l/@z) ไม่ทำงาน** — ใช้ `@T` (Tab) / `@B` (Backtab) / `@0` (Home) แทน
- **DHT53203: ห้าม put ที่ช่อง JUDGEMENT** — host วาง cursor ไว้ให้แล้วตอนเข้าหน้า, `SS.send("A")` ตรงๆ โดยไม่ต้อง goto (goto จะเลื่อนไปโดน numeric field → rc=5)
- `SS.goto(row, col)` ใน `ehllapi.py` ทำงานผ่าน Home + Tab enumerate (ไม่ใช้ Set Cursor)

## Calibration

`MHT53202_OPT_COL = 2` — คอลัมน์ OPT บนหน้า D/O list ต้องจูนกับจอจริง ตั้งใน `.env`

## Logs

`logs/as400-rpa.log` — RotatingFileHandler 5MB × 5 ไฟล์, UTF-8
