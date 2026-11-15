# -*- coding: utf-8 -*-
"""Bộ điều khiển kho lạnh chạy nền (không giao diện). Thay thế Worker + logic trong class App của bản Tkinter.

- MỘT luồng duy nhất chạm vào cổng COM (đọc cảm biến, ghi relay, mở/đóng cổng) -> không bao giờ tranh chấp.
- Web (Flask) chỉ gọi các hàm public bên dưới; lệnh đi qua hàng đợi.
- Ngưỡng / chế độ / failsafe / thông số kết nối được lưu trong SQLite nên khởi động lại không bị mất.
"""
import logging
import queue
import threading
import time
from collections import deque

from modbus import ModbusError, ModbusRTU, SimulatedBus

log = logging.getLogger("kholanh")

LIMITS = {"t_high": (-50, 100), "t_low": (-50, 100), "h_high": (0, 100),
          "h_low": (0, 100), "t_hyst": (0, 20), "h_hyst": (0, 50)}
FAILSAFE_OPTIONS = ("Giữ nguyên", "Bật cả hai quạt", "Tắt cả hai quạt")
DEFAULT_THR = {"t_high": 8.0, "t_low": 0.0, "h_high": 85.0, "h_low": 60.0,
               "t_hyst": 1.0, "h_hyst": 5.0}
ALARM_LOST = "Mất kết nối cảm biến"
BAUDS = (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200)


def conn_defaults(cfg):
    """Giá trị ban đầu của bảng 'Kết nối Modbus RTU' (lấy từ config.json, sau đó chỉnh trên web)."""
    return {"simulate": bool(cfg["simulate"]), "port": cfg["serial_port"], "baud": int(cfg["baud"]),
            "parity": cfg["parity"], "sensor_id": int(cfg["sensor_id"]), "relay_id": int(cfg["relay_id"]),
            "function": int(cfg["function"]), "hum_first": bool(cfg["hum_first"]),
            "interval": float(cfg["poll_interval"])}


def validate_conn(d):
    try:
        out = {"simulate": bool(d.get("simulate")),
               "port": str(d.get("port") or "").split(" - ")[0].strip(),   # "COM6 - mô tả" -> "COM6"
               "baud": int(d.get("baud")), "parity": str(d.get("parity")),
               "sensor_id": int(d.get("sensor_id")), "relay_id": int(d.get("relay_id")),
               "function": int(d.get("function")), "hum_first": bool(d.get("hum_first")),
               "interval": float(d.get("interval"))}
    except (TypeError, ValueError):
        raise ValueError("Thông số kết nối không hợp lệ")
    if not out["simulate"] and not out["port"]:
        raise ValueError("Hãy chọn hoặc nhập cổng COM của USB-RS485 (ví dụ COM6)")
    if out["baud"] not in BAUDS:
        raise ValueError("Baud không hợp lệ")
    if out["parity"] not in ("N", "E", "O"):
        raise ValueError("Parity không hợp lệ")
    if out["function"] not in (3, 4):
        raise ValueError("Function đọc phải là 03 hoặc 04")
    for k, name in (("sensor_id", "ID cảm biến"), ("relay_id", "ID relay")):
        if not 1 <= out[k] <= 255:
            raise ValueError("%s phải từ 1 đến 255" % name)
    if out["sensor_id"] == out["relay_id"]:
        raise ValueError("ID cảm biến và ID relay phải khác nhau")
    if not 0.3 <= out["interval"] <= 60:
        raise ValueError("Chu kỳ đọc phải từ 0.3 đến 60 giây")
    return out


