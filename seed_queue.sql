-- seed_queue.sql — 3 test records for dry-run / mockup testing
-- Reset first (optional):
-- DELETE FROM as400_queue WHERE status = 'PENDING';

INSERT INTO as400_queue (VENDOR_CODE, MATLOT, ITEM_NO, STOCK_QTY, VENDOR_NAME)
VALUES
    ('V0001', 'INV-2024-001', 'ITEM-ABC', 100.000, 'ACME CORPORATION'),
    ('V0002', 'INV-2024-002', 'ITEM-DEF', 250.500, 'GLOBAL SUPPLIES LTD'),
    ('V0003', 'INV-2024-003', 'ITEM-GHI',  75.750, 'TECH PARTS CO');
