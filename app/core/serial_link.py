# -*- coding: utf-8 -*-
"""串口连接：QSerialPort 主线程事件驱动 + 拔出检测 + 自动重连。"""
from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtSerialPort import QSerialPort, QSerialPortInfo

from .link import Link

STOPBITS_MAP = {1: QSerialPort.OneStop, 2: QSerialPort.TwoStop, 1.5: QSerialPort.OneAndHalfStop}
DATABITS_MAP = {5: QSerialPort.Data5, 6: QSerialPort.Data6,
                7: QSerialPort.Data7, 8: QSerialPort.Data8}
PARITY_MAP = {"none": QSerialPort.NoParity, "even": QSerialPort.EvenParity,
              "odd": QSerialPort.OddParity, "mark": QSerialPort.MarkParity,
              "space": QSerialPort.SpaceParity}
FLOW_MAP = {"none": QSerialPort.NoFlowControl, "rtscts": QSerialPort.HardwareControl,
            "xonxoff": QSerialPort.SoftwareControl}


class SerialLink(Link):
    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.sp = QSerialPort(self)
        self.sp.readyRead.connect(self._on_rx)
        self.sp.errorOccurred.connect(self._on_error)
        self._want_open = False
        self._recon = QTimer(self)
        self._recon.setSingleShot(True)
        self._recon.timeout.connect(self._try_reopen)

    # ---- 生命周期 ----
    def open(self) -> bool:
        self._want_open = True
        return self._try_reopen()

    def _try_reopen(self) -> bool:
        if not self._want_open:
            return False
        if self.sp.isOpen():
            return True
        c = self.cfg
        self.sp.setPortName(c["port"])
        self.sp.setBaudRate(int(c["baud"]))
        self.sp.setDataBits(DATABITS_MAP.get(c["databits"], QSerialPort.Data8))
        self.sp.setStopBits(STOPBITS_MAP.get(c["stopbits"], QSerialPort.OneStop))
        self.sp.setParity(PARITY_MAP.get(c["parity"], QSerialPort.NoParity))
        self.sp.setFlowControl(FLOW_MAP.get(c["flow"], QSerialPort.NoFlowControl))
        ok = self.sp.open(QSerialPort.ReadWrite)
        if ok:
            self.sp.setDataTerminalReady(bool(c.get("dtr", True)))
            self.sp.setRequestToSend(bool(c.get("rts", False)))
            self.state_changed.emit("open")
        else:
            self.state_changed.emit("error: 打开 %s 失败(%s)" %
                                    (c["port"], self.sp.errorString()))
            self._schedule_reconnect()
        return ok

    def close(self) -> None:
        self._want_open = False
        self._recon.stop()
        if self.sp.isOpen():
            self.sp.close()
        self.state_changed.emit("closed")

    def _schedule_reconnect(self) -> None:
        if not self.cfg.get("port"):
            # 无端口（未选择/不存在）时不再重连，避免"错误→重连中"死循环
            self.state_changed.emit("closed")
            return
        if self._want_open and self.auto_reconnect:
            self._recon.start(self.reconnect_ms)
            self.state_changed.emit("reconnecting")

    def _on_error(self, err) -> None:
        if err in (QSerialPort.ResourceError, QSerialPort.PermissionError,
                   QSerialPort.DeviceNotFoundError, QSerialPort.OpenError):
            # 典型场景：USB 串口被拔出
            if self.sp.isOpen():
                self.sp.close()
            self.state_changed.emit("error: 串口断开(%s)" % self.sp.errorString())
            self._schedule_reconnect()

    # ---- 数据 ----
    def _on_rx(self) -> None:
        data = bytes(self.sp.readAll())
        while self.sp.waitForReadyRead(0):      # 排空
            data += self.sp.readAll()
        if data:
            self.rx.emit(data)

    def write(self, data: bytes) -> int:
        if not self.sp.isOpen():
            return 0
        # 不调用 sp.flush()：其会阻塞等待底层写出，慢波特率/大文件时卡 UI；
        # QSerialPort.write 本身经事件循环异步发出
        n = self.sp.write(data)
        if n > 0:
            self.tx_done.emit(int(n))
        return int(n)

    def is_open(self) -> bool:
        return self.sp.isOpen()

    def describe(self) -> str:
        c = self.cfg
        return "%s %d,%d,%g,%s,%s" % (c["port"], c["baud"], c["databits"],
                                      c["stopbits"], c["parity"][:4].upper(),
                                      "NONE" if c["flow"] == "none" else c["flow"])

    # ---- 工具 ----
    @staticmethod
    def list_ports() -> list[tuple[str, str]]:
        """返回 [(端口名, 设备描述)]，描述可能为空字符串。"""
        return [(p.portName(), p.description() or "")
                for p in QSerialPortInfo.availablePorts()]
