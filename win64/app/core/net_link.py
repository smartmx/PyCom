# -*- coding: utf-8 -*-
"""网络连接：TCP 客户端 / TCP 服务端 / UDP（主线程事件驱动）。

IPv4/IPv6 纯单栈语义：本地绑定地址是哪个族就只收发哪个族（0.0.0.0=仅 IPv4
全部、::=仅 IPv6 全部、具体地址=仅该地址，链路本地 fe80::x%作用域 直接可用），
不做双栈。TCP 客户端连接前按所选本机地址预绑定（限定源网卡与协议族），族由
绑定与远程地址共同决定。"""
from __future__ import annotations

from PySide6.QtNetwork import (QTcpServer, QTcpSocket, QAbstractSocket,
                               QUdpSocket, QHostAddress)

from .link import Link


def _strip_brackets(host: str) -> str:
    """远程主机写法宽容：[::1] → ::1（用户常从浏览器/URL 复制带方括号的
    IPv6 地址，而 QHostAddress/QHostInfo 都不认方括号）。"""
    h = (host or "").strip()
    if len(h) >= 2 and h[0] == "[" and h[-1] == "]":
        return h[1:-1]
    return h


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
        self.sock.abort()       # 回到未绑定态，防重连路径上重复 bind 失败
        # 按所选本机地址预绑定（此前从不绑定，"本机 IP"下拉形同虚设——十九轮
        # F1）：具体地址=限定源地址/出口网卡；0.0.0.0 / ::=限定协议族，与其余
        # 两模式同语义。端口 0=系统分配临时端口（TCP 客户端无需配置本机端口）。
        local = QHostAddress(self.cfg.get("local_ip") or QHostAddress.AnyIPv4)
        if local.isNull():
            local = QHostAddress(QHostAddress.AnyIPv4)
        if not self.sock.bind(local, 0):
            self.state_changed.emit("error: 本机IP绑定失败(%s)"
                                    % self.sock.errorString())
            self._schedule()
            return False
        # IPv4/IPv6 字面量与主机名（含 AAAA）都交给 Qt 解析；族由绑定+远程决定
        self.sock.connectToHost(_strip_brackets(self.cfg["remote_ip"]),
                                int(self.cfg["remote_port"]))
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
        self.send_err_active = False          # 发送失败提示去重（定时发送不刷屏）；
                                              # 主窗口 _write 据此跳过通用提示（F3）

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
        host = QHostAddress(_strip_brackets(self.cfg["remote_ip"] or "127.0.0.1"))
        if host.isNull():
            return self._send_fail("远程IP无法解析(%s)"
                                   % (self.cfg["remote_ip"] or ""))
        n = self.sock.writeDatagram(data, host, int(self.cfg["remote_port"]))
        if n < 0:
            # 常见于族不匹配（本地绑 :: 只收发 IPv6，远程却是 IPv4 地址）等
            return self._send_fail(self.sock.errorString() or "发送失败")
        self._send_recover()
        if n > 0:
            self.tx_done.emit(int(n))
        return int(n)

    def _send_fail(self, reason: str) -> int:
        """发送失败经 state 报一次（红灯+通知），恢复成功后回 open——链路本身
        保持绑定（界面保持已连接/关闭，见主窗口 _on_state），去重避免定时发送
        刷屏；send_err_active 供主窗口 _write 门控通用提示。"""
        if not self.send_err_active:
            self.send_err_active = True
            self.state_changed.emit("error: %s" % reason)
        return 0

    def _send_recover(self) -> None:
        if self.send_err_active:
            self.send_err_active = False
            self.state_changed.emit("open")

    def is_open(self) -> bool:
        return self.sock.state() == QAbstractSocket.BoundState

    def describe(self) -> str:
        return ("UDP 本机IP=%s :%s → %s:%s"
                % (self.cfg.get("local_ip", "0.0.0.0"), self.cfg["local_port"],
                   self.cfg["remote_ip"], self.cfg["remote_port"]))