class Controller(threading.Thread):
    def __init__(self, cfg, db):
        super().__init__(daemon=True, name="controller")
        self.cfg, self.db = cfg, db
        self.stop_evt = threading.Event()
        self.cmdq = queue.Queue()
        self.lock = threading.RLock()

        # --- trạng thái (đọc/ghi dưới self.lock) ---
        self.thr = dict(DEFAULT_THR, **db.get_setting("thr", {}))
        self.mode = db.get_setting("mode", "auto")
        self.failsafe = db.get_setting("failsafe", FAILSAFE_OPTIONS[0])
        self.conn = dict(conn_defaults(cfg), **db.get_setting("conn", {}))
        self.want_conn = bool(db.get_setting("conn_enabled", False))   # người dùng muốn kết nối (tự nối lại khi mất)
        if cfg.get("cli_simulate"):                                    # python run_server.py --simulate
            self.conn["simulate"], self.want_conn = True, True
        self.conn_desc, self.conn_error = "", ""
        self.fan_state = [None, None]            # None = chưa biết -> lần đầu ở chế độ Auto sẽ ép đồng bộ
        self.last_switch = [0.0, 0.0]
        self.alarm_counts, self.alarm_active = {}, {}
        self.err_count = 0
        self.last_data = None                    # (ts, temp, hum)
        self.connected = False
        self.events = deque(maxlen=60)
        self.hist_temp, self.hist_hum = deque(maxlen=150), deque(maxlen=150)
        self._last_db_write = 0.0
        self._last_purge = 0.0
        self._last_reopen = 0.0
        self.bus = None

        for ts, level, msg in db.recent_events(60):
            self.events.append((time.strftime("%H:%M:%S", time.localtime(ts)), msg, level))
        for r in db.readings_since(time.time() - 3600, max_points=150):
            self.hist_temp.append(r[1])
            self.hist_hum.append(r[2])

    # ================================================================ sự kiện
    def event(self, msg, level="info"):
        log.log(logging.WARNING if level == "alarm" else logging.INFO, msg)
        now = time.time()
        with self.lock:
            self.events.append((time.strftime("%H:%M:%S", time.localtime(now)), msg, level))
        try:
            self.db.add_event(now, level, msg)
        except Exception:
            log.exception("Không ghi được sự kiện vào CSDL")

    # ================================================================ kết nối
    def _close_bus(self):
        if self.bus:
            try:
                self.bus.close()
            except Exception:
                pass
        self.bus = None
        self.connected = False

    def _connect(self, reset=True):
        """Mở cổng theo self.conn. Chạy TRONG luồng điều khiển. Ném lỗi nếu không mở được."""
        self._close_bus()
        with self.lock:
            c = dict(self.conn)
        if c["simulate"]:
            bus, desc = SimulatedBus(), "Chế độ mô phỏng"
        else:
            bus = ModbusRTU()
            bus.open(c["port"], c["baud"], c["parity"])
            desc = "%s @ %d-8-%s-1" % (c["port"], c["baud"], c["parity"])
        with self.lock:
            self.bus, self.connected = bus, True
            self.conn_desc, self.conn_error, self.err_count = desc, "", 0
            if reset:
                self.fan_state = [None, None]       # lần đầu ở chế độ Auto sẽ ép đồng bộ relay
        self.event("Kết nối: %s (ID cảm biến=%d, ID relay=%d)" % (desc, c["sensor_id"], c["relay_id"]),
                   "alarm" if c["simulate"] else "ok")

    def _disconnect(self):
        self._close_bus()
        with self.lock:
            self.conn_desc, self.conn_error, self.err_count = "", "", 0
            self.last_data = None
            self.fan_state = [None, None]
            self.alarm_active.clear()
            self.alarm_counts.clear()
        self.event("Đã ngắt kết nối")

    def _set_conn_error(self, msg):
        if msg != self.conn_error:                  # chỉ ghi nhật ký khi lỗi đổi, tránh tràn log
            self.conn_error = msg
            self.event("Không mở được cổng COM: %s (sẽ tự thử lại)" % msg, "alarm")

    def _try_reopen(self):
        """Tự nối lại khi rút/cắm USB-RS485 (thử mỗi 10 giây)."""
        if time.time() - self._last_reopen < 10:
            return
        self._last_reopen = time.time()
        try:
            self._connect(reset=False)
        except Exception as e:
            self._set_conn_error(str(e))

    # ================================================================ vòng chạy
    def run(self):
        if self.want_conn:
            try:
                self._connect()
            except Exception as e:
                self._set_conn_error(str(e))
        next_poll = 0.0
        while not self.stop_evt.is_set():
            try:
                cmd = self.cmdq.get(timeout=0.05)
                self._do_cmd(cmd)
            except queue.Empty:
                pass
            except Exception:
                log.exception("Lỗi xử lý lệnh")
            if time.time() >= next_poll:
                try:
                    self._poll()
                except Exception:
                    log.exception("Lỗi vòng đọc cảm biến")
                with self.lock:
                    iv = float(self.conn["interval"])
                next_poll = time.time() + max(0.3, iv)
        self._close_bus()

    def stop(self):
        self.stop_evt.set()
        self.join(timeout=3)

    # ================================================================ đọc cảm biến
    def _poll(self):
        if not self.want_conn:
            return                                   # chưa kết nối (hoặc đã ngắt): đứng yên, không báo lỗi
        with self.lock:
            c = dict(self.conn)
        if not self.connected or self.bus is None:
            self._try_reopen()
            if not self.connected:
                self._on_sensor_err(self.conn_error or "Chưa mở được cổng COM")
                return
        last = None
        for _ in range(3):
            try:
                regs = self.bus.read_registers(c["sensor_id"], c["function"], 0, 2)
                hi, ti = (0, 1) if c["hum_first"] else (1, 0)
                hum = regs[hi] / 10.0
                traw = regs[ti]
                if traw >= 0x8000:                   # nhiệt độ âm (số bù 2)
                    traw -= 0x10000
                self._on_sensor(time.time(), hum, traw / 10.0)
                return
            except Exception as e:
                last = e
                time.sleep(0.1)
        self._on_sensor_err(str(last))
        # lỗi cấp cổng (rút USB...) khác lỗi thiết bị không trả lời (Timeout/CRC): chỉ lỗi cổng mới mở lại cổng
        if self.err_count >= 3 and not c["simulate"] and \
                (not isinstance(last, ModbusError) or not self.bus.is_open):
            self._try_reopen()

    def _on_sensor(self, ts, hum, temp):
        with self.lock:
            self.err_count = 0
            self.last_data = (ts, temp, hum)
            thr, n = dict(self.thr), int(self.cfg["debounce"])
        self._set_alarm(ALARM_LOST, False)

        conds = {"Nhiệt độ cao": temp > thr["t_high"], "Nhiệt độ thấp": temp < thr["t_low"],
                 "Độ ẩm cao": hum > thr["h_high"], "Độ ẩm thấp": hum < thr["h_low"]}
        for name, cond in conds.items():
            with self.lock:
                self.alarm_counts[name] = self.alarm_counts.get(name, 0) + 1 if cond else 0
                active = self.alarm_counts[name] >= n
            self._update_alarm(name, active, temp if "Nhiệt" in name else hum)

        with self.lock:
            auto = self.mode == "auto"
            self.hist_temp.append(temp)
            self.hist_hum.append(hum)
        if auto:
            self._auto_control(temp, hum, thr)
        if ts - self._last_db_write >= float(self.cfg["log_interval"]):
            self._last_db_write = ts
            with self.lock:
                f = [bool(x) for x in self.fan_state]
                alarms = "; ".join(k for k, v in self.alarm_active.items() if v)
            try:
                self.db.add_reading(ts, temp, hum, f[0], f[1], alarms)
            except Exception:
                log.exception("Không ghi được số liệu")
        if ts - self._last_purge > 86400:
            self._last_purge = ts
            try:
                self.db.purge_old(int(self.cfg["retention_days"]))
            except Exception:
                log.exception("Không dọn được số liệu cũ")

    def _on_sensor_err(self, msg):
        with self.lock:
            self.err_count += 1
            cnt, mode, fs = self.err_count, self.mode, self.failsafe
        if cnt == 1 or cnt % 10 == 0:
            self.event("Lỗi đọc cảm biến (%d lần liên tiếp): %s" % (cnt, msg), "alarm")
        if cnt >= int(self.cfg["debounce"]):
            if self._set_alarm(ALARM_LOST, True) and mode == "auto":
                if fs == "Bật cả hai quạt":
                    self._relay(0, True); self._relay(1, True)
                elif fs == "Tắt cả hai quạt":
                    self._relay(0, False); self._relay(1, False)

    # ================================================================ báo động
    def _set_alarm(self, name, on):
        """Trả về True nếu báo động VỪA mới bật."""
        with self.lock:
            was = self.alarm_active.get(name, False)
            self.alarm_active[name] = on
        if on and not was:
            self.event("CẢNH BÁO: " + name, "alarm")
            return True
        if was and not on:
            self.event("Hết cảnh báo: " + name, "ok")
        return False

    def _update_alarm(self, name, active, value):
        with self.lock:
            was = self.alarm_active.get(name, False)
            self.alarm_active[name] = active
        if active and not was:
            self.event("CẢNH BÁO: %s (giá trị đo = %.1f)" % (name, value), "alarm")
        elif was and not active:
            self.event("Hết cảnh báo: " + name, "ok")

    # ================================================================ điều khiển quạt
    def _auto_control(self, temp, hum, thr):
        """Quạt 1 theo nhiệt độ, quạt 2 theo độ ẩm, có trễ (hysteresis)."""
        with self.lock:
            cur = [bool(s) for s in self.fan_state]
            known = list(self.fan_state)
            last = list(self.last_switch)
        want = list(cur)
        if temp > thr["t_high"]:
            want[0] = True
        elif temp < thr["t_high"] - thr["t_hyst"]:
            want[0] = False
        if hum > thr["h_high"]:
            want[1] = True
        elif hum < thr["h_high"] - thr["h_hyst"]:
            want[1] = False
        for ch in (0, 1):
            # known[ch] is None -> chưa đồng bộ với relay thật, ép ghi ngay lần đầu
            if (known[ch] is None or want[ch] != cur[ch]) and \
                    time.time() - last[ch] >= float(self.cfg["min_switch"]):
                self._relay(ch, want[ch])

    def _relay(self, ch, on):
        err = None
        if not self.connected or self.bus is None:
            self.event("Lỗi điều khiển quạt %d: chưa kết nối thiết bị" % (ch + 1), "alarm")
            return False
        with self.lock:
            rid = self.conn["relay_id"]
        for _ in range(3):
            try:
                self.bus.write_coil(rid, ch, on)
                with self.lock:
                    self.fan_state[ch] = on
                    self.last_switch[ch] = time.time()
                self.event("Quạt %d: %s" % (ch + 1, "BẬT" if on else "TẮT"), "ok" if on else "info")
                return True
            except Exception as e:
                err = e
                time.sleep(0.1)
        self.event("Lỗi điều khiển quạt %d: %s" % (ch + 1, err), "alarm")
        return False

    def _do_cmd(self, cmd):
        if cmd[0] == "relay":
            self._relay(cmd[1], cmd[2])
        elif cmd[0] in ("connect", "disconnect"):
            _, done, box = cmd
            try:
                self._connect() if cmd[0] == "connect" else self._disconnect()
            except Exception as e:
                box["err"] = str(e)
            finally:
                done.set()

    def _call(self, name, timeout=12):
        """Nhờ luồng điều khiển (luồng duy nhất được chạm cổng COM) làm việc và chờ kết quả."""
        done, box = threading.Event(), {}
        self.cmdq.put((name, done, box))
        if not done.wait(timeout):
            raise ValueError("Bộ điều khiển đang bận, thử lại sau vài giây")
        if box.get("err"):
            raise ValueError(box["err"])

    # ================================================================ API kết nối cho web
    def get_conn(self):
        with self.lock:
            return dict(conn=dict(self.conn), want=self.want_conn, connected=self.connected,
                        desc=self.conn_desc, error=self.conn_error)

    def _save_conn(self, data):
        new = validate_conn(data)
        with self.lock:
            self.conn = new
        self.db.set_setting("conn", new)

    def connect(self, data, who):
        self._save_conn(data)
        with self.lock:
            self.want_conn = True
        self.db.set_setting("conn_enabled", True)
        try:
            self._call("connect")
        except ValueError as e:
            with self.lock:
                self.want_conn = False                # kết nối thủ công thất bại: không tự thử lại âm thầm
            self.db.set_setting("conn_enabled", False)
            self.event("%s: kết nối thất bại: %s" % (who, e), "alarm")
            raise ValueError("Không mở được cổng: %s" % e)

    def disconnect(self, who):
        with self.lock:
            self.want_conn = False
        self.db.set_setting("conn_enabled", False)
        self._call("disconnect")
        self.event("%s: ngắt kết nối" % who)

    def apply_conn(self, data, who):
        """Lưu thông số mới; nếu đang kết nối thì nối lại ngay với thông số mới."""
        self._save_conn(data)
        self.event("%s: cập nhật thông số kết nối" % who, "ok")
        if self.want_conn:
            self._call("connect")

    # ================================================================ API điều khiển cho web
    def set_mode(self, mode, who):
        if mode not in ("auto", "manual"):
            raise ValueError("Chế độ không hợp lệ")
        with self.lock:
            self.mode = mode
        self.db.set_setting("mode", mode)
        self.event("%s: chuyển chế độ %s" % (who, "TỰ ĐỘNG" if mode == "auto" else "THỦ CÔNG"), "info")

    def set_fan(self, ch, on, who):
        if ch not in (0, 1):
            raise ValueError("Quạt không hợp lệ")
        with self.lock:
            if self.mode != "manual":
                raise ValueError("Đang ở chế độ Tự động, hãy chuyển sang Thủ công")
        if not self.connected:
            raise ValueError("Chưa kết nối thiết bị")
        self.event("%s: yêu cầu %s quạt %d" % (who, "BẬT" if on else "TẮT", ch + 1))
        self.cmdq.put(("relay", ch, bool(on)))

    def set_failsafe(self, value, who):
        if value not in FAILSAFE_OPTIONS:
            raise ValueError("Giá trị không hợp lệ")
        with self.lock:
            self.failsafe = value
        self.db.set_setting("failsafe", value)
        self.event("%s: khi mất cảm biến → %s" % (who, value), "ok")

    def set_thresholds(self, data, who):
        new = dict(self.thr)
        for k, v in data.items():
            if k not in LIMITS:
                continue
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise ValueError("Giá trị %s không phải số" % k)
            lo, hi = LIMITS[k]
            if not (lo <= v <= hi):
                raise ValueError("%s phải nằm trong khoảng %s đến %s" % (k, lo, hi))
            new[k] = v
        if new["t_low"] >= new["t_high"]:
            raise ValueError("Nhiệt độ thấp phải nhỏ hơn nhiệt độ cao")
        if new["h_low"] >= new["h_high"]:
            raise ValueError("Độ ẩm thấp phải nhỏ hơn độ ẩm cao")
        with self.lock:
            self.thr = new
        self.db.set_setting("thr", new)
        self.event("%s: cập nhật ngưỡng %s" % (who, ", ".join(
            "%s=%g" % (k, new[k]) for k in LIMITS)), "ok")

    def snapshot(self):
        with self.lock:
            last = self.last_data
            return dict(
                connected=self.connected, sim=bool(self.connected and self.conn["simulate"]),
                want_conn=self.want_conn, conn_desc=self.conn_desc, conn_error=self.conn_error,
                temp=None if not last else round(last[1], 1),
                hum=None if not last else round(last[2], 1),
                age=None if not last else round(time.time() - last[0], 1),
                mode=self.mode, failsafe=self.failsafe,
                fans=[bool(x) for x in self.fan_state],
                alarms=[k for k, v in self.alarm_active.items() if v],
                thr=dict(self.thr),
                hist=dict(temp=list(self.hist_temp), hum=list(self.hist_hum)),
                events=[list(e) for e in list(self.events)[-20:]])
