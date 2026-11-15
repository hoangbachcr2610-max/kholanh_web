# -*- coding: utf-8 -*-
"""Modbus RTU (PC = Master) tự cài bằng pyserial + bộ giả lập để thử khi chưa có phần cứng.
Logic giữ nguyên từ kholanh.py bản Tkinter."""
import random
import struct
import threading
import time

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    serial = None


def crc16(data: bytes) -> int:
    """CRC16-Modbus: khởi tạo 0xFFFF, đa thức 0xA001."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


class ModbusError(Exception):
    pass


def list_ports():
    if serial is None:
        return []
    return [(p.device, p.description) for p in serial.tools.list_ports.comports()]


class ModbusRTU:
    def __init__(self):
        self.ser = None
        self.lock = threading.Lock()

    def open(self, port, baud, parity):
        if serial is None:
            raise ModbusError("Chưa cài pyserial (pip install pyserial)")
        self.ser = serial.Serial(port=port, baudrate=baud, bytesize=8, parity=parity,
                                 stopbits=1, timeout=0.6, write_timeout=1.0)

    def close(self):
        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
        except Exception:
            pass

    @property
    def is_open(self):
        return bool(self.ser and self.ser.is_open)

    def _request(self, payload: bytes) -> bytes:
        if not self.is_open:
            raise ModbusError("Cổng COM chưa mở")
        frame = payload + struct.pack("<H", crc16(payload))   # CRC: byte thấp trước
        with self.lock:
            self.ser.reset_input_buffer()
            self.ser.write(frame)
            self.ser.flush()
            head = self.ser.read(2)                            # [ID][Function]
            if len(head) < 2:
                raise ModbusError("Timeout: thiết bị không trả lời")
            func = head[1]
            if func & 0x80:
                rest = self.ser.read(3)
            elif func in (1, 2, 3, 4):
                bc = self.ser.read(1)
                if not bc:
                    raise ModbusError("Thiếu byte dữ liệu")
                rest = bc + self.ser.read(bc[0] + 2)
            else:
                rest = self.ser.read(6)
        resp = head + rest
        if func & 0x80:
            expected = 5
        elif func in (1, 2, 3, 4):
            expected = 5 + resp[2]
        else:
            expected = 8
        if len(resp) < expected:
            raise ModbusError("Thiếu byte dữ liệu")
        if crc16(resp[:-2]) != struct.unpack("<H", resp[-2:])[0]:
            raise ModbusError("Sai CRC")
        if resp[0] != payload[0]:
            raise ModbusError("Sai địa chỉ slave trong phản hồi")
        if func & 0x80:
            raise ModbusError("Thiết bị báo Exception code %d" % resp[2])
        return resp

    def read_registers(self, slave, func, addr, count):
        resp = self._request(struct.pack(">BBHH", slave, func, addr, count))
        if resp[2] != 2 * count:
            raise ModbusError("Sai độ dài dữ liệu")
        return list(struct.unpack(">%dH" % count, resp[3:3 + 2 * count]))

    def write_coil(self, slave, addr, on):
        """Function 05: bật = 0xFF00, tắt = 0x0000."""
        self._request(struct.pack(">BBHH", slave, 5, addr, 0xFF00 if on else 0x0000))


class SimulatedBus:
    """Giả lập cảm biến + relay."""
    is_open = True

    def __init__(self):
        self.temp, self.hum = 9.0, 75.0
        self.coils = [False, False]

    def close(self):
        pass

    def read_registers(self, slave, func, addr, count):
        time.sleep(0.03)
        self.temp += 0.05 - (0.12 if self.coils[0] else 0) + random.uniform(-0.03, 0.03)
        self.hum += 0.15 - (0.40 if self.coils[1] else 0) + random.uniform(-0.1, 0.1)
        self.hum = min(99.0, max(30.0, self.hum))
        return [int(round(self.hum * 10)), int(round(self.temp * 10)) & 0xFFFF]

    def write_coil(self, slave, addr, on):
        time.sleep(0.02)
        self.coils[addr] = bool(on)
