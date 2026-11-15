# -*- coding: utf-8 -*-
"""Web Flask có đăng nhập tài khoản + phân quyền (viewer / operator / admin)."""
import csv
import io
import secrets
import threading
import time
from datetime import timedelta
from functools import wraps
from urllib.parse import urlparse

from flask import (Flask, Response, abort, jsonify, redirect, render_template, request,
                   session, url_for)
from flask.sessions import SecureCookieSessionInterface

from db import ROLES
from modbus import list_ports, serial as _serial

RANK = {"viewer": 1, "operator": 2, "admin": 3}
# viewer: xem số liệu, lịch sử · operator: + chế độ Auto/Thủ công, bật/tắt quạt, đổi ngưỡng
# admin: + failsafe, thiết bị Modbus, truy cập từ xa, quản lý tài khoản, xem audit


class LoginThrottle:
    """Chống dò mật khẩu: tối đa 5 lần sai/5 phút theo IP và 10 lần sai/10 phút theo tên đăng nhập."""

    def __init__(self):
        self.lock = threading.Lock()
        self.by_ip, self.by_user = {}, {}

    @staticmethod
    def _prune(lst, window):
        now = time.time()
        lst[:] = [t for t in lst if now - t < window]

    def blocked(self, ip, user):
        with self.lock:
            a = self.by_ip.setdefault(ip, [])
            b = self.by_user.setdefault((user or "").lower(), [])
            self._prune(a, 300); self._prune(b, 600)
            return len(a) >= 5 or len(b) >= 10

    def fail(self, ip, user):
        with self.lock:
            self.by_ip.setdefault(ip, []).append(time.time())
            self.by_user.setdefault((user or "").lower(), []).append(time.time())

    def ok(self, ip, user):
        with self.lock:
            self.by_ip.pop(ip, None)
            self.by_user.pop((user or "").lower(), None)


class DynamicSecureSession(SecureCookieSessionInterface):
    """Cookie chỉ gắn cờ Secure khi người dùng vào bằng HTTPS (Cloudflare Tunnel).
    Vào bằng http://IP-LAN hoặc Tailscale thì không gắn cờ này, nếu không trình duyệt sẽ không giữ phiên đăng nhập."""
    force = False

    def get_cookie_secure(self, app):
        return bool(self.force or request.is_secure
                    or request.headers.get("X-Forwarded-Proto", "").lower() == "https"
                    or '"https"' in request.headers.get("CF-Visitor", "").lower())


