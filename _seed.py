import os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from dotenv import load_dotenv; load_dotenv()
import pyodbc
_SQL_DRV = next((d for d in sorted(pyodbc.drivers(), reverse=True) if "SQL Server" in d), "ODBC Driver 17 for SQL Server")
cs = (f"DRIVER={{{_SQL_DRV}}};"
      f"SERVER={os.getenv('DB_SERVER','localhost')};"
      f"DATABASE={os.getenv('DB_NAME','')};"
      f"UID={os.getenv('DB_USER','')};"
      f"PWD={os.getenv('DB_PASSWORD','')}")
conn = pyodbc.connect(cs)
rows = [
    ("MOCK01", "MOCK-INV-001", "ITEM-TEST-A", 100.000, "TEST VENDOR A"),
    ("MOCK02", "MOCK-INV-002", "ITEM-TEST-B", 250.500, "TEST VENDOR B"),
]
for r in rows:
    conn.execute(
        "INSERT INTO as400_queue (VENDOR_CODE,MATLOT,ITEM_NO,STOCK_QTY,VENDOR_NAME) VALUES (?,?,?,?,?)", r)
conn.commit()
print(f"Inserted {len(rows)} mock records (status=PENDING)")
