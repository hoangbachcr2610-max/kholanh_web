# -*- coding: utf-8 -*-
"""Nhập file kho_lanh_log.csv cũ (bản Tkinter) vào CSDL mới.   python import_csv.py duong_dan\\kho_lanh_log.csv"""
import csv
import os
import sys
import time

import config
from db import DB

if len(sys.argv) != 2:
    sys.exit(__doc__)
config.load()
db = DB(os.path.join(config.DATA_DIR, "kholanh.db"))
n = bad = 0
with open(sys.argv[1], encoding="utf-8-sig", newline="") as f:
    rd = csv.reader(f)
    next(rd, None)
    for row in rd:
        try:
            ts = time.mktime(time.strptime(row[0], "%Y-%m-%d %H:%M:%S"))
            db.add_reading(ts, float(row[1]), float(row[2]), int(row[3]), int(row[4]), row[5] if len(row) > 5 else "")
            n += 1
        except Exception:
            bad += 1
print("Đã nhập %d dòng, bỏ qua %d dòng lỗi." % (n, bad))
