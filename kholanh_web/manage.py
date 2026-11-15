# -*- coding: utf-8 -*-
"""Quản lý từ dòng lệnh trên máy chủ.

  python manage.py ports                       liệt kê cổng COM
  python manage.py add-user NAME --role admin  tạo tài khoản (hỏi mật khẩu)
  python manage.py set-password NAME           đặt lại mật khẩu
  python manage.py list                        liệt kê tài khoản
  python manage.py disable NAME | enable NAME  khóa / mở khóa
  python manage.py delete NAME                 xóa
"""
import argparse
import getpass
import os
import sys
import time

import config
from db import DB, ROLES


def ask_password():
    p1 = getpass.getpass("Mật khẩu (≥ 8 ký tự): ")
    p2 = getpass.getpass("Nhập lại: ")
    if p1 != p2:
        sys.exit("Hai mật khẩu không khớp.")
    return p1


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ports")
    sub.add_parser("list")
    a = sub.add_parser("add-user"); a.add_argument("name"); a.add_argument("--role", choices=ROLES, default="viewer")
    for c in ("set-password", "disable", "enable", "delete"):
        sub.add_parser(c).add_argument("name")
    args = ap.parse_args()

    if args.cmd == "ports":
        from modbus import list_ports
        ports = list_ports()
        print("\n".join("%s - %s" % p for p in ports) if ports else "Không thấy cổng COM nào (đã cài pyserial chưa?)")
        return

    config.load()
    db = DB(os.path.join(config.DATA_DIR, "kholanh.db"))
    try:
        if args.cmd == "list":
            for u in db.list_users():
                print("%-20s %-9s %-8s đăng nhập gần nhất: %s" % (
                    u["username"], u["role"], "hoạt động" if u["active"] else "KHÓA",
                    time.strftime("%Y-%m-%d %H:%M", time.localtime(u["last_login"])) if u["last_login"] else "—"))
        elif args.cmd == "add-user":
            db.create_user(args.name, ask_password(), args.role)
            print("Đã tạo tài khoản %s (%s)." % (args.name, args.role))
        else:
            u = db.get_user(username=args.name)
            if not u:
                sys.exit("Không có tài khoản '%s'." % args.name)
            if args.cmd == "set-password":
                db.set_password(u["id"], ask_password()); print("Đã đổi mật khẩu.")
            elif args.cmd == "disable":
                db.update_user(u["id"], active=False); print("Đã khóa.")
            elif args.cmd == "enable":
                db.update_user(u["id"], active=True); print("Đã mở khóa.")
            elif args.cmd == "delete":
                db.delete_user(u["id"]); print("Đã xóa.")
    except ValueError as e:
        sys.exit("Lỗi: %s" % e)


if __name__ == "__main__":
    main()
