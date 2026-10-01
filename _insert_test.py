import pyodbc
con = pyodbc.connect(
    "DRIVER={ODBC Driver 17 for SQL Server};SERVER=localhost;DATABASE=Warehouse_F5;UID=DATALYZER;PWD=NMB54321",
    timeout=5
)
cur = con.cursor()
cur.execute(
    "INSERT INTO as400_queue (VENDOR_CODE, MATLOT, ITEM_NO, STOCK_QTY, status) VALUES (?,?,?,?,'PENDING')",
    "PT.SUMITRO", "INMB2608/077", "1115-660-00007", 480.0
)
con.commit()
cur.execute("SELECT id, VENDOR_CODE, MATLOT, ITEM_NO, STOCK_QTY, status FROM as400_queue WHERE status='PENDING'")
for r in cur.fetchall():
    print(r)
con.close()
