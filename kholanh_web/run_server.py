# -*- coding: utf-8 -*-
"""Khởi động hệ thống: bộ điều khiển Modbus (luồng nền) + web có đăng nhập.

Chạy thử :  python run_server.py --simulate
Chạy thật:  python run_server.py   (rồi vào web, mục "Thiết bị" để kết nối)
"""
import argparse
import logging
import os
import signal
import sys
from logging.handlers import RotatingFileHandler

import config
from app import create_app
from controller import Controller
from db import DB
from remote import RemoteAccess


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--simulate", action="store_true", help="dùng dữ liệu giả để thử (không cần phần cứng)")
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    args = ap.parse_args()

    cfg = config.load()
    if args.simulate:
        cfg["cli_simulate"] = True          # chỉ cho lần chạy này, không lưu vào CSDL
    if args.host:
        cfg["host"] = args.host
    if args.port:
        cfg["port"] = args.port

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = RotatingFileHandler(os.path.join(config.DATA_DIR, "kholanh.log"),
                             maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stderr and sys.stderr.isatty():
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)

    db = DB(os.path.join(config.DATA_DIR, "kholanh.db"))
    if db.count_users() == 0:
        logging.warning("CHƯA CÓ TÀI KHOẢN NÀO. Chạy: python manage.py add-user admin --role admin")

    ctrl = Controller(cfg, db)
    ctrl.start()
    remote = RemoteAccess(cfg, db, ctrl.event)
    app = create_app(cfg, db, ctrl, remote)
    if db.get_setting("tunnel_autostart", False):
        remote.start_tunnel()

    def shutdown(*_):
        logging.info("Đang dừng...")
        remote.shutdown()
        ctrl.stop()
        os._exit(0)
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    logging.info("Web: http://%s:%s  (mô phỏng=%s)", cfg["host"], cfg["port"], bool(cfg.get("cli_simulate")))
    try:
        from waitress import serve
        serve(app, host=cfg["host"], port=int(cfg["port"]), threads=8)
    except ImportError:
        logging.warning("Chưa cài waitress (pip install waitress), đang dùng server phát triển của Flask.")
        app.run(host=cfg["host"], port=int(cfg["port"]), threaded=True)


if __name__ == "__main__":
    main()