def create_app(cfg, db, ctrl, remote):
    app = Flask(__name__)
    key = db.get_setting("secret_key")
    if not key:
        key = secrets.token_hex(32)
        db.set_setting("secret_key", key)
    app.secret_key = key
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=float(cfg["session_hours"])),
        MAX_CONTENT_LENGTH=64 * 1024,
    )
    sess_if = DynamicSecureSession()
    sess_if.force = bool(cfg["secure_cookie"])
    app.session_interface = sess_if
    throttle = LoginThrottle()

    # ------------------------------------------------------------ tiện ích
    def client_ip():
        ip = request.remote_addr or "?"
        # Chỉ tin header của Cloudflare khi kết nối đến từ chính máy này (cloudflared chạy cùng máy)
        if ip in ("127.0.0.1", "::1"):
            ip = request.headers.get("CF-Connecting-IP", ip)
        return ip

    def current_user():
        uid = session.get("uid")
        if not uid:
            return None
        u = db.get_user(uid=uid)
        if not u or not u["active"] or session.get("pwv") != u["pw_hash"][-16:]:
            session.clear()                  # tài khoản bị khóa / đổi mật khẩu -> đăng xuất
            return None
        return u

    def who(u):
        return "%s" % u["username"]

    def audit(u, action, detail=""):
        db.add_audit(u["username"] if u else None, client_ip(), action, detail)

    def require(role):
        def deco(fn):
            @wraps(fn)
            def wrapper(*a, **kw):
                u = current_user()
                is_api = request.path.startswith("/api/")
                if not u:
                    if is_api:
                        return jsonify(error="Chưa đăng nhập"), 401
                    return redirect(url_for("login", next=request.full_path.rstrip("?")))
                if RANK[u["role"]] < RANK[role]:
                    if is_api:
                        return jsonify(error="Tài khoản không đủ quyền"), 403
                    abort(403)
                return fn(u, *a, **kw)
            return wrapper
        return deco

    def safe_next(target):
        if target and target.startswith("/") and not target.startswith("//") \
                and not urlparse(target).netloc:
            return target
        return url_for("dashboard")

    def json_body():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError("Dữ liệu gửi lên không hợp lệ")
        return data

    # ------------------------------------------------------------ bảo mật chung
    @app.before_request
    def csrf_guard():
        """Chặn request ghi đến từ trang web khác (CSRF) bằng Sec-Fetch-Site / Origin."""
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            sfs = request.headers.get("Sec-Fetch-Site")
            if sfs and sfs not in ("same-origin", "none"):
                abort(403)
            origin = request.headers.get("Origin")
            if origin and urlparse(origin).netloc != request.host:
                abort(403)

    @app.after_request
    def headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
        if not request.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.errorhandler(403)
    def e403(e):
        if request.path.startswith("/api/"):
            return jsonify(error="Bị từ chối"), 403
        return render_template("message.html", title="Không đủ quyền",
                               msg="Tài khoản của bạn không có quyền truy cập trang này."), 403

    @app.errorhandler(404)
    def e404(e):
        if request.path.startswith("/api/"):
            return jsonify(error="Không tìm thấy"), 404
        return render_template("message.html", title="Không tìm thấy", msg="Trang không tồn tại."), 404

    @app.errorhandler(ValueError)
    def e_value(e):
        return jsonify(error=str(e)), 400

    # ------------------------------------------------------------ đăng nhập
    @app.route("/login", methods=["GET", "POST"])
    def login():
        nxt = safe_next(request.values.get("next"))
        if request.method == "GET":
            if current_user():
                return redirect(nxt)
            return render_template("login.html", next=nxt, error=None,
                                   no_users=db.count_users() == 0)
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        ip = client_ip()
        if throttle.blocked(ip, username):
            audit(None, "login_blocked", username)
            return render_template("login.html", next=nxt, no_users=False,
                                   error="Đăng nhập sai quá nhiều lần. Thử lại sau vài phút."), 429
        u = db.verify_login(username, password)
        if not u:
            throttle.fail(ip, username)
            audit(None, "login_fail", username)
            return render_template("login.html", next=nxt, no_users=False,
                                   error="Sai tên đăng nhập hoặc mật khẩu, hoặc tài khoản đã bị khóa."), 401
        throttle.ok(ip, username)
        session.clear()
        session.permanent = True
        session["uid"] = u["id"]
        session["pwv"] = u["pw_hash"][-16:]
        audit(u, "login")
        return redirect(nxt)

    @app.route("/logout", methods=["POST"])
    def logout():
        u = current_user()
        if u:
            audit(u, "logout")
        session.clear()
        return redirect(url_for("login"))

    @app.route("/healthz")
    def healthz():
        return "ok"

    # ------------------------------------------------------------ trang
    @app.route("/")
    @require("viewer")
    def dashboard(u):
        return render_template("dashboard.html", me=u)

    @app.route("/history")
    @require("viewer")
    def history_page(u):
        return render_template("history.html", me=u)

    @app.route("/account")
    @require("viewer")
    def account_page(u):
        return render_template("account.html", me=u)

    @app.route("/admin/users")
    @require("admin")
    def users_page(u):
        return render_template("users.html", me=u, roles=ROLES)

    @app.route("/connection")
    @require("admin")
    def connection_page(u):
        return render_template("connection.html", me=u)

    @app.route("/remote")
    @require("admin")
    def remote_page(u):
        return render_template("remote.html", me=u)

    # ------------------------------------------------------------ API: số liệu
    @app.route("/api/state")
    @require("viewer")
    def api_state(u):
        s = ctrl.snapshot()
        s["me"] = {"username": u["username"], "role": u["role"]}
        return jsonify(s)

    def _hours():
        try:
            return min(max(float(request.args.get("hours", 24)), 0.1), 24 * 365)
        except ValueError:
            return 24.0

    @app.route("/api/history")
    @require("viewer")
    def api_history(u):
        h = _hours()
        rows = db.readings_since(time.time() - h * 3600, max_points=800)
        return jsonify(hours=h, rows=[[r[0], r[1], r[2], r[3], r[4], r[5]] for r in rows])

    @app.route("/history.csv")
    @require("viewer")
    def history_csv(u):
        h = _hours()
        rows = db.readings_since(time.time() - h * 3600, max_points=0)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Thoi gian", "Nhiet do (C)", "Do am (%RH)", "Quat 1", "Quat 2", "Canh bao"])
        for ts, t, hm, f1, f2, al in rows:
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)),
                        "%.1f" % t, "%.1f" % hm, f1, f2, al or ""])
        audit(u, "export_csv", "%gh" % h)
        # utf-8-sig để Excel mở không lỗi font
        return Response(("﻿" + buf.getvalue()).encode("utf-8"), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=kho_lanh_%s.csv"
                                 % time.strftime("%Y%m%d_%H%M")})

    # ------------------------------------------------------------ API: điều khiển
    @app.route("/api/mode", methods=["POST"])
    @require("operator")
    def api_mode(u):
        mode = json_body().get("mode")
        ctrl.set_mode(mode, who(u))
        audit(u, "set_mode", str(mode))
        return jsonify(ok=True)

    @app.route("/api/fan", methods=["POST"])
    @require("operator")
    def api_fan(u):
        d = json_body()
        ch, on = d.get("ch"), d.get("on")
        if not isinstance(ch, int) or not isinstance(on, bool):
            raise ValueError("Dữ liệu không hợp lệ")
        ctrl.set_fan(ch, on, who(u))
        audit(u, "set_fan", "quat %d -> %s" % (ch + 1, "BẬT" if on else "TẮT"))
        return jsonify(ok=True)

    @app.route("/api/failsafe", methods=["POST"])
    @require("admin")
    def api_failsafe(u):
        v = json_body().get("value")
        ctrl.set_failsafe(v, who(u))
        audit(u, "set_failsafe", str(v))
        return jsonify(ok=True)

    @app.route("/api/thresholds", methods=["POST"])
    @require("operator")
    def api_thr(u):
        d = json_body()
        ctrl.set_thresholds(d, who(u))
        audit(u, "set_thresholds", str({k: d.get(k) for k in d}))
        return jsonify(ok=True)

    # ------------------------------------------------------------ API: Kết nối Modbus RTU (admin)
    @app.route("/api/connection")
    @require("admin")
    def api_connection(u):
        d = ctrl.get_conn()
        d["ports"] = [{"device": dev, "description": desc} for dev, desc in list_ports()]
        d["pyserial"] = _serial is not None
        d["bauds"] = [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]
        return jsonify(d)

    @app.route("/api/connection/connect", methods=["POST"])
    @require("admin")
    def api_conn_connect(u):
        d = json_body()
        try:
            ctrl.connect(d, who(u))
        finally:
            audit(u, "modbus_connect", "%s" % {k: d.get(k) for k in ("port", "baud", "parity", "simulate")})
        return jsonify(ok=True)

    @app.route("/api/connection/disconnect", methods=["POST"])
    @require("admin")
    def api_conn_disconnect(u):
        ctrl.disconnect(who(u))
        audit(u, "modbus_disconnect")
        return jsonify(ok=True)

    @app.route("/api/connection/apply", methods=["POST"])
    @require("admin")
    def api_conn_apply(u):
        d = json_body()
        ctrl.apply_conn(d, who(u))
        audit(u, "modbus_apply", "%s" % {k: d.get(k) for k in ("port", "baud", "parity", "simulate")})
        return jsonify(ok=True)

    # ------------------------------------------------------------ API: Điều khiển từ xa (admin)
    @app.route("/api/remote")
    @require("admin")
    def api_remote(u):
        return jsonify(remote.status())

    @app.route("/api/remote/tailscale", methods=["POST"])
    @require("admin")
    def api_remote_ts(u):
        remote.check_tailscale()
        return jsonify(ok=True)

    @app.route("/api/remote/tunnel", methods=["POST"])
    @require("admin")
    def api_remote_tunnel(u):
        on = json_body().get("on")
        if not isinstance(on, bool):
            raise ValueError("Dữ liệu không hợp lệ")
        remote.start_tunnel() if on else remote.stop_tunnel()
        audit(u, "tunnel_on" if on else "tunnel_off")
        return jsonify(ok=True)

    @app.route("/api/remote/autostart", methods=["POST"])
    @require("admin")
    def api_remote_autostart(u):
        on = json_body().get("on")
        if not isinstance(on, bool):
            raise ValueError("Dữ liệu không hợp lệ")
        db.set_setting("tunnel_autostart", on)
        audit(u, "tunnel_autostart", str(on))
        return jsonify(ok=True)

    # ------------------------------------------------------------ API: tài khoản
    @app.route("/api/password", methods=["POST"])
    @require("viewer")
    def api_password(u):
        d = json_body()
        if not db.verify_login(u["username"], d.get("current", "")):
            raise ValueError("Mật khẩu hiện tại không đúng")
        db.set_password(u["id"], d.get("new", ""))
        audit(u, "change_password")
        session.clear()                       # bắt đăng nhập lại bằng mật khẩu mới
        return jsonify(ok=True)

    @app.route("/api/users", methods=["GET", "POST"])
    @require("admin")
    def api_users(u):
        if request.method == "GET":
            return jsonify(users=db.list_users(), audit=db.recent_audit(60))
        d = json_body()
        db.create_user((d.get("username") or "").strip(), d.get("password", ""), d.get("role"))
        audit(u, "create_user", "%s (%s)" % (d.get("username"), d.get("role")))
        return jsonify(ok=True)

    @app.route("/api/users/<int:uid>", methods=["POST", "DELETE"])
    @require("admin")
    def api_user_one(u, uid):
        target = db.get_user(uid=uid)
        if not target:
            raise ValueError("Không có tài khoản này")
        if request.method == "DELETE":
            if uid == u["id"]:
                raise ValueError("Không thể tự xóa chính mình")
            if target["role"] == "admin" and target["active"] and db.count_active_admins(uid) == 0:
                raise ValueError("Phải còn ít nhất 1 admin đang hoạt động")
            db.delete_user(uid)
            audit(u, "delete_user", target["username"])
            return jsonify(ok=True)
        d = json_body()
        new_role = d.get("role", target["role"])
        new_active = d.get("active", bool(target["active"]))
        losing_admin = target["role"] == "admin" and target["active"] and \
            (new_role != "admin" or not new_active)
        if losing_admin and db.count_active_admins(uid) == 0:
            raise ValueError("Phải còn ít nhất 1 admin đang hoạt động")
        if "role" in d or "active" in d:
            db.update_user(uid, role=new_role, active=bool(new_active))
            audit(u, "update_user", "%s role=%s active=%s" % (target["username"], new_role, new_active))
        if d.get("password"):
            db.set_password(uid, d["password"])
            audit(u, "reset_password", target["username"])
        return jsonify(ok=True)

    return app
