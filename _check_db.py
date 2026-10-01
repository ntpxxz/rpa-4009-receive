"""
ตรวจสอบ / สร้าง / เพิ่ม test record ใน as400_queue
Usage:
    python _check_db.py          — แสดง pending records ทั้งหมด
    python _check_db.py --setup  — สร้างตารางถ้ายังไม่มี + insert 1 test record
"""
import os, sys, pyodbc
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

conn_str = (
    f"DRIVER={{ODBC Driver 17 for SQL Server}};"
    f"SERVER={os.getenv('DB_SERVER','localhost')};"
    f"DATABASE={os.getenv('DB_NAME','')};"
    f"UID={os.getenv('DB_USER','')};"
    f"PWD={os.getenv('DB_PASSWORD','')}"
)

CREATE_TABLE = """
IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_NAME = 'as400_queue'
)
BEGIN
    CREATE TABLE as400_queue (
        id          INT IDENTITY PRIMARY KEY,
        VENDOR_CODE NVARCHAR(50),
        MATLOT      NVARCHAR(100),
        ITEM_NO     NVARCHAR(50),
        STOCK_QTY   DECIMAL(18,3),
        status      NVARCHAR(20)  DEFAULT 'PENDING',
        error       NVARCHAR(MAX) NULL,
        created_at  DATETIME2     DEFAULT GETUTCDATE(),
        updated_at  DATETIME2     DEFAULT GETUTCDATE()
    )
    PRINT 'Table as400_queue created.'
END
"""

TEST_INSERT = """
INSERT INTO as400_queue (VENDOR_CODE, MATLOT, ITEM_NO, STOCK_QTY, status)
VALUES (?, ?, ?, ?, 'PENDING')
"""

def main():
    setup = "--setup" in sys.argv
    try:
        con = pyodbc.connect(conn_str, timeout=5)
    except Exception as e:
        print(f"[ERROR] DB connect failed: {e}")
        print("  ตรวจสอบ DB_SERVER / DB_NAME / DB_USER / DB_PASSWORD ใน .env")
        return

    cur = con.cursor()

    if setup:
        cur.execute(CREATE_TABLE)
        con.commit()
        print("[OK] table as400_queue ready")

        vendor = input("VENDOR_CODE  (e.g. PT.SUMITRO): ").strip() or "TEST_VENDOR"
        matlot = input("MATLOT       (e.g. INMB2608/077): ").strip() or "TEST-MAT-001"
        item   = input("ITEM_NO      (e.g. 1115-660-00007): ").strip() or "ITEM-001"
        qty    = input("STOCK_QTY    (e.g. 480): ").strip() or "1"
        cur.execute(TEST_INSERT, vendor, matlot, item, float(qty))
        con.commit()
        print(f"[OK] inserted PENDING record — VENDOR={vendor} MATLOT={matlot}")

    # always show pending
    cur.execute("SELECT id, VENDOR_CODE, MATLOT, ITEM_NO, STOCK_QTY, status, created_at FROM as400_queue ORDER BY id DESC")
    rows = cur.fetchall()
    if not rows:
        print("[INFO] as400_queue is empty — run with --setup to insert a test record")
    else:
        print(f"\n{'ID':<6} {'STATUS':<10} {'VENDOR':<15} {'MATLOT':<20} {'ITEM':<18} {'QTY':<8} {'CREATED'}")
        print("-" * 90)
        for r in rows:
            print(f"{r.id:<6} {r.status:<10} {r.VENDOR_CODE or '':<15} {r.MATLOT or '':<20} {r.ITEM_NO or '':<18} {r.STOCK_QTY:<8} {r.created_at}")

    con.close()

if __name__ == "__main__":
    main()
