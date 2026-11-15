# -*- coding: utf-8 -*-
"""SQLite: tài khoản, số liệu đo, nhật ký sự kiện, nhật ký thao tác (audit), cài đặt."""
import json
import re
import sqlite3
import threading
import time

from werkzeug.security import check_password_hash, generate_password_hash

ROLES = ("viewer", "operator", "admin")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
MIN_PASSWORD = 8

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL COLLATE NOCASE,
  pw_hash TEXT NOT NULL,
  role TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  created REAL NOT NULL,
  last_login REAL
);
CREATE TABLE IF NOT EXISTS readings(
  ts REAL NOT NULL, temp REAL, hum REAL, fan1 INTEGER, fan2 INTEGER, alarms TEXT
);
CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(ts);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, level TEXT NOT NULL, msg TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, username TEXT, ip TEXT,
  action TEXT NOT NULL, detail TEXT
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class DB:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def _run(self, sql, args=(), commit=True):
        with self.lock:
            cur = self.conn.execute(sql, args)
            if commit:
                self.conn.commit()
            return cur

    # ---------------------------------------------------------- cài đặt
    def get_setting(self, key, default=None):
        row = self._run("SELECT value FROM settings WHERE key=?", (key,), commit=False).fetchone()
        return json.loads(row["value"]) if row else default

    def set_setting(self, key, value):
        self._run("INSERT INTO settings(key,value) VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    # ---------------------------------------------------------- tài khoản
    @staticmethod
    def validate_password(pw):
        if len(pw or "") < MIN_PASSWORD:
            raise ValueError("Mật khẩu phải có ít nhất %d ký tự" % MIN_PASSWORD)

    def count_users(self):
        return self._run("SELECT COUNT(*) c FROM users", commit=False).fetchone()["c"]

    def create_user(self, username, password, role):
        if not USERNAME_RE.match(username or ""):
            raise ValueError("Tên đăng nhập 3-32 ký tự: chữ, số, _ . -")
        if role not in ROLES:
            raise ValueError("Quyền không hợp lệ")
        self.validate_password(password)
        try:
            self._run("INSERT INTO users(username,pw_hash,role,created) VALUES(?,?,?,?)",
                      (username, generate_password_hash(password), role, time.time()))
        except sqlite3.IntegrityError:
            raise ValueError("Tên đăng nhập đã tồn tại")

    def get_user(self, uid=None, username=None):
        if uid is not None:
            return self._run("SELECT * FROM users WHERE id=?", (uid,), commit=False).fetchone()
        return self._run("SELECT * FROM users WHERE username=?", (username,), commit=False).fetchone()

    def list_users(self):
        rows = self._run("SELECT id,username,role,active,created,last_login FROM users "
                         "ORDER BY username", commit=False).fetchall()
        return [dict(r) for r in rows]

    def verify_login(self, username, password):
        u = self.get_user(username=username or "")
        # luôn băm 1 lần dù user không tồn tại để thời gian phản hồi không lộ tên hợp lệ
        ok = check_password_hash(u["pw_hash"] if u else _DUMMY_HASH, password or "")
        if u and ok and u["active"]:
            self._run("UPDATE users SET last_login=? WHERE id=?", (time.time(), u["id"]))
            return u
        return None

    def set_password(self, uid, password):
        self.validate_password(password)
        self._run("UPDATE users SET pw_hash=? WHERE id=?", (generate_password_hash(password), uid))

    def update_user(self, uid, role=None, active=None):
        if role is not None:
            if role not in ROLES:
                raise ValueError("Quyền không hợp lệ")
            self._run("UPDATE users SET role=? WHERE id=?", (role, uid))
        if active is not None:
            self._run("UPDATE users SET active=? WHERE id=?", (1 if active else 0, uid))

    def count_active_admins(self, exclude_id=None):
        sql, args = "SELECT COUNT(*) c FROM users WHERE role='admin' AND active=1", ()
        if exclude_id is not None:
            sql, args = sql + " AND id<>?", (exclude_id,)
        return self._run(sql, args, commit=False).fetchone()["c"]

    def delete_user(self, uid):
        self._run("DELETE FROM users WHERE id=?", (uid,))

    # ---------------------------------------------------------- số liệu
    def add_reading(self, ts, temp, hum, fan1, fan2, alarms):
        self._run("INSERT INTO readings VALUES(?,?,?,?,?,?)",
                  (ts, temp, hum, int(bool(fan1)), int(bool(fan2)), alarms))

    def readings_since(self, since, until=None, max_points=600):
        until = until or time.time()
        rows = self._run("SELECT ts,temp,hum,fan1,fan2,alarms FROM readings "
                         "WHERE ts>=? AND ts<=? ORDER BY ts", (since, until), commit=False).fetchall()
        rows = [tuple(r) for r in rows]
        if max_points and len(rows) > max_points:      # lấy mẫu đều để đồ thị không quá nặng
            step = len(rows) / float(max_points)
            rows = [rows[int(i * step)] for i in range(max_points)]
        return rows

    def purge_old(self, days):
        cutoff = time.time() - days * 86400
        self._run("DELETE FROM readings WHERE ts<?", (cutoff,))
        self._run("DELETE FROM events WHERE ts<?", (cutoff,))

    # ---------------------------------------------------------- sự kiện / audit
    def add_event(self, ts, level, msg):
        self._run("INSERT INTO events(ts,level,msg) VALUES(?,?,?)", (ts, level, msg))

    def recent_events(self, n=40):
        rows = self._run("SELECT ts,level,msg FROM events ORDER BY id DESC LIMIT ?", (n,),
                         commit=False).fetchall()
        return [tuple(r) for r in reversed(rows)]

    def add_audit(self, username, ip, action, detail=""):
        self._run("INSERT INTO audit(ts,username,ip,action,detail) VALUES(?,?,?,?,?)",
                  (time.time(), username, ip, action, detail))

    def recent_audit(self, n=100):
        rows = self._run("SELECT ts,username,ip,action,detail FROM audit ORDER BY id DESC LIMIT ?",
                         (n,), commit=False).fetchall()
        return [dict(r) for r in rows]


_DUMMY_HASH = generate_password_hash("dummy-password-for-timing")
