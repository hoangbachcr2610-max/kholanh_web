# -*- coding: utf-8 -*-
"""Đọc cấu hình từ config.json (cùng thư mục). Thiếu khóa nào thì dùng giá trị mặc định."""
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

DEFAULTS = {
    # --- Modbus: chỉ là giá trị BAN ĐẦU, sau đó chỉnh trên web ở mục 'Thiết bị' ---
    "simulate": False,
    "serial_port": "COM6",
    "baud": 9600,
    "parity": "N",              # N / E / O
    "sensor_id": 1,
    "relay_id": 255,
    "function": 3,              # 3 hoặc 4
    "hum_first": True,          # True: thanh ghi đầu là độ ẩm, thanh ghi sau là nhiệt độ
    "poll_interval": 1.0,
    # --- Điều khiển ---
    "debounce": 3,              # số mẫu liên tiếp trước khi báo động / kích hoạt failsafe
    "min_switch": 10.0,         # giây tối thiểu giữa 2 lần đóng/cắt 1 quạt
    # --- Lưu trữ ---
    "log_interval": 10.0,
    "retention_days": 365,
    # --- Web ---
    "host": "0.0.0.0",          # "0.0.0.0": vào được từ LAN/Tailscale/Cloudflare. "127.0.0.1": chỉ máy này
    "port": 8000,
    "secure_cookie": False,     # để False: cookie tự gắn cờ Secure khi vào bằng HTTPS
    "tunnel_bin": "cloudflared",  # hoặc đường dẫn đầy đủ tới cloudflared.exe
    "session_hours": 12,
}


def load():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    os.makedirs(DATA_DIR, exist_ok=True)
    return cfg
