# -*- coding: utf-8 -*-
"""Truy cập từ xa: 1) cùng Wi-Fi/LAN  2) Tailscale (VPN riêng)  3) Cloudflare Quick Tunnel (link Internet).
Logic lấy từ web_phone.py bản cũ; khác là giờ điều khiển qua trang web (admin), và web có đăng nhập tài khoản."""
import ipaddress
import logging
import os
import re
import shutil
import socket
import subprocess
import threading
from collections import deque

log = logging.getLogger("kholanh.remote")
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))          # không gửi gói tin, chỉ để chọn card mạng
        return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        s.close()


def tailscale_ip():
    """IPv4 Tailscale (dải 100.64.0.0/10) của máy này, hoặc None nếu chưa cài / chưa kết nối.
    Chạy lệnh ngoài nên có thể mất vài giây: chỉ gọi trong luồng riêng."""
    cgnat = ipaddress.ip_network("100.64.0.0/10")

    def is_ts(text):
        try:
            return ipaddress.ip_address(text.strip()) in cgnat
        except ValueError:
            return False

    exes = [shutil.which("tailscale")]
    for env in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env)
        if base:
            exes.append(os.path.join(base, "Tailscale", "tailscale.exe"))
    for exe in dict.fromkeys(e for e in exes if e):
        if not os.path.exists(exe):
            continue
        try:
            out = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True,
                                 timeout=5, creationflags=CREATE_NO_WINDOW)
            for line in out.stdout.splitlines():
                if is_ts(line):
                    return line.strip()
        except Exception:
            pass
    try:                                    # dự phòng: tìm trong các địa chỉ IP của máy
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            if is_ts(info[4][0]):
                return info[4][0]
    except OSError:
        pass
    return None


def find_cloudflared(binary):
    exe = shutil.which(binary)
    if exe:
        return exe
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(env)
        if base:
            p = os.path.join(base, "cloudflared", "cloudflared.exe")
            if os.path.exists(p):
                return p
    return None


class QuickTunnel:
    """Chạy `cloudflared tunnel --url ...` (miễn phí, không cần tên miền) và bắt địa chỉ
    https://xxxx.trycloudflare.com. Link đổi mỗi lần chạy lại; không tự khởi động lại khi cloudflared thoát."""
    URL_RE = re.compile(r"https://(?!api\.)[a-z0-9][a-z0-9-]*\.trycloudflare\.com", re.I)

    def __init__(self, port, binary="cloudflared"):
        self.port, self.binary = port, binary
        self.url, self.proc, self._stopping = None, None, False

    def start(self, on_url, on_error):
        exe = find_cloudflared(self.binary)
        if not exe:
            on_error("Không tìm thấy cloudflared (đã cài chưa? hoặc ghi đường dẫn đầy đủ vào "
                     "\"tunnel_bin\" trong config.json)")
            return False
        threading.Thread(target=self._run, args=(exe, on_url, on_error), daemon=True).start()
        return True

    def _run(self, exe, on_url, on_error):
        cmd = [exe, "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:%d" % self.port]
        tail = deque(maxlen=6)
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         text=True, encoding="utf-8", errors="replace",
                                         creationflags=CREATE_NO_WINDOW)
            for line in self.proc.stdout:
                line = line.strip()
                if line:
                    tail.append(line)
                m = self.URL_RE.search(line)
                if m and not self.url:
                    self.url = m.group(0)
                    on_url(self.url)
            code = self.proc.wait()
        except Exception as e:
            if not self._stopping:
                on_error("Không chạy được cloudflared: %s" % e)
            return
        if not self._stopping:
            on_error("cloudflared đã dừng (mã %s). Link cũ không còn dùng được. %s"
                     % (code, " | ".join(list(tail)[-3:])))

    def stop(self):
        self._stopping = True
        p = self.proc
        if p and p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=3)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass


class RemoteAccess:
    def __init__(self, cfg, db, on_event):
        self.cfg, self.db, self.ev = cfg, db, on_event
        self.lock = threading.Lock()
        self.tunnel, self.t_state, self.t_url, self.t_msg = None, "off", None, ""   # off|starting|on|error
        self.ts_ip, self.ts_checking = None, False
        self.check_tailscale()

    # ---------------------------------------------------------------- Tailscale
    def check_tailscale(self):
        with self.lock:
            if self.ts_checking:
                return
            self.ts_checking = True

        def run():
            try:
                ip = tailscale_ip()
            except Exception:
                ip = None
            with self.lock:
                self.ts_ip, self.ts_checking = ip, False
        threading.Thread(target=run, daemon=True).start()

    # ---------------------------------------------------------------- Cloudflare
    def start_tunnel(self):
        with self.lock:
            if self.t_state in ("starting", "on"):
                return
            t = QuickTunnel(int(self.cfg["port"]), self.cfg.get("tunnel_bin") or "cloudflared")
            self.tunnel, self.t_url, self.t_msg, self.t_state = t, None, "", "starting"
        self.ev("Đang tạo link Internet qua Cloudflare Tunnel...", "info")

        def on_url(u):
            with self.lock:
                if t is not self.tunnel:        # bỏ qua tin của tunnel cũ đã tắt
                    return
                self.t_url, self.t_state = u, "on"
            self.ev("Link Internet (đổi mỗi lần bật lại): %s" % u, "ok")

        def on_err(m):
            with self.lock:
                if t is not self.tunnel:
                    return
                self.t_url, self.t_state, self.t_msg = None, "error", m
            self.ev("Cloudflare Tunnel: " + m, "alarm")
        t.start(on_url, on_err)

    def stop_tunnel(self):
        with self.lock:
            t, self.tunnel = self.tunnel, None
            self.t_url, self.t_state, self.t_msg = None, "off", ""
        if t:
            threading.Thread(target=t.stop, daemon=True).start()
        self.ev("Đã tắt Cloudflare Tunnel", "info")

    def shutdown(self):
        t = self.tunnel
        if t:
            t.stop()

    # ---------------------------------------------------------------- trạng thái cho web
    def status(self):
        port = int(self.cfg["port"])
        with self.lock:
            ts_ip, ts_chk = self.ts_ip, self.ts_checking
            tstate, turl, tmsg = self.t_state, self.t_url, self.t_msg
        lan = lan_ip()
        return dict(
            lan_url="http://%s:%d" % (lan, port),
            lan_ready=self.cfg["host"] in ("0.0.0.0", "::"),
            ts_url=("http://%s:%d" % (ts_ip, port)) if ts_ip else None,
            ts_checking=ts_chk,
            tunnel_state=tstate, tunnel_url=turl, tunnel_msg=tmsg,
            tunnel_autostart=bool(self.db.get_setting("tunnel_autostart", False)))
