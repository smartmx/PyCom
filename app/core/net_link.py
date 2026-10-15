# -*- coding: utf-8 -*-
"""网络连接：TCP 客户端 / TCP 服务端 / UDP（主线程事件驱动）。"""
from __future__ import annotations

from PySide6.QtNetwork import (QTcpServer, QTcpSocket, QAbstractSocket,
                               QUdpSocket, QHostAddress)

from .link import Link


class TcpClientLink(Link):
    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.sock = QTcpSocket(self)
        self.sock.readyRead.connect(self._on_rx)
        self.sock.connected.connect(lambda: self.state_changed.emit("open"))
        self.sock.disconnected.connect(self._on_disc)
        self.sock.errorOccurred.connect(self._on_err)
        self._want_open = False
        from PySide6.QtCore import QTimer
        self._recon = QTimer(self)
        self._recon.setSingleShot(True)
        self._recon.timeout.connect(self.open)

    def open(self) -> bool:
        self._want_open = True
        if self.sock.state() == QAbstractSocket.ConnectedState:
            return True
        self.sock.connectToHost(self.cfg["remote_ip"], int(self.cfg["remote_port"]))
        return False        # 异步连接，结果经信号通知

    def close(self) -> None:
        self._want_open = False
        self._recon.stop()
        self.sock.abort()
        self.state_changed.emit("closed")

    def _on_disc(self) -> None:
        self.state_changed.emit("error: 连接断开")
        self._schedule()

    def _on_err(self, err) -> None:
        if err != QAbstractSocket.RemoteHostClosedError:
            self.state_changed.emit("error: %s" % self.sock.errorString())
        self._schedule()

    def _schedule(self) -> None:
        if self._want_open and self.auto_reconnect:
            self._recon.start(self.reconnect_ms)
            self.state_changed.emit("reconnecting")

    def _on_rx(self) -> None:
        data = bytes(self.sock.readAll())
        if data:
            self.rx.emit(data)

    def write(self, data: bytes) -> int:
        if self.sock.state() != QAbstractSocket.ConnectedState:
            return 0
        n = self.sock.write(data)
        if n > 0:
            self.tx_done.emit(int(n))
        return int(n)

    def is_open(self) -> bool:
        return self.sock.state() == QAbstractSocket.ConnectedState

    def describe(self) -> str:
        return ("TCPClient 本机IP=%s → %s:%s"
                % (self.cfg.get("local_ip", "127.0.0.1"),
                   self.cfg["remote_ip"], self.cfg["remote_port"]))


class TcpServerLink(Link):
    """监听本地端口；支持多客户端（收合并、发广播）。"""

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.srv = QTcpServer(self)
        self.clients: list[QTcpSocket] = []
        self._pending: dict = {}          # 每客户端待发余量（慢客户端不拖累别人）
        self._pending_max = 256 * 1024    # 单客户端队列上限，超限拒绝推进（防无界内存）

    def open(self) -> bool:
        addr = QHostAddress(self.cfg.get("local_ip") or QHostAddress.AnyIPv4)
        if addr == QHostAddress(QHostAddress.AnyIPv4) or addr.isNull():
            addr = QHostAddress(QHostAddress.AnyIPv4)
        ok = self.srv.listen(addr, int(self.cfg["local_port"]))
        if ok:
            self.srv.newConnection.connect(self._on_new)
            self.state_changed.emit("open")
        else:
            self.state_changed.emit("error: 监听失败(%s)" % self.srv.errorString())
        return ok

    def close(self) -> None:
        for c in self.clients:
            c.disconnectFromHost()
        self.clients.clear()
        self._pending.clear()
        if self.srv.isListening():
            self.srv.close()
        self.state_changed.emit("closed")

    def _on_new(self) -> None:
        while self.srv.hasPendingConnections():
            sock = self.srv.nextPendingConnection()
            sock.readyRead.connect(lambda s=sock: self._on_rx(s))
            sock.disconnected.connect(lambda s=sock: self._drop(s))
            sock.bytesWritten.connect(lambda _n, s=sock: self._flush_pending(s))
            self.clients.append(sock)

    def _drop(self, sock) -> None:
        self._pending.pop(sock, None)
        if sock in self.clients:
            self.clients.remove(sock)
        sock.deleteLater()                  # 释放断开的客户端对象，防积累

    def _on_rx(self, sock) -> None:
        data = bytes(sock.readAll())
        if data:
            self.rx.emit(data)

    def _flush_pending(self, sock) -> None:
        """客户端缓冲排空后，把其待发余量继续写出并计入 TX。"""
        pend = self._pending.get(sock)
        if not pend or sock.state() != QAbstractSocket.ConnectedState:
            return
        n = sock.write(bytes(pend))
        if n > 0:
            del pend[:n]
            self.tx_done.emit(int(n))
        if not pend:
            self._pending.pop(sock, None)

    def write(self, data: bytes) -> int:
        """广播给所有客户端，部分写入的余量进入该客户端独立队列（连接缓冲排空
        后自动续发）。返回"源数据可安全推进的字节数"：数据要么已写出、要么已入
        队，因此推进 len(data)；无客户端或队列溢出（客户端长时间不收）时返回 0。"""
        data = bytes(data)
        clients = [c for c in self.clients
                   if c.state() == QAbstractSocket.ConnectedState]
        if not clients:
            return 0
        # 事务式容量判定：
        # 1) 先排空各客户端旧余量（可恢复场景不应误判失败）
        # 2) 任一客户端"余量 + data"仍超上限，或单块 data 本身超过上限
        #    → 整体拒绝（返回 0），不产生部分广播；调用方（FileSender）不得重试
        for c in clients:
            self._flush_pending(c)
        for c in clients:
            pend = self._pending.get(c)
            if (pend and len(pend) + len(data) > self._pending_max) \
                    or len(data) > self._pending_max:
                return 0
        wrote = 0
        for c in clients:
            self._flush_pending(c)          # 先尝试清掉旧余量
            n = c.write(data) if not self._pending.get(c) else 0
            if n < 0:
                n = 0
            wrote += n
            if n < len(data):
                pend = self._pending.setdefault(c, bytearray())
                pend += data[n:]
        if wrote > 0:
            self.tx_done.emit(wrote)
        return len(data)

    def is_open(self) -> bool:
        return self.srv.isListening()

    def describe(self) -> str:
        n = len(self.clients)
        return ("TCPServer 本机IP=%s :%s (%d 客户端%s)"
                % (self.cfg.get("local_ip", "0.0.0.0"), self.cfg["local_port"],
                   n, "" if n == 1 else "s"))


class UdpLink(Link):
    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.sock = QUdpSocket(self)
        self.sock.readyRead.connect(self._on_rx)

    def open(self) -> bool:
        addr = QHostAddress(self.cfg.get("local_ip") or QHostAddress.AnyIPv4)
        if addr == QHostAddress(QHostAddress.AnyIPv4) or addr.isNull():
            addr = QHostAddress(QHostAddress.AnyIPv4)
        ok = self.sock.bind(addr, int(self.cfg["local_port"]))
        if ok:
            self.state_changed.emit("open")
        else:
            self.state_changed.emit("error: 绑定失败(%s)" % self.sock.errorString())
        return ok

    def close(self) -> None:
        self.sock.close()
        self.state_changed.emit("closed")

    def _on_rx(self) -> None:
        # 逐 datagram 发射，保留数据报边界
        while self.sock.hasPendingDatagrams():
            dg = self.sock.receiveDatagram(self.sock.pendingDatagramSize())
            if dg.data():
                self.rx.emit(bytes(dg.data()))

    def write(self, data: bytes) -> int:
        host = QHostAddress(self.cfg["remote_ip"] or "127.0.0.1")
        n = self.sock.writeDatagram(data, host, int(self.cfg["remote_port"]))
        if n > 0:
            self.tx_done.emit(int(n))
        return int(n)

    def is_open(self) -> bool:
        return self.sock.state() == QAbstractSocket.BoundState

    def describe(self) -> str:
        return ("UDP 本机IP=%s :%s → %s:%s"
                % (self.cfg.get("local_ip", "0.0.0.0"), self.cfg["local_port"],
                   self.cfg["remote_ip"], self.cfg["remote_port"]))
