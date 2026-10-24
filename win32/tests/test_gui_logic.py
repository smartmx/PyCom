# -*- coding: utf-8 -*-
"""需要 Qt 环境的回归测试：txfile 契约、终端按键、多条发送循环、日志游标、TCP 广播。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide2.QtCore import QPoint, Qt
from PySide2.QtTest import QTest
from PySide2.QtWidgets import QApplication

from app.core.settings import DEFAULTS, Settings
from app.core.txfile import FileSender
from app.ui.mainwindow import MainWindow

app = QApplication.instance() or QApplication([])


class _ClipStub:
    """系统剪贴板桩：真实剪贴板可能被其他进程长期占用（Qt OpenClipboard
    持续失败，2026-10-19 实测），剪贴断言一律走桩保证确定性。"""

    def __init__(self):
        self._text = ""

    def setText(self, t):
        self._text = t

    def text(self):
        return self._text


class _QAppStub:
    def __init__(self, clip):
        self._clip = clip

    def clipboard(self):
        return self._clip

# PySide2 的 QtTest 没有 qWait 静态方法（PySide6 才有），用事件循环轮询等价替代
if not hasattr(QTest, "qWait"):
    import time as _time

    def _qwait(ms):
        end = _time.monotonic() + ms / 1000.0
        while _time.monotonic() < end:
            app.processEvents()
            _time.sleep(0.002)

    QTest.qWait = _qwait


# ---- P0-4: 终端按键映射（Qt5/Qt6 键值一致） ----
def test_terminal_keymap():
    from app.core.terminal import key_to_bytes

    class E:
        def __init__(self, k, t=""):
            self._k, self._t = k, t

        def key(self):
            return self._k

        def text(self):
            return self._t

    assert key_to_bytes(E(int(Qt.Key_Up)), "gbk") == b"\x1b[A"
    assert key_to_bytes(E(int(Qt.Key_Down)), "gbk") == b"\x1b[B"
    assert key_to_bytes(E(int(Qt.Key_Right)), "gbk") == b"\x1b[C"
    assert key_to_bytes(E(int(Qt.Key_Left)), "gbk") == b"\x1b[D"
    assert key_to_bytes(E(int(Qt.Key_Home)), "gbk") == b"\x1b[H"
    assert key_to_bytes(E(int(Qt.Key_End)), "gbk") == b"\x1b[F"
    assert key_to_bytes(E(int(Qt.Key_PageUp)), "gbk") == b"\x1b[5~"
    assert key_to_bytes(E(int(Qt.Key_PageDown)), "gbk") == b"\x1b[6~"
    assert key_to_bytes(E(int(Qt.Key_Delete)), "gbk") == b"\x1b[3~"
    assert key_to_bytes(E(int(Qt.Key_Backspace)), "gbk") == b"\x08"
    assert key_to_bytes(E(int(Qt.Key_A), "a"), "gbk") == b"a"


# ---- 第五轮 P0-1: 部分写入不丢余量（流式回归） ----
def test_txfile_partial_write_no_loss(tmp_path):
    from app.core.txfile import FileSender

    data = bytes(range(256)) * 2            # 512 字节

    class PartialLink:
        def __init__(self):
            self.received = bytearray()
            self.finished_count = 0

        def is_open(self):
            return True

        def write(self, d):
            n = min(100, len(d))            # 每次只接受 100 字节
            self.received += d[:n]
            return n

    f = tmp_path / "data.bin"
    f.write_bytes(data)
    link = PartialLink()
    fs = FileSender(link)
    done = []
    fs.finished.connect(lambda: done.append(1))
    assert fs.start(str(f), 0) is True
    for _ in range(50):
        fs._send_chunk()
        if done:
            break
    assert link.received == data, "部分写入的余量不得丢失"
    assert len(link.received) == 512
    assert done == [1], "finished 只发一次"


# ---- P0-3: FileSender 与 link 生命周期（流式读取） ----
def test_txfile_zero_write_fails_and_stops(tmp_path):
    from app.core.txfile import FileSender

    f = tmp_path / "data.bin"
    f.write_bytes(b"abc")

    class ZeroLink:
        def is_open(self):
            return True

        def write(self, d):
            return 0

    errors = []
    fs = FileSender(ZeroLink())
    fs.failed.connect(lambda m: errors.append(m))
    assert fs.start(str(f)) is True           # 文件打开成功
    fs._send_chunk()
    assert fs._pos == 0, "0 写入不应推进"
    assert errors, "0 写入应触发失败提示"
    assert fs._f is None, "失败后应关闭句柄"


def test_txfile_broadcast_contract(tmp_path):
    """TCP 服务端广播返回源数据消费量：3 客户端时 _pos 只按源数据推进。"""
    from app.core.txfile import FileSender

    class BroadcastLink:
        def __init__(self):
            self.wire = 0

        def is_open(self):
            return True

        def write(self, d):
            self.wire += 3 * len(d)      # 3 个客户端
            return len(d)                # 源数据消费量

    f = tmp_path / "data.bin"
    f.write_bytes(b"abcdef")
    link = BroadcastLink()
    fs = FileSender(link)
    done = []
    fs.finished.connect(lambda: done.append(1))
    assert fs.start(str(f), 0) is True
    fs._send_chunk()                      # 6B ≤ CHUNK → 一次写完
    assert fs._pos == 6 and link.wire == 18
    fs._send_chunk()                      # EOF → finished，句柄关闭
    assert done and fs._f is None


# ---- 第四轮 P1-1: 接收日志保留空行（显示/日志一致） ----
def test_rxlog_keeps_empty_lines():
    win = MainWindow(Settings(os.path.join(tempfile.gettempdir(),
                                            "pycom_smoke.json")))

    class FakeLog:
        def __init__(self):
            self.buf = []

        def write(self, s):
            self.buf.append(s)

        def flush(self):
            pass

    log = FakeLog()
    win._rx_log = log
    win._rx_log_written = 0
    win.builder.feed("rx", b"one\n\ntwo\n", 1.0, 0)
    win.builder.flush()
    win._rx_log_poll()
    text = "".join(log.buf)
    ls = text.splitlines()
    # 三行：one / 空行（包内续行，无前缀无标识） / two
    assert len(ls) == 3, ls
    assert ls[0].endswith("one") and ls[2].endswith("two")
    assert ls[1] == ""
    win._rx_log = None


# ---- 第四轮 P1-2: 非法类型字段回退默认值，构造不崩溃 ----
def test_config_bad_types_fall_back(tmp_path):
    cfg = tmp_path / "bad_types.json"
    cfg.write_text(
        '{"win_w": "abc", "win_h": null, "checksum_kind": [], "baud": null,\n'
        ' "send_font": null, "recv_font": "oops", "buffer_cap": -5,\n'
        ' "local_port": "70000", "mode": 123, "theme": []}',
        encoding="utf-8")
    win = MainWindow(Settings(str(cfg)))
    for _ in range(3):
        app.processEvents()
    assert win.cfg.d["win_w"] >= 200 and win.cfg.d["win_h"] >= 200
    assert win.cfg.d["checksum_kind"] == "none"
    assert win.cfg.d["baud"] == DEFAULTS["baud"]
    assert isinstance(win.cfg.d["send_font"], dict)
    assert isinstance(win.cfg.d["recv_font"], dict)
    assert win.cfg.d["buffer_cap"] >= 1024
    assert win.cfg.d["local_port"] == 65535      # 越界钳制
    assert win.cfg.d["mode"] in ("serial", "tcp_client", "tcp_server", "udp")
    assert win.cfg.d["theme"] in ("light", "dark")


# ---- 第六轮 P2-3: TCP 广播——部分入队推进源游标；flush 不重复 ----
def test_tcp_write_partial_queue_and_flush():
    from PySide2.QtNetwork import QAbstractSocket
    from app.core.net_link import TcpServerLink

    class Slow:
        def __init__(self):
            self.received = bytearray()
            self.free = 30

        def state(self):
            return QAbstractSocket.ConnectedState

        def write(self, data):
            n = min(self.free, len(data))
            self.received += data[:n]
            self.free -= n
            return n

    link = TcpServerLink({})
    slow = Slow()
    fast = Slow()
    fast.free = 10 ** 9
    link.clients = [slow, fast]
    big = b"z" * 200                          # slow 仅剩 30 可收 → 部分入队
    ret = link.write(big)
    assert ret == 200, "部分入队应推进源游标"
    assert bytes(slow.received) == big[:30]
    slow.free += 1000                         # 对端缓冲排空
    link._flush_pending(slow)                 # 缓冲排空后续发余量
    assert bytes(slow.received) == big, "续发后应对端收全"
    assert not link._pending.get(slow)
    before = bytes(fast.received)
    link.write(b"more")                       # 后续数据不重复
    assert bytes(fast.received).endswith(b"more")
    assert bytes(slow.received) == big + b"more"


# ---- 多条发送循环调度：顺序号排序、跳过 0、逐条延时 ----
def test_multisend_loop_order_skips_zero():
    from app.core.multisend import MultiSendLoop

    rows = [{"note": "a", "order": 2, "interval": 5, "text": "A", "hex": False},
            {"note": "b", "order": 0, "interval": 5, "text": "B", "hex": False},
            {"note": "c", "order": 1, "interval": 5, "text": "C", "hex": False}]
    sent = []
    loop = MultiSendLoop(lambda: rows)
    loop.send_row.connect(lambda r: sent.append(r["note"]))
    assert loop.start_loop() is True     # 有 order>0 条目才启动
    for _ in range(200):                 # 等前两发到达（5ms/条），1s 上限防负载抖动
        QTest.qWait(10)
        if len(sent) >= 2:
            break
    loop.stop()
    assert len(sent) >= 2 and sent[0] == "c"   # 顺序号 1 最先
    assert "b" not in sent               # 顺序号 0 跳过
    assert set(sent) == {"c", "a"}


def test_multisend_loop_no_ordered_rows():
    from app.core.multisend import MultiSendLoop

    rows = [{"note": "x", "order": 0, "interval": 5, "text": "X", "hex": False}]
    loop = MultiSendLoop(lambda: rows)
    sent = []
    loop.send_row.connect(lambda r: sent.append(r["note"]))
    assert loop.start_loop() is False    # 无 order>0 → 不启动
    QTest.qWait(20)
    assert sent == []


# ---- P1-6: 配置循环发送启动后恢复 ----
def test_loop_restored_from_config(tmp_path):
    import json
    cfg = tmp_path / "loop_restore.json"
    cfg.write_text(json.dumps(
        {"multisend_loop": True,
         "multisend_rows": [{"note": "r", "hex": False, "text": "X",
                             "order": 1, "interval": 50}]},
        ensure_ascii=False), encoding="utf-8")
    win2 = MainWindow(Settings(str(cfg)))
    win2._apply_settings()
    win2._wire()
    for _ in range(3):
        app.processEvents()
    assert win2.ms_loop.is_active(), "配置勾选循环发送应恢复启动"
    win2.ms_loop.stop()


# ---- 第六轮 P2-3 补充: 超大 payload 整体拒绝（不写快客户端） ----
def test_tcp_write_rejects_oversize_atomically(tmp_path):
    from PySide2.QtNetwork import QAbstractSocket
    from app.core.net_link import TcpServerLink

    class Slow:
        def __init__(self):
            self.received = bytearray()

        def state(self):
            return QAbstractSocket.ConnectedState

        def write(self, data):
            self.received += data
            return len(data)

    link = TcpServerLink({})
    slow = Slow()
    fast = Slow()
    link.clients = [slow, fast]
    huge = b"z" * (256 * 1024 + 1)            # 超过单客户端队列上限
    assert link.write(huge) == 0, "超限应整体拒绝"
    assert not slow.received and not fast.received, "拒绝时不得产生部分广播"


# ---- 第六轮 P2-2: FindBar 空白 HEX 与非法 HEX 清空旧命中 ----
def test_find_bar_hex_edge_cases():
    from app.ui.find_bar import FindBar
    win = MainWindow(Settings(os.path.join(tempfile.gettempdir(),
                                            "pycom_smoke.json")))
    win.show()
    for _ in range(3):
        app.processEvents()
    win.builder.feed("rx", b"ABAB\n", 1.0, 0)
    win.builder.flush()
    fb = win.find_bar
    fb.show()
    for _ in range(3):
        app.processEvents()
    fb.cb_hex.setChecked(True)
    win.builder.set_hex_mode(True)            # HEX 渲染模式才在 hex 文本里找
    # 有效 HEX：命中
    fb.edit.setText("AB")
    fb._search()
    assert len(fb._hits) == 2, fb._hits
    # 空白 HEX 查询：格式错误 + 清空旧命中
    fb.edit.setText("  ")
    fb._search()
    assert fb._hits == [], "非法查询应清空旧命中"
    assert "格式错误" in fb.label.text()
    # 非法 HEX 查询：同样清空
    fb.edit.setText("ZZ")
    fb._search()
    assert fb._hits == [] and "格式错误" in fb.label.text()
    fb.hide()


# ---- 第六轮 P2-3 补充: 计算器 float32 超范围 ----
def test_calculator_float32_overflow():
    from app.ui.calculator_dlg import CalculatorWidget
    dlg = CalculatorWidget()
    assert dlg._from_f32le("3.5e38") is None, "超范围应返回 None"
    assert dlg._from_f32be("1e39") is None
    assert dlg._from_f32le("1e30") is not None  # 范围内正常


# ---- 第六轮 P3: 终端 CRLF 不重复补 LF ----
def test_terminal_crlf_no_double_lf():
    from app.core.terminal import cr_add_lf
    crlf = b"A" + bytes((0x0D, 0x0A)) + b"B"          # A CRLF B
    assert cr_add_lf(crlf, True) == crlf              # 已有 CRLF 不重复补
    assert cr_add_lf(bytes((0x0D, 0x0A)), True) == bytes((0x0D, 0x0A))
    cr = b"A" + bytes((0x0D,)) + b"B"                 # A CR B：单 CR 仍补 LF
    assert cr_add_lf(cr, True) == b"A" + bytes((0x0D, 0x0A)) + b"B"
    tail = b"A" + bytes((0x0D,))                      # 末尾单 CR 仍补 LF
    assert cr_add_lf(tail, True) == b"A" + bytes((0x0D, 0x0A))


# ---- 第六轮 P1-1 补充: 字体嵌套非法值构造 MainWindow 不崩溃 ----


# ---- 第六轮 P1-1 补充: 字体嵌套非法值构造 MainWindow 不崩溃 ----
def test_font_nested_bad_values(tmp_path):
    import json
    cfg = tmp_path / "font_bad.json"
    cfg.write_text(json.dumps(
        {"send_font": {"size": "x"}, "recv_font": None,
         "encoding": "not-a-codec", "multisend_rows": [1],
         "multisend_loop": True}),
        encoding="utf-8")
    win = MainWindow(Settings(str(cfg)))
    win.show()
    for _ in range(3):
        app.processEvents()
    assert win.cfg.d["encoding"] == "gbk"          # 非法编码回退
    assert isinstance(win.cfg.d["send_font"], dict)
    assert win.cfg.d["recv_font"]["family"]        # None → 回退默认字体
    assert win.ms_model.rowCount() == 0            # 非法行被过滤
    win.builder.feed("rx", b"RXDATA", 1.0, 0)      # 首次接收不抛 LookupError
    win.builder.flush()
    for _ in range(3):
        app.processEvents()
    assert "RXDATA" in win.rxview.plain_text()


# ---- 第六轮 P2-3 补充: 可 flush 恢复场景不被误判拒绝 ----
def test_tcp_write_flush_before_capacity_check():
    from PySide2.QtNetwork import QAbstractSocket
    from app.core.net_link import TcpServerLink

    class Slow:
        def __init__(self):
            self.received = bytearray()
            self.free = 10

        def state(self):
            return QAbstractSocket.ConnectedState

        def write(self, data):
            n = min(self.free, len(data))
            self.received += data[:n]
            self.free -= n
            return n

    link = TcpServerLink({})
    slow = Slow()
    link.clients = [slow]
    d = b"w" * 40
    link.write(d)                          # 余 30 入队（free 用尽）
    slow.free = 100                        # 对端缓冲恢复
    ret = link.write(d)                    # 应先 flush 旧余量再接纳，不误判拒绝
    assert ret == len(d), ret
    link._flush_pending(slow)
    assert bytes(slow.received) == d * 2


# ---- 第七轮 P1-1: 裁剪后（base>0）搜索命中绝对行号 ----
def test_find_bar_absolute_lines_after_trim():
    from app.core.display import LineBuilder
    from app.ui.find_bar import FindBar

    b = LineBuilder(max_lines=1000)
    got = []
    fb = FindBar(got.append, lambda idx: None)
    fb.set_builder_provider(lambda: b)
    for i in range(1200):                       # 超过 max_lines 触发裁剪
        b.feed("rx", ("line%d\n" % i).encode(), 1.0, 0)
    b.flush()
    assert b.base > 0, b.base
    fb.edit.setText("line1199")
    fb._search()
    assert len(fb._hits) == 1, fb._hits
    assert fb._hits[0] == (1199, 0, 8), fb._hits    # 绝对行号而非相对下标
    assert 1199 in got[-1]


# ---- 第七轮 P1-1: 跨行单匹配只计 1 个结果，高亮仍覆盖两行 ----
def test_find_bar_crossline_match_counts_once():
    from app.core.display import LineBuilder
    from app.ui.find_bar import FindBar

    b = LineBuilder(max_lines=1000)
    got = []
    fb = FindBar(lambda m: got.append(m), lambda idx: None)
    fb.set_builder_provider(lambda: b)
    b.feed("rx", b"AB\nCD\n", 1.0, 0)
    b.flush()
    fb.edit.setText("B\nC")                     # 含换行的跨行匹配（行分隔符参与匹配）
    fb._search()
    assert len(fb._hits) == 1, fb._hits         # 逻辑匹配计 1（不再拆成 1/2）
    segs = got[-1]
    assert sorted(segs.keys()) == [0, 1]        # 高亮两行都有段
    assert segs[0] == [(1, 1)] and segs[1] == [(0, 1)]


# ---- 第七轮 P2-1: multisend 超大 order/interval 钳制 ----
def test_multisend_rows_clamped(tmp_path):
    import json
    cfg = tmp_path / "ms_huge.json"
    cfg.write_text(json.dumps({"multisend_rows": [
        {"note": "n", "hex": False, "text": "x",
         "order": 10 ** 30, "interval": 10 ** 30}]}), encoding="utf-8")
    s = Settings(str(cfg))
    row = s.d["multisend_rows"][0]
    assert row["order"] == 9999, row
    assert row["interval"] == 3_600_000, row


# ---- 第七轮 P2-1: INI 多条发送超大数值同样钳制 ----
def test_ini_multisend_huge_values(tmp_path):
    ini = tmp_path / "sscom51.ini"
    ini.write_text("N101=999999999999999999999999,note1,888888888888888888888888\n"
                   "N1=A,hello\n", encoding="gbk")
    s = Settings(str(tmp_path / "cfg.json"))
    s.import_original_ini(str(ini))
    row = s.d["multisend_rows"][0]
    assert row["order"] == 9999, row
    assert row["interval"] == 3_600_000, row
    assert row["note"] == "note1" and row["text"] == "hello"


# ---- 第八轮 P2-1: 防抖期间回车/上一/下一个先立即搜索 ----
def test_find_bar_debounce_enter_and_buttons():
    from app.core.display import LineBuilder
    from app.ui.find_bar import FindBar

    b = LineBuilder(max_lines=1000)
    fb = FindBar(lambda m: None, lambda idx: None)
    fb.set_builder_provider(lambda: b)
    b.feed("rx", b"needle\n", 1.0, 0)
    b.flush()
    fb.edit.setText("needle")               # 防抖启动，_hits 尚未更新
    assert fb._debounce.isActive()
    assert fb._hits == []
    fb._on_enter()                          # 回车立即落定查询
    assert len(fb._hits) == 1 and not fb._debounce.isActive()
    fb.edit.setText("nee")
    fb.edit.setText("needle")               # 重新进入防抖
    assert fb._debounce.isActive()
    fb._jump_next()                         # "下一个"：先落定再跳
    assert len(fb._hits) == 1
    fb.edit.setText("nee")
    fb.edit.setText("needle")
    fb._jump_prev()
    assert len(fb._hits) == 1


# ---- 第八轮 P2-2: 命中行被裁剪后，跳转前重算失效 ----
def test_find_bar_stale_hits_revalidate_on_jump():
    from app.core.display import LineBuilder
    from app.ui.find_bar import FindBar

    b = LineBuilder(max_lines=1000)
    fb = FindBar(lambda m: None, lambda idx: None)
    fb.set_builder_provider(lambda: b)
    for i in range(1200):
        b.feed("rx", ("line%d\n" % i).encode(), 1.0, 0)
    b.flush()
    fb.edit.setText("line1199")
    fb._search()
    assert fb._hits == [(1199, 0, 8)]
    for i in range(1100):                   # 持续接收 → 裁剪使 base 越过 1199
        b.feed("rx", ("x%d\n" % i).encode(), 2.0, 0)
    b.flush()
    assert b.base > 1199, b.base
    fb._jump(1)                             # 命中已失效 → 自动重算
    assert fb._hits == []                   # 旧行已不存在
    assert fb.label.text() == "0 个结果"


# ---- 第八轮 P2-2: 新数据到达触发节流重扫 ----
def test_find_bar_notify_rescan_coalesced():
    from app.core.display import LineBuilder
    from app.ui.find_bar import FindBar

    b = LineBuilder(max_lines=1000)
    fb = FindBar(lambda m: None, lambda idx: None)
    fb.set_builder_provider(lambda: b)
    b.feed("rx", b"needle\n", 1.0, 0)
    b.flush()
    fb.show()
    for _ in range(3):
        app.processEvents()
    fb.edit.setText("needle")
    fb._debounce.stop()                     # 模拟防抖已落定
    b.feed("rx", b"needle\n", 2.0, 0)       # 新数据到达
    b.flush()
    fb.notify_data_changed()                # → 启动节流重扫
    assert fb._rescan.isActive()
    fb._rescan.timeout.emit()               # 直接触发（测试不等 500ms）
    assert len(fb._hits) == 2


# ---- 第八轮 P2-3: INI 导入即替换 + 参数上界钳制 ----
def test_ini_import_replaces_rows_and_clamps(tmp_path):
    ini = tmp_path / "sscom51.ini"
    ini.write_text("N1067=999999999999\nN1069=99999\n"
                   "N1082=99999999\nN1083=99999999\n", encoding="gbk")
    s = Settings(str(tmp_path / "cfg.json"))
    s.d["multisend_rows"] = [{"note": "old", "hex": True, "text": "AA",
                              "order": 1, "interval": 1}]
    s.import_original_ini(str(ini))
    assert s.d["multisend_rows"] == []      # INI 无多条 → 清空旧行（导入即替换）
    assert s.d["buffer_cap"] == 50 * 1024 * 1024   # 超大缓冲钳到上界（50MB，32 位）
    assert s.d["remote_port"] == 65535
    assert s.d["win_w"] == 20000 and s.d["win_h"] == 20000


# ---- 第八轮 P3-1: 右键保存 .bin 失败有提示、成功正常落盘 ----
def test_save_rx_bin_hook_failure_and_success(tmp_path, monkeypatch):
    win = MainWindow(Settings(os.path.join(tempfile.gettempdir(),
                                            "pycom_smoke.json")))
    win.show()
    for _ in range(3):
        app.processEvents()
    win._on_rx(b"DATA")                     # 走真实接收路径（buf + builder 同步）
    for _ in range(3):
        app.processEvents()
    from PySide2.QtWidgets import QMessageBox
    calls = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: calls.append(a))
    bad = tmp_path / "dir.bin"
    bad.mkdir()                             # 同名目录 → open 必败
    win._save_rx(str(bad))
    assert len(calls) == 1 and "保存失败" in calls[0][2]
    ok = tmp_path / "ok.bin"
    win._save_rx(str(ok))
    assert ok.read_bytes() == b"DATA"
    assert len(calls) == 1                  # 成功路径不弹窗


# ---- 第八轮 P3-2: 链路关闭时清理文件发送进度条 ----
def test_close_link_hides_file_progress():
    win = MainWindow(Settings(os.path.join(tempfile.gettempdir(),
                                            "pycom_smoke.json")))
    win.show()
    for _ in range(3):
        app.processEvents()
    win.progress.show()
    win._close_link(silent=True)
    assert not win.progress.isVisible()


# ---- 第八轮 P3-3: 单次超大块（余量为空）同样受积压上限约束 ----
def test_write_over_cap_rejected_even_empty_pend():
    win = MainWindow(Settings(os.path.join(tempfile.gettempdir(),
                                            "pycom_smoke.json")))
    win.show()
    for _ in range(3):
        app.processEvents()
    assert win.link is None
    win._write(b"x" * (win._TX_PEND_CAP + 1))
    assert not win._tx_pend                 # 整体拒绝，不进入余量
    assert "超限" in win.sb_link.text()
    win._tx_pend = [(b"y" * 100, True)]
    win._write(b"z" * win._TX_PEND_CAP)     # 合并后超限 → 拒绝
    assert win._tx_pend == [(b"y" * 100, True)]   # 旧余量原样保留


def test_statusbar_notice_reverts_to_link_base():
    """状态栏约定：第1框只显示 已连接/未连接；其余提示为第4框临时通知，
    期内新通知刷新、超时回显常驻连接信息（串口参数/网络对端）。"""
    win = MainWindow(Settings(os.path.join(tempfile.gettempdir(),
                                            "pycom_smoke.json")))
    win._set_link_base("COM3 115200,8,1,NONE,NONE")
    assert win.sb_link.text() == "COM3 115200,8,1,NONE,NONE"
    win._notify("打开 COM3 失败(拒绝访问)")
    assert "打开 COM3 失败" in win.sb_link.text()
    win._set_link_base("BASE2")             # 通知显示期间只更新底稿
    assert win.sb_link.text() != "BASE2"
    win._sb_notice.timeout.emit()           # 模拟 3 秒超时（不等真实时间）
    assert win.sb_link.text() == "BASE2"
    win.close()


def test_rx_log_flush_tail_failure_resets_cursor(tmp_path, monkeypatch):
    """回归（审查 R1）：flush_tail 写盘失败也必须复位转存游标，
    否则残留游标会把行数硬上限的裁剪永久锁死。"""
    win = MainWindow(Settings(str(tmp_path / "rt.json")))
    win.show()
    for _ in range(3):
        app.processEvents()

    class BoomLog:
        def write(self, s):
            raise OSError("disk full")

        def close(self):
            pass

    win._rx_log = BoomLog()
    win._rx_log_written = 0
    win._on_rx(b"hello")                   # 未收尾行 → 游标经 _trim_lines_to_buf 设置
    win.builder.flush()                    # pending 落成 _open 行（未收尾，无换行）
    assert win._rx_log is not None
    assert win.builder._log_keep is not None
    win._rx_log_flush_tail()               # 写盘失败 → 脱钩 + 游标复位
    assert win._rx_log is None
    assert win.builder._log_keep is None
    win.close()


def test_close_with_log_write_failure_still_saves(tmp_path):
    """回归（审查 S1）：关窗收尾时写盘失败（poll/flush_tail 已把日志置 None），
    closeEvent 不得再抛 AttributeError，且 _save_settings 仍要执行。"""
    cfg_path = str(tmp_path / "cs.json")
    win = MainWindow(Settings(cfg_path))
    win.show()
    for _ in range(3):
        app.processEvents()

    class BoomLog:
        def write(self, s):
            raise OSError("disk full")

        def close(self):
            pass

    win._rx_log = BoomLog()
    win._rx_log_written = 0
    win._on_rx(b"hello")
    win.builder.flush()
    win.close()                            # 不应抛 AttributeError
    assert win._rx_log is None
    assert os.path.exists(cfg_path)        # 退出参数已落盘


# ---- 第九轮 P2: 失效命中重算后只跳一次（不再二次推进） ----
def test_find_bar_revalidate_jumps_once():
    from app.core.display import LineBuilder
    from app.ui.find_bar import FindBar

    b = LineBuilder(max_lines=1000)
    jumped = []
    fb = FindBar(lambda m: None, lambda idx: jumped.append(idx))
    fb.set_builder_provider(lambda: b)
    for i in range(1025):                   # 裁剪后 base=125，行 0-124 已不存在
        b.feed("rx", ("filler%d\n" % i).encode(), 1.0, 0)
    b.flush()
    assert b.base == 125, b.base
    fb.edit.setText("filler9")              # 当前查询重算后仍有命中
    fb._hits = [(100, 0, 6), (900, 0, 6), (950, 0, 6)]   # 模拟裁剪前的旧命中
    fb._cur = -1
    fb._jump(1)
    assert len(jumped) == 1, jumped         # 重算自动跳一次，不二次推进
    assert fb._cur == 0
    assert jumped[0] == fb._hits[0][0]      # 落在首个有效命中


# ---- 第九轮 P2: 表格直接编辑同样受 order/interval 限幅 ----
def test_multisend_model_setdata_clamped():
    from app.core.multisend import COL_INTERVAL, COL_ORDER, MultiSendModel

    m = MultiSendModel()
    m.add_row()
    assert m.setData(m.index(0, COL_INTERVAL), "1000000000000000000000000000000")
    assert m.rows[0]["interval"] == 3_600_000
    assert m.setData(m.index(0, COL_ORDER), "999999999999")
    assert m.rows[0]["order"] == 9999
    assert m.setData(m.index(0, COL_INTERVAL), "abc") is False


# ---- 第九轮 P2: 循环调度器对非 Settings 来源行数据的兜底限幅 ----
def test_multisend_loop_interval_defense():
    from app.core.multisend import MultiSendLoop

    sent = []
    loop = MultiSendLoop(lambda: [{"note": "", "hex": False, "text": "x",
                                   "order": 1, "interval": 10 ** 30}])
    loop.send_row.connect(lambda r: sent.append(r))
    assert loop.start_loop()                # 不触发 Shiboken 溢出
    assert loop._timer.interval() == 3_600_000
    assert len(sent) == 1


# ---- 第九轮 P3: 隐藏期间数据变化，重开查找条补一次重扫 ----
def test_find_bar_activate_rescans_after_hidden_changes():
    from app.core.display import LineBuilder
    from app.ui.find_bar import FindBar

    b = LineBuilder(max_lines=1000)
    fb = FindBar(lambda m: None, lambda idx: None)
    fb.set_builder_provider(lambda: b)
    b.feed("rx", b"needle\n", 1.0, 0)
    b.flush()
    fb.edit.setText("needle")
    fb._search()
    assert len(fb._hits) == 1
    fb.hide()
    b.feed("rx", b"needle\n", 2.0, 0)       # 隐藏期间新数据到达
    b.flush()
    fb.activate()                           # 重开 → 补一次节流重扫
    assert fb._rescan.isActive()
    fb._rescan.timeout.emit()
    assert len(fb._hits) == 2


# ---- 第九轮 P3: 清屏后查找状态同步归零 ----
def test_clear_display_resets_find_state():
    win = MainWindow(Settings(os.path.join(tempfile.gettempdir(),
                                            "pycom_smoke.json")))
    win.show()
    for _ in range(3):
        app.processEvents()
    win._on_rx(b"needle\n")
    win.builder.flush()
    for _ in range(3):
        app.processEvents()
    fb = win.find_bar
    fb.show()
    fb.edit.setText("needle")
    fb._search_now()
    assert len(fb._hits) == 1
    win._clear_display()
    assert fb._rescan.isActive()            # 清屏通知了重算
    fb._rescan.timeout.emit()
    assert fb._hits == []                   # 命中归零


# ---- 第十轮 P2: 重算节流 pending 期间回车/跳转先落定重算 ----
def test_find_bar_rescan_pending_jump_flushes():
    from app.core.display import LineBuilder
    from app.ui.find_bar import FindBar

    b = LineBuilder(max_lines=1000)
    fb = FindBar(lambda m: None, lambda idx: None)
    fb.set_builder_provider(lambda: b)
    b.feed("rx", b"needle\n", 1.0, 0)
    b.flush()
    fb.show()
    for _ in range(3):
        app.processEvents()
    fb.edit.setText("needle")
    fb._search()                            # 旧命中集合 = 1
    assert len(fb._hits) == 1
    b.feed("rx", b"needle\n", 2.0, 0)       # 新数据到达
    b.flush()
    fb._debounce.stop()                     # 模拟防抖已落定（仅剩重算节流）
    fb.notify_data_changed()
    assert fb._rescan.isActive()
    assert len(fb._hits) == 1               # pending 期间仍是旧集合
    fb._jump_next()                         # pending 窗口内跳转 → 先落定重算
    assert not fb._rescan.isActive()
    assert len(fb._hits) == 2               # 新命中已纳入
    fb.notify_data_changed()
    assert fb._rescan.isActive()
    fb._on_enter()                          # 回车同样先落定再跳
    assert len(fb._hits) == 2


# ---- 功能: 自动滚屏开关（默认跟随，关闭后视图停留） ----
def test_auto_scroll_toggle(tmp_path):
    win = MainWindow(Settings(str(tmp_path / "autoscroll.json")))
    win.show()
    for _ in range(3):
        app.processEvents()
    rv = win.rxview
    sb = rv.verticalScrollBar()
    assert win.cb_auto_scroll.isChecked()   # 默认勾选
    win._on_rx(b"DATA\n" * 300)
    win.builder.flush()
    for _ in range(5):
        app.processEvents()
    assert sb.value() == sb.maximum()       # 默认自动跟随到底
    rv.set_auto_scroll(False)
    sb.setValue(0)
    win._on_rx(b"DATA\n" * 300)             # 新数据只入缓冲
    win.builder.flush()
    for _ in range(5):
        app.processEvents()
    assert sb.value() == 0                  # 视图停在原位置
    assert win.builder.line_count() > 0     # 数据仍在缓冲中
    rv.set_auto_scroll(True)
    for _ in range(5):
        app.processEvents()
    assert sb.value() == sb.maximum()       # 重新开启立即跳回最新
    # 持久化
    win.cb_auto_scroll.setChecked(False)
    win._save_settings(silent=True)
    assert win.cfg.d["auto_scroll"] is False


# ---- 功能: 网卡选择下拉与本地 IP 联动 ----
def test_nic_combo_filters_local_ips():
    win = MainWindow(Settings(os.path.join(tempfile.gettempdir(),
                                            "pycom_smoke.json")))
    win.show()
    for _ in range(3):
        app.processEvents()
    nic = win.cmb_nic
    assert nic.count() >= 1 and nic.itemText(0) == "全部网卡"
    # 选中某个具体网卡 → 本地 IP 只列该网卡地址（不再混入其它接口）
    nic.setCurrentIndex(nic.count() - 1)
    expected = nic.currentData()
    assert expected, "至少应枚举出一个含 IPv4 的接口"
    ips = [win.cmb_local_ip.itemText(i) for i in range(win.cmb_local_ip.count())]
    assert set(ips) == set(expected)
    # 切回"全部网卡" → 恢复全地址列表
    nic.setCurrentIndex(0)
    ips_all = [win.cmb_local_ip.itemText(i) for i in range(win.cmb_local_ip.count())]
    assert len(ips_all) >= len(expected)
    # 刷新按钮路径不崩溃，仍保留"全部网卡"首项
    win._refresh_nics(select_ip=win.cmb_local_ip.currentText())
    assert win.cmb_nic.itemText(0) == "全部网卡"
    assert isinstance(win._nics, list) and len(win._nics) >= 1


# ---- 复审: auto_scroll=false 配置启动时 RxView 同步为不跟随 ----
def test_auto_scroll_off_from_config(tmp_path):
    import json
    cfg = tmp_path / "off.json"
    cfg.write_text(json.dumps({"auto_scroll": False}), encoding="utf-8")
    win = MainWindow(Settings(str(cfg)))
    win.show()
    for _ in range(3):
        app.processEvents()
    assert not win.cb_auto_scroll.isChecked()
    assert not win.rxview._auto_scroll      # 初始状态已显式同步（不等 toggled 连接）
    sb = win.rxview.verticalScrollBar()
    win._on_rx(b"DATA\n" * 300)
    win.builder.flush()
    for _ in range(5):
        app.processEvents()
    assert sb.value() == 0                  # 新数据不自动跟随


# ---- 终端仿真: 回车后发送行（在接收区输入、回显、回车发送最后一行+回车） ----
def test_term_enter_line_mode(tmp_path, monkeypatch):
    from PySide2.QtTest import QTest
    from PySide2.QtCore import Qt as _Qt
    win = MainWindow(Settings(str(tmp_path / "term.json")))
    win.show()
    for _ in range(3):
        app.processEvents()
    win.act_term_enter.setChecked(True)
    win.act_term_lf.setChecked(False)       # 先验证纯回车 0x0D
    sent = []
    win._write = lambda d, record=True: sent.append(bytes(d))
    vp = win.rxview.viewport()
    QTest.keyClicks(vp, "help")
    assert "".join(win._term_line) == "help"         # 输入行缓冲
    QTest.keyClick(vp, _Qt.Key_Backspace)
    QTest.keyClicks(vp, "l")
    QTest.keyClick(vp, _Qt.Key_Return)
    assert sent == [b"hell\r"], sent                 # 最后一行 + 回车
    assert not win._term_line                        # 发送后行缓冲清空
    win.act_term_lf.setChecked(True)
    QTest.keyClick(vp, _Qt.Key_Return)
    assert sent[-1] == b"\r\n"                       # 勾选后回车自动补换行
    win.builder.flush()
    # 本地回显：输入过程逐字回显，退格以真实终端的 "\b \b" 序列呈现
    assert "help\x08 \x08l" in win.rxview.plain_text()


# ---- 复审: 链路打开时终端行模式不重复回显 ----
def test_term_enter_no_double_echo(tmp_path):
    from PySide2.QtTest import QTest
    from PySide2.QtCore import Qt as _Qt
    win = MainWindow(Settings(str(tmp_path / "echo.json")))
    win.show()
    for _ in range(3):
        app.processEvents()
    win.act_term_enter.setChecked(True)

    class FakeLink:
        def is_open(self):
            return True

        def write(self, d):
            return len(d)

    win.link = FakeLink()
    vp = win.rxview.viewport()
    QTest.keyClicks(vp, "abc")
    QTest.keyClick(vp, _Qt.Key_Return)
    win.builder.flush()
    text = win.rxview.plain_text()
    assert "abcabc" not in text, text                # 不双重回显
    assert "abc" in text


# ---- 复审: 多字节字符退格删除整字，不发送半个编码单元 ----
def test_term_multibyte_backspace(tmp_path):
    from PySide2.QtTest import QTest
    from PySide2.QtCore import Qt as _Qt
    from PySide2.QtGui import QInputMethodEvent
    win = MainWindow(Settings(str(tmp_path / "mb.json")))
    win.show()
    for _ in range(3):
        app.processEvents()
    win.act_term_enter.setChecked(True)
    win.act_term_lf.setChecked(False)
    sent = []
    win.link = type("L", (), {"is_open": lambda s: True,
                              "write": lambda s, d: len(d)})()
    win._write = lambda d, record=True: sent.append(bytes(d))
    vp = win.rxview.viewport()
    ev = QInputMethodEvent()
    ev.setCommitString("中")                          # GBK 两字节 d6 d0
    win.eventFilter(vp, ev)
    assert "".join(win._term_line) == "中"
    QTest.keyClick(vp, _Qt.Key_Backspace)
    assert not win._term_line and not win._term_line_raw   # 整字删除，无残留半字节
    QTest.keyClick(vp, _Qt.Key_Return)
    assert sent == [b"\r"], sent                     # 不发送 d6 这类非法残片
    # 编码边界：latin-1 下 IME 输入"中"，发送字节与回显字节严格一致（GBK 回退）
    win._encoding = "latin-1"
    ev2 = QInputMethodEvent()
    ev2.setCommitString("中")
    win.eventFilter(vp, ev2)
    QTest.keyClick(vp, _Qt.Key_Return)
    assert sent[-1] == b"\xd6\xd0\r", sent[-1]       # 与逐字回显的 d6 d0 一致


# ---- 复审: databits 5/6 配置持久化 ----
def test_databits_persist(tmp_path):
    for v in (5, 6, 7, 8):
        p = tmp_path / ("db%d.json" % v)
        p.write_text('{"databits": %d}' % v, encoding="utf-8")
        assert Settings(str(p)).d["databits"] == v


# ---- 复审: 终端数据进 pending 后，续发不再重复记录 TX 显示 ----
def test_term_pend_no_dup_after_reconnect(tmp_path):
    from PySide2.QtTest import QTest
    from PySide2.QtCore import Qt as _Qt
    win = MainWindow(Settings(str(tmp_path / "pend.json")))
    win.show()
    for _ in range(3):
        app.processEvents()
    win.act_term_enter.setChecked(True)
    win.act_term_lf.setChecked(False)

    class DownLink:
        def is_open(self):
            return False

        def write(self, d):
            return 0

    win.link = DownLink()
    vp = win.rxview.viewport()
    QTest.keyClicks(vp, "abc")
    QTest.keyClick(vp, _Qt.Key_Return)
    assert win._tx_pend == [(b"abc\r", False)]       # 链路不可用 → 进余量（终端段不记录）
    win.builder.flush()
    before = win.rxview.plain_text()
    # 链路恢复 → 余量续发
    win.link = type("L", (), {"is_open": lambda s: True,
                              "write": lambda s, d: len(d)})()
    win._flush_tx_pend()
    assert not win._tx_pend
    win.builder.flush()
    after = win.rxview.plain_text()
    assert before == after, (before, after)          # 续发不追加 TX 显示
    # 余量清空后标记复位：普通发送仍正常记录 TX
    win._write(b"NORMAL")
    win.builder.flush()
    assert "NORMAL" in win.rxview.plain_text()


# ---- 复审: 终端余量与普通发送混合时按段保存记录语义 ----
def test_term_mixed_pend_segments(tmp_path):
    from PySide2.QtTest import QTest
    from PySide2.QtCore import Qt as _Qt
    win = MainWindow(Settings(str(tmp_path / "mix.json")))
    win.show()
    for _ in range(3):
        app.processEvents()
    win.act_term_enter.setChecked(True)
    win.act_term_lf.setChecked(False)
    win.link = type("D", (), {"is_open": lambda s: False,
                              "write": lambda s, d: len(d)})()
    vp = win.rxview.viewport()
    QTest.keyClicks(vp, "TERM")
    QTest.keyClick(vp, _Qt.Key_Return)
    win._write(b"NORMAL")                    # 离线合并：两种语义分段共存
    assert [seg for seg, _ in win._tx_pend] == [b"TERM\r", b"NORMAL"]
    assert [rec for _, rec in win._tx_pend] == [False, True]
    # 链路恢复 → 分段续发：终端段不重复记录，普通段记录一次
    win.link = type("U", (), {"is_open": lambda s: True,
                              "write": lambda s, d: len(d)})()
    win._flush_tx_pend()
    assert not win._tx_pend
    win.builder.flush()
    text = win.rxview.plain_text()
    assert "TERMTERM" not in text, text
    assert "NORMAL" in text


# ---- P1 回归: 链路打开时 _write 不重复发送（对端字节序列断言） ----
def test_write_send_once_p1(tmp_path):
    from PySide2.QtTest import QTest
    from PySide2.QtCore import Qt as _Qt
    win = MainWindow(Settings(str(tmp_path / "p1.json")))
    win.show()
    for _ in range(3):
        app.processEvents()

    class RecLink:
        def __init__(self):
            self.received = bytearray()
            self.cap = None                  # None=全量接受
        def is_open(self):
            return True
        def write(self, d):
            n = len(d) if self.cap is None else min(self.cap, len(d))
            self.received += d[:n]
            return n

    link = RecLink()
    win.link = link
    # 1) 普通发送只发一次
    win._write(b"abc")
    assert bytes(link.received) == b"abc", bytes(link.received)
    # 2) 旧 pending 在前、当前段在后，各发送一次
    win.link = type("D", (), {"is_open": lambda s: False,
                              "write": lambda s, d: len(d)})()
    win.act_term_enter.setChecked(True)
    win.act_term_lf.setChecked(False)
    vp = win.rxview.viewport()
    QTest.keyClicks(vp, "TERM")
    QTest.keyClick(vp, _Qt.Key_Return)       # 离线 → 终端段入队
    win.link = link
    win._write(b"NORMAL")
    assert bytes(link.received) == b"abcTERM\rNORMAL", bytes(link.received)
    # 3) 终端行发送走真实 _write 路径也只发一次
    marker = len(link.received)
    QTest.keyClicks(vp, "hi")
    QTest.keyClick(vp, _Qt.Key_Return)
    assert bytes(link.received)[marker:] == b"hi\r"
    # 4) 部分写入：剩余只续发一次
    link.cap = 3
    marker = len(link.received)
    win._write(b"XYZ12")
    for _ in range(5):
        if not win._tx_pend:
            break
        win._flush_tx_pend()
    assert bytes(link.received)[marker:] == b"XYZ12"
    # 5) record 混合：显示无重复、普通段有记录
    win.builder.flush()
    text = win.rxview.plain_text()
    assert "TERMTERM" not in text
    assert "NORMAL" in text and "XYZ12" in text


# ---- SSCOM 语义: 终端仿真两种发送方式互斥 + 本地回显仅按键方式可用 ----
def test_term_modes_mutually_exclusive(tmp_path):
    win = MainWindow(Settings(str(tmp_path / "term_mutex.json")))
    win.show()
    for _ in range(3):
        app.processEvents()
    # 默认对齐 SSCOM：选中【回车后发送新行(带回显)】，本地回显按键值置灰
    assert win.act_term_enter.isChecked()
    assert not win.act_term_key.isChecked()
    assert not win.act_term_echo.isEnabled()        # 回车方式下回显项不可用
    win.act_term_key.trigger()
    assert win.act_term_key.isChecked()
    assert not win.act_term_enter.isChecked()       # 选中按键方式自动取消回车方式
    assert win.act_term_echo.isEnabled()            # 仅与按键方式搭配
    # 再切回回车方式：互斥仍然成立、回显重新置灰
    win.act_term_enter.trigger()
    assert win.act_term_enter.isChecked()
    assert not win.act_term_key.isChecked()
    assert not win.act_term_echo.isEnabled()
    # 点击已选中的方式可取消：两种方式全关（终端仿真关闭）
    win.act_term_enter.trigger()
    assert not win.act_term_enter.isChecked()
    assert not win.act_term_key.isChecked()


# ---- 回归: 真实鼠标点击接收区后，按键落在实际焦点控件上必须被终端仿真接收 ----
def test_term_input_real_click_focus(tmp_path):
    win = MainWindow(Settings(str(tmp_path / "term_click.json")))
    win.show()
    for _ in range(3):
        app.processEvents()
    sent = []

    class UpLink:
        def is_open(self):
            return True
        def write(self, d):
            sent.append(bytes(d))
            return len(d)

    win.link = UpLink()
    vp = win.rxview.viewport()
    QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, vp.rect().center())
    for _ in range(3):
        app.processEvents()
    # 用窗口内焦点而非 app.focusWidget()：桌面被其他程序占前台时 show() 不保证
    # 激活窗口（全局焦点会是 None），而"点击 viewport 后窗口内焦点落在哪"才是
    # 本测试的回归点（终端输入对两个焦点目标都必须可用）
    fw = win.focusWidget()
    # Qt 把点击 viewport 的焦点交给外框 RxView，真实按键只会投递到焦点控件；
    # 无论焦点在外框还是 viewport，终端输入都必须可用（缺陷回归点）
    assert fw in (win.rxview, vp), type(fw).__name__
    QTest.keyClicks(fw, "help")
    QTest.keyClick(fw, Qt.Key_Backspace)
    QTest.keyClicks(fw, "lo")
    QTest.keyClick(fw, Qt.Key_Return)
    assert sent == [b"hello\r\n"], sent             # 默认 0D 自动补 0A、退格生效
    win.builder.flush()
    assert "help\x08 \x08lo" in win.rxview.plain_text()   # 本地回显


# ---- SSCOM 语义: 配置两方式同真时归一化为按键方式优先 ----
def test_term_mode_mutex_normalize_gui(tmp_path):
    cfg = tmp_path / "term_both.json"
    cfg.write_text('{"term_enter_send": true, "term_key_send": true, '
                   '"term_key_echo": true}', encoding="utf-8")
    win = MainWindow(Settings(str(cfg)))
    win.show()
    for _ in range(3):
        app.processEvents()
    assert win.act_term_key.isChecked()
    assert not win.act_term_enter.isChecked()
    assert win.act_term_echo.isEnabled() and win.act_term_echo.isChecked()


# ---- 串口设置-帮助: 菜单项存在且弹出 SSCOM 对齐的帮助文本 ----
def test_serial_help_menu(tmp_path, monkeypatch):
    from PySide2.QtWidgets import QMessageBox
    win = MainWindow(Settings(str(tmp_path / "help.json")))
    win.show()
    for _ in range(3):
        app.processEvents()
    act_menu = [a for a in win.menuBar().actions()
                if a.text().replace("&", "") == "串口设置"]
    assert act_menu, [a.text() for a in win.menuBar().actions()]
    help_acts = [a for a in act_menu[0].menu().actions()
                 if a.text().replace("&", "") == "帮助"]
    assert help_acts, [a.text() for a in act_menu[0].menu().actions()]
    cap = {}
    monkeypatch.setattr(QMessageBox, "information",
                        lambda *a, **k: cap.update(
                            title=a[1] if len(a) > 1 else "",
                            text=a[2] if len(a) > 2 else ""))
    help_acts[0].trigger()
    assert cap["title"] == "串口设置帮助"
    t = cap["text"]
    assert "串口设置帮助" in t
    assert "115200" in t and "12000000" in t      # 波特率上下限说明
    assert "pycom.json" in t                      # 本软件配置文件（非 sscom5.ini）
    assert "启动立刻打开串口" in t and "数据位、停止位、校验位、流控" in t


# ---- 深色模式: 调色板跟随主题（原生绘制元素不再白底白字）+ 明暗提示同步 ----
def test_theme_palette_follows_theme(tmp_path):
    from PySide2.QtGui import QGuiApplication, QPalette
    cfg = tmp_path / "dark.json"
    cfg.write_text('{"theme": "dark"}', encoding="utf-8")
    win = MainWindow(Settings(str(cfg)))
    win.show()
    for _ in range(3):
        app.processEvents()
    pal = app.palette()
    assert pal.color(QPalette.Window).name() == "#252526"     # 深色窗体底
    assert pal.color(QPalette.Base).name() == "#1e1e1e"       # 深色输入底
    assert pal.color(QPalette.Text).name() == "#d4d4d4"       # 浅色文字（状态栏可见）
    # Qt5 无 styleHints().colorScheme()（Qt 6.5+ API）：明暗完全由
    # 自身 QSS+调色板+DWM 标题栏控制，此处不再断言系统明暗提示
    win.act_theme.setChecked(False)                            # 切浅色 → 恢复标准色板
    for _ in range(3):
        app.processEvents()
    assert app.palette().color(QPalette.Window).name() != "#252526"
    win.act_theme.setChecked(True)                             # 切回深色
    for _ in range(3):
        app.processEvents()
    assert app.palette().color(QPalette.Window).name() == "#252526"
    # 标题栏接口在任意平台调用都不抛异常（Windows 上写 DWM 属性）
    win._apply_titlebar(True)
    win._apply_titlebar(False)


# ---- SSCOM 语义: 未勾选"加时间戳和分包显示"时无方向标识也无时间戳前缀 ----
def test_marker_only_with_timestamp(tmp_path):
    win = MainWindow(Settings(str(tmp_path / "split.json")))
    win.show()
    for _ in range(3):
        app.processEvents()
    assert win.cb_timestamp.isChecked()           # 默认勾选（对齐 SSCOM）
    win._on_rx("adc_value:2560\r\nNO CARD\r\n".encode("gbk"))
    win.builder.flush()
    lines = win.rxview.plain_text().splitlines()
    assert lines[0].startswith("[") and "收←◆" in lines[0]   # 包首行：前缀+标识
    assert lines[1] == "NO CARD"                  # 续行不带标识（对齐 SSCOM 参照图）
    win.cb_timestamp.setChecked(False)            # 取消勾选：显示纯文本
    for _ in range(3):
        app.processEvents()
    win._on_rx("adc_value:2567\r\nNO CARD\r\n".encode("gbk"))
    win.builder.flush()
    t2 = win.rxview.plain_text()
    assert "收←◆" not in t2 and "发→◇" not in t2
    assert not any(ln.startswith("[") for ln in t2.splitlines())  # 前缀也不显示
    assert "adc_value:2560" in t2 and "adc_value:2567" in t2      # 数据本身不丢
    win.cb_timestamp.setChecked(True)             # 重新勾选：旧行注解恢复
    for _ in range(3):
        app.processEvents()
    t3 = win.rxview.plain_text().splitlines()
    assert "收←◆" in t3[0] and t3[1] == "NO CARD"


# ---- SSCOM 风格: 状态栏每个模块带清晰边框 + 校验配置前有竖分隔线 ----
def test_status_seg_boxes_and_chk_separator(tmp_path):
    from app.ui import theme as _theme
    win = MainWindow(Settings(str(tmp_path / "seg.json")))
    win.show()
    for _ in range(3):
        app.processEvents()
    for lbl in (win.sb_state, win.sb_tx, win.sb_rx, win.sb_link):
        assert lbl.objectName() == "sbSeg"        # QSS 按此画清晰边框
    for name in ("light", "dark"):
        q = _theme.qss(name)
        assert "QLabel#sbSeg" in q and "border: 1px solid" in q
    # 校验配置前的竖分隔线（静置在行内）
    from PySide2.QtWidgets import QFrame
    assert win._chk_sep.frameShape() == QFrame.VLine
    assert win._chk_sep.parent() is not None


# ---- 复审 P3: 启动即显示校验值预览（信号未连接时也要初始化） ----
def test_checksum_view_initialized_from_config(tmp_path):
    from app.core.checksum import checksum_appendix, checksum_body_slice
    # 旧档位配置（无 v2 标记）：9 会按旧语义迁移，正好覆盖启动取值路径
    cfg = tmp_path / "ck.json"
    cfg.write_text('{"checksum_kind": "add8", "checksum_tail": 9,'
                   ' "main_text": "abc"}', encoding="utf-8")
    win = MainWindow(Settings(str(cfg)))
    win.show()
    for _ in range(3):
        app.processEvents()
    shown = win.edit_checksum_view.text()
    assert shown, "启动后校验值预览应已按配置初始化"
    # 期望值按运行时控件状态推导（同一套公开接口，和真实发送一致）
    data = win._build_payload(win.send_edit.toPlainText(),
                              win.cb_send_hex.isChecked(),
                              win.cb_newline.isChecked(), False)
    body = checksum_body_slice(data, win.sp_chk_start.value(),
                               win.cmb_chk_place.currentData())
    expect = " ".join("%02X" % b for b in
                      checksum_appendix(win.cmb_checksum.currentData(), body))
    assert shown == expect
    # 无校验配置时保持为空
    cfg2 = tmp_path / "ck2.json"
    cfg2.write_text('{"checksum_kind": "none", "main_text": "abc"}',
                    encoding="utf-8")
    win2 = MainWindow(Settings(str(cfg2)))
    win2.show()
    for _ in range(3):
        app.processEvents()
    assert win2.edit_checksum_view.text() == ""


# ---- 复审 P2: 保存参数/导入多条配置不得静默停止循环发送 ----
def test_save_settings_keeps_ms_loop(tmp_path):
    win = MainWindow(Settings(str(tmp_path / "loop.json")))
    win.show()
    for _ in range(3):
        app.processEvents()
    win.ms_model.rows = [{"note": "t", "hex": False, "text": "AA",
                          "order": 1, "interval": 30}]
    win.cb_ms_loop.setChecked(True)           # 触发启动
    for _ in range(3):
        app.processEvents()
    assert win.ms_loop.is_active() is True
    win._save_settings(silent=True)           # 保存参数
    assert win.cb_ms_loop.isChecked() is True
    assert win.ms_loop.is_active() is True, "保存后循环发送不应停止"
    # 导入多条配置路径同样复用 _save_settings
    win._save_settings(silent=True)
    assert win.ms_loop.is_active() is True


# ---- 多条字符串发送：功能键顺序 + - 循环发送 多条发送说明 导入sscom.ini ----
def test_ms_row_button_order(tmp_path):
    win = MainWindow(Settings(str(tmp_path / "msrow.json")))
    win.show()
    for _ in range(3):
        app.processEvents()
    mv = win.tabs.widget(0).layout()          # 多条字符串发送 tab
    row = None
    for i in range(mv.count()):
        it = mv.itemAt(i)
        if it.layout() is not None and it.layout().indexOf(win.btn_ms_add) >= 0:
            row = it.layout()
    assert row is not None
    order = [row.itemAt(i).widget() for i in range(row.count())
             if row.itemAt(i).widget() is not None]
    assert order == [win.btn_ms_add, win.btn_ms_del, win.cb_ms_loop,
                     win.btn_ms_help, win.btn_ms_import]


# ---- 显示缓冲上限：默认 5M、上限 50M（2026-10-20 放宽；配置超限自动收敛）----
def test_buffer_sizes_capped_50m_for_32bit(tmp_path):
    """32 位分支：菜单上限 50M（原 20M，2026-10-20 用户要求放宽）、默认 5M；
    两层都收敛——settings 归一化与开窗时的 _clamp_cap（实测见 tools/mem_probe.py）。
    注：该上限只管原始字节（*.bin 导出量/内存）；接收区回看深度由行表 50 万行决定。"""
    from app.core.settings import BUF_CAP_DEFAULT, BUF_CAP_MAX, BUF_CAP_MIN
    from app.ui.mainwindow import BUF_SIZES, _MAX_BUF
    names = [n for n, _ in BUF_SIZES]
    assert "50M" in names and "200K" not in names and "128M" not in names
    assert max(s for _, s in BUF_SIZES) == _MAX_BUF == BUF_CAP_MAX == 52_428_800
    assert min(s for _, s in BUF_SIZES) == BUF_CAP_MIN == 500_000
    assert Settings(str(tmp_path / "fresh.json")).d["buffer_cap"] == BUF_CAP_DEFAULT
    assert BUF_CAP_DEFAULT == 5_242_880 and "5M" in names   # 默认值正好是菜单里的 5M 档
    p = tmp_path / "big.json"
    p.write_text('{"buffer_cap": 1073741824}', encoding="utf-8")
    s = Settings(str(p))
    assert s.d["buffer_cap"] == BUF_CAP_MAX      # settings 层就钳到上界
    p2 = tmp_path / "tiny.json"
    p2.write_text('{"buffer_cap": 200000}', encoding="utf-8")
    assert Settings(str(p2)).d["buffer_cap"] == BUF_CAP_MIN   # 删除的 200K 档收敛到 500K
    win = MainWindow(s)                          # 开窗再兜一层，菜单勾选态同步
    assert win.cfg.d["buffer_cap"] == _MAX_BUF
    assert win.buf.cap == _MAX_BUF
    assert win.buf_actions[_MAX_BUF].isChecked()


def test_rx_oom_degrades_gracefully(tmp_path, monkeypatch):
    """32 位内存耗尽不许把 MemoryError 抛回 Qt 槽：先降档重试，仍失败只丢这一包。"""
    win = MainWindow(Settings(str(tmp_path / "oom.json")))
    win.show()
    for _ in range(3):
        app.processEvents()
    win._set_buffer_cap(268_435_456)                 # 超上限的请求被钳到 50M 档
    assert win.buf.cap == 52_428_800
    fail = {"n": 1}
    real_append = win.buf.append

    def maybe_fail(data):
        if fail["n"] > 0:
            fail["n"] -= 1
            raise MemoryError
        return real_append(data)

    monkeypatch.setattr(win.buf, "append", maybe_fail)
    win._on_rx(b"AAAA")                              # 首次失败 → 降档重试成功
    assert win.buf.cap == 26_214_400                 # 50M 减半
    assert win.cfg.d["buffer_cap"] == 26_214_400
    assert b"AAAA" in win.buf.read(win.buf.start_abs, 64)
    fail["n"] = 99                                   # 之后一律失败 → 丢包不崩
    win._on_rx(b"BBBB")
    assert win.buf.cap == 13_107_200                  # 再降一档
    assert b"BBBB" not in win.buf.read(win.buf.start_abs, 64)
    assert "内存不足" in win.sb_link.text()   # OOM 提示走第4框临时通知


def test_rx_oom_feed_failure_no_double_append(tmp_path, monkeypatch):
    """回归（审查 P2-2）：builder.feed 抛 MemoryError 降档重试时，不得把同一包
    字节再次 append 进接收缓冲（否则 *.bin 导出与显示都会出现两份）。"""
    win = MainWindow(Settings(str(tmp_path / "oom2.json")))
    win.show()
    for _ in range(3):
        app.processEvents()
    win._set_buffer_cap(52_428_800)
    fail = {"n": 1}
    real_feed = win.builder.feed

    def maybe_fail(*a, **k):
        if fail["n"] > 0:
            fail["n"] -= 1
            raise MemoryError
        return real_feed(*a, **k)

    monkeypatch.setattr(win.builder, "feed", maybe_fail)
    win._on_rx(b"ZZZZZZZZ")                  # append 成功、feed 失败 → 降档重试
    assert win.buf.cap == 26_214_400         # 50M 减半
    assert win.buf.read(win.buf.start_abs, 64) == b"ZZZZZZZZ"   # 只有一份
    assert win.rx_bytes == 8                 # 字节在缓冲里：计数一致（审查 N3）


# ---- 窄窗模式：向左缩到配置框宽度，右侧发送/选项区隐藏（对齐 SSCOM）----
def test_narrow_mode_clips_right_panel(tmp_path):
    """SSCOM 式缩窗：右侧不隐藏、保持自然宽度被窗口边缘截断，左块永不压缩。"""
    win = MainWindow(Settings(str(tmp_path / "narrow.json")))
    win.show()
    for _ in range(3):
        app.processEvents()
    win.resize(1000, 620)
    for _ in range(3):
        app.processEvents()
    assert win.send_edit.isVisible() and win.sb_link.isVisible()
    win.resize(340, 620)
    for _ in range(5):
        app.processEvents()
    assert win.width() == 340                    # 能缩到配置框宽度附近
    assert win.send_edit.isVisible()             # 发送输入框不再隐藏（压缩后随区截断）
    assert not win.sb_link.isVisible()           # 状态栏连接信息让位
    assert win.cb_break.isVisible()              # 配置列（线路行）保留
    assert win.cmb_baud.isVisible() and win.btn_link.isVisible()
    # 左块钉在自然宽度：串口列、工具行左组、文件块首键都不被压缩
    assert win.serial_box.width() == win.btn_send.width()
    assert win.btn_clear.width() == win.btn_clear.sizeHint().width()
    assert win.cb_auto_scroll.width() == win.cb_auto_scroll.sizeHint().width()
    assert win.btn_open_file.width() == win.btn_open_file.sizeHint().width()
    # 右侧内容溢出窗口边缘：截断而非隐藏（最右键超出窗口宽度）
    assert (win.btn_expand.mapTo(win, win.btn_expand.rect().topLeft()).x()
            + win.btn_expand.width() > 340)
    win.resize(1000, 620)                         # 展宽恢复
    for _ in range(5):
        app.processEvents()
    assert win.send_edit.isVisible() and win.sb_link.isVisible()


def test_shrink_freezes_canvas(tmp_path):
    """V0.0.5：按钮类控件位置绝对恒定（收缩裁右边、拉宽右侧留空，均不移位）；
    显示框/发送框跟随窗口伸缩。"""
    win = MainWindow(Settings(str(tmp_path / "freeze.json")))
    win.show()
    QTest.qWait(2700)                  # 等启动沉降末拍(2500ms)走完，避免竞态重冻结
    for _ in range(3):
        app.processEvents()
    win.resize(1250, 700)
    for _ in range(3):
        app.processEvents()
    win._final_settle()
    for _ in range(3):
        app.processEvents()
    m = lambda w: w.mapTo(win, w.rect().topLeft()).x()
    x_open = m(win.btn_open_file)
    x_collapse = m(win.btn_collapse)
    x_expand = m(win.btn_expand)
    x_chk = m(win.edit_checksum_view)
    w_send = win.send_edit.width()
    # 收缩：所有按钮原位，发送框/显示框跟随压缩
    win.resize(1000, 700)
    for _ in range(5):
        app.processEvents()
    assert m(win.btn_open_file) == x_open, "文件块不得移位"
    assert m(win.btn_collapse) == x_collapse, "右侧按钮组不得移位"
    assert m(win.btn_expand) == x_expand, "右侧按钮组不得移位"
    assert m(win.edit_checksum_view) == x_chk, "选项组不得移位"
    assert win.send_edit.width() < w_send, "发送框应跟随压缩"
    assert win.rxview.width() == win.width() - 8, "显示框应跟随视口宽"
    # 拉宽：所有按钮仍原位（右侧留空），发送框/显示框跟随变宽
    win.resize(1500, 700)
    for _ in range(5):
        app.processEvents()
    assert m(win.btn_collapse) == x_collapse and m(win.btn_expand) == x_expand, \
        "拉宽时按钮组也不得移位"
    assert m(win.btn_open_file) == x_open and m(win.edit_checksum_view) == x_chk
    assert win.send_edit.width() > w_send, "发送框应跟随变宽"
    assert win.rxview.width() == win.width() - 8, "显示框应跟随视口宽"


def test_expand_panel_separate_window(tmp_path):
    """V0.0.5：扩展面板是独立小窗——弹出后贴主窗右缘（右缘贴屏时自动
    夹回屏幕内/改弹左侧），× 直接关闭同步主窗按钮；显示框与主界面布局
    完全不受影响。"""
    win = MainWindow(Settings(str(tmp_path / "expand.json")))
    win.show()
    QTest.qWait(2700)                  # 等启动沉降末拍(2500ms)走完，避免竞态重冻结
    for _ in range(3):
        app.processEvents()
    win.resize(1250, 700)
    for _ in range(3):
        app.processEvents()
    win._final_settle()
    for _ in range(3):
        app.processEvents()
    w0 = win.rxview.width()
    avail = win.screen().availableGeometry()

    def in_screen():
        return avail.left() <= win.right.x() <= avail.right() - win.right.width() + 1

    win._toggle_expand(True)
    for _ in range(3):
        app.processEvents()
    assert win.right.isVisible() and win.right.isWindow(), "扩展应为独立小窗"
    assert in_screen(), "小窗应贴主窗右缘（贴屏时夹回屏幕内）"
    assert win.rxview.width() == w0, "显示框不受影响"
    win.move(60, 40)                       # 主窗移动 → 小窗跟随（贴屏时夹取）
    for _ in range(3):
        app.processEvents()
    assert in_screen(), "主窗移动后小窗仍应在屏幕内"
    # 用户点小窗 × 直接关闭 → 主窗按钮同步恢复"扩展"
    win.right.close()
    for _ in range(3):
        app.processEvents()
    assert not win.right.isVisible()
    assert not win.btn_expand.isChecked(), "关闭小窗后扩展按钮应恢复"
    # 再次打开（经按钮触发，勾选态真实）：高度与主窗客户区一致
    win.btn_expand.setChecked(True)       # toggled → _toggle_expand(True)
    for _ in range(3):
        app.processEvents()
    assert win.right.isVisible() and win.right.height() == win.height()
    # 主窗右缘贴屏（最大化）时小窗夹回屏幕内，不得落到屏幕外（二十四轮 F1）
    win.showMaximized()
    QTest.qWait(200)
    win._reposition_ext_window(keep_size=True)
    for _ in range(3):
        app.processEvents()
    assert in_screen(), "最大化时小窗应夹取在屏幕内"
    win.showNormal()
    QTest.qWait(100)
    # 主窗缩放时小窗跟随：高度与主窗客户区一致（二十八轮 F1）
    win.resize(1250, 900)
    QTest.qWait(100)
    for _ in range(3):
        app.processEvents()
    assert win.right.height() == win.height(), "主窗拉高后小窗应同高"
    # 主窗最小化时小窗隐藏、恢复时按按钮状态重新显示（二十八轮 F3）
    win.showMinimized()
    for _ in range(5):
        app.processEvents()
    assert not win.right.isVisible(), "最小化时小窗应隐藏"
    win.showNormal()
    for _ in range(5):
        app.processEvents()
    assert win.right.isVisible() and win.btn_expand.isChecked(), "恢复后小窗应重新显示"
    win.btn_expand.setChecked(False)      # toggled → _toggle_expand(False)
    for _ in range(3):
        app.processEvents()
    assert not win.right.isVisible()
    assert win.rxview.width() == w0, "收起后显示框仍不变"


# ---- 复审 P3: 运行中条目清空/order 全为 0，调度器自停须同步取消勾选 ----
def test_ms_loop_autostop_syncs_checkbox(tmp_path):
    win = MainWindow(Settings(str(tmp_path / "loop2.json")))
    win.show()
    for _ in range(3):
        app.processEvents()
    win.ms_model.rows = [{"note": "t", "hex": False, "text": "AA",
                          "order": 1, "interval": 30}]
    win.cb_ms_loop.setChecked(True)
    for _ in range(3):
        app.processEvents()
    assert win.ms_loop.is_active() and win.cb_ms_loop.isChecked()
    win.ms_model.rows[0]["order"] = 0        # 运行中全部条目失效
    win.ms_loop._send_next()                 # 等价于下一次定时器触发
    assert not win.ms_loop.is_active()
    assert not win.cb_ms_loop.isChecked(), "调度器自停后复选框必须同步取消"


# ---- 复审 P3: 窄窗下查找条隐藏（无可用宽度），展宽恢复 ----
def test_narrow_hides_find_bar(tmp_path):
    win = MainWindow(Settings(str(tmp_path / "narrow_find.json")))
    win.show()
    for _ in range(3):
        app.processEvents()
    win.resize(1000, 620)
    for _ in range(3):
        app.processEvents()
    win.find_bar.activate()
    assert win.find_bar.isVisible()
    win.resize(340, 620)                     # 缩窄 → 查找条让位
    for _ in range(5):
        app.processEvents()
    assert not win.find_bar.isVisible()
    win.resize(1000, 620)                     # 展宽 → 恢复原打开状态
    for _ in range(5):
        app.processEvents()
    assert win.find_bar.isVisible()
    win.resize(340, 620)                     # 窄窗下再点查找：不开条
    for _ in range(5):
        app.processEvents()
    win._activate_find()
    assert not win.find_bar.isVisible()


# ================= PySide2 适配回归：菜单真实触发 =================
# 背景（docs/04-Win7-32位适配说明.md）：PySide2 的 QAction.triggered **不传参数**（PySide6 会传
# checked）。槽若要求必填首参，触发即抛 TypeError 且**静默不执行**——现象就是
# "通讯端口菜单切不了模式""显示缓冲上限点了没反应"。offscreen 测试不覆盖菜单
# 点击路径，故这里补两道闸：静态签名守卫 + 真实 trigger() 行为验证。

_OK_MARK = "PYCOM-OK-TRIGGERED-ARG"   # 豁免标记（QActionGroup 等必传参信号用）


def _required_params_after(slot, skip):
    """槽在 self 之后、跳过前 skip 个位置参数后，还剩下的必填参数名。"""
    import inspect
    params = list(inspect.signature(slot).parameters.values())[1:][skip:]
    return [p.name for p in params
            if p.default is p.empty
            and p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                           inspect.Parameter.POSITIONAL_OR_KEYWORD)]


def _triggered_arg_problem(arg, path, lineno):
    """检查一个 triggered.connect(...) 实参能否零参调用，返回问题描述或 None。"""
    import ast
    if isinstance(arg, ast.Lambda):
        pos = arg.args.posonlyargs + arg.args.args
        if pos and len(arg.args.defaults) < len(pos):
            return "%s:%d lambda 首参『%s』没有默认值" % (path, lineno, pos[0].arg)
    elif isinstance(arg, ast.Attribute):                     # 具名槽 self._foo
        slot = getattr(MainWindow, arg.attr, None)
        if slot is not None:
            miss = _required_params_after(slot, 0)
            if miss:
                return "%s:%d %s() 还需要参数 %s" % (path, lineno, arg.attr, miss)
    elif (isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name)
          and arg.func.id == "partial" and arg.args
          and isinstance(arg.args[0], ast.Attribute)):
        target = getattr(MainWindow, arg.args[0].attr, None)
        if target is not None:
            miss = _required_params_after(target, len(arg.args) - 1)
            if miss:
                return "%s:%d partial(%s, ...) 余下必填参数 %s" % (
                    path, lineno, arg.args[0].attr, miss)
    return None


def test_menu_triggered_slots_accept_zero_args():
    """静态守卫：触发型菜单槽必须能零参调用（标记行可豁免）。"""
    import ast
    import io
    root = os.path.join(os.path.dirname(__file__), "..", "app")
    offenders = []
    for dirpath, _dirs, names in os.walk(root):
        for name in sorted(names):
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            with io.open(path, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
            tree = ast.parse("\n".join(lines), path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                if not (isinstance(fn, ast.Attribute) and fn.attr == "connect"
                        and isinstance(fn.value, ast.Attribute)
                        and fn.value.attr == "triggered"):
                    continue
                # 标记写在连接语句上一两行内也算（QActionGroup 这类豁免）
                seg = "\n".join(lines[max(0, node.lineno - 3):node.end_lineno])
                if _OK_MARK in seg:
                    continue
                for arg in node.args:
                    bad = _triggered_arg_problem(arg, path, node.lineno)
                    if bad:
                        offenders.append(bad)
    assert not offenders, (
        "下列槽无法零参调用（PySide2 下菜单会点了没反应）:\n  "
        + "\n  ".join(offenders))


def test_mode_menu_trigger_switches_mode(tmp_path):
    """通讯端口菜单四种模式逐个"真实触发"，必须切过去并切换串口/网络页。"""
    from app.ui.mainwindow import LINK_MODES
    win = MainWindow(Settings(str(tmp_path / "mode_menu.json")))
    win.show()
    for _ in range(3):
        app.processEvents()
    assert set(win.net_actions) == {m for _n, m in LINK_MODES}
    for name, mode in LINK_MODES:
        win.net_actions[mode].trigger()
        for _ in range(2):
            app.processEvents()
        assert win._mode == mode, "点击『%s』后模式应为 %s" % (name, mode)
        assert win.net_actions[mode].isChecked()
        assert win.serial_box.isVisible() == (mode == "serial")
        assert win.net_box.isVisible() == (mode != "serial")


def test_view_and_encoding_menu_triggers_apply(tmp_path):
    """显示缓冲上限 / 编码菜单真实触发即生效（同一类 triggered 坑的回归点）。"""
    win = MainWindow(Settings(str(tmp_path / "view_menu.json")))
    win.show()
    for _ in range(3):
        app.processEvents()
    for size in (500_000, 2_097_152, 20_971_520):
        win.buf_actions[size].trigger()
        assert win.cfg.d["buffer_cap"] == size
        assert win.buf.cap == size
        assert win.buf_actions[size].isChecked()
    for enc in ("utf-8", "big5"):
        win.enc_actions[enc].trigger()
        assert win._encoding == enc
        assert win.enc_actions[enc].isChecked()


def test_file_delay_group_trigger_writes_config(tmp_path):
    """发送文件延时菜单挂在 QActionGroup 上：该信号只有"带 action"一个签名，
    PySide2 必传参，故 lambda act: ... 合法（就是上面标记豁免的那种写法）。"""
    win = MainWindow(Settings(str(tmp_path / "delay_menu.json")))
    win.show()
    for _ in range(3):
        app.processEvents()
    win.act_file_delays[10].trigger()
    assert win.cfg.d["file_delay_ms"] == 10
    assert win.act_file_delays[10].isChecked()
    win.act_file_delays[0].trigger()
    assert win.cfg.d["file_delay_ms"] == 0


# ---- Qt5 菜单勾选标记：定稿 = 行尾 `●`（tab 右对齐成列，条目前缀不动）----
def test_menu_text_marker(tmp_path):
    """勾选标记终版（2026-10-16）：真机 Win7 分数缩放（125%/150%）下一切
    勾号图形发虚/裁剪（原生 indicator、自绘位图、图标占位），行首前置标记
    （几何字符/全角字符）也无法对齐——`●` 改放**行尾**：条目文本前缀不动
    （汉字天然对齐），列对齐用 Qt 制表符右对齐机制（`文字\t\u3000●`，
    快捷键同布局），标记与文字间距 ≥ 全角空格（≥ 两空格）。守回归：
    ①条目一律无图标；②QSS 归零 QMenu::indicator；③弹出前按勾选态刷新
    行尾标记；④未勾选/不可勾选条目无标记。"""
    win = MainWindow(Settings(str(tmp_path / "menu_mark.json")))
    assert len(win._menu_list) >= 9
    for m in win._menu_list:
        for a in m.actions():
            assert a.icon().isNull(), "菜单条目不得设图标（真机发虚/裁剪弹窗）"
            assert "\t" not in a.text(), "原始文本不带标记，由弹出前刷新写入"
    theme_act = win.act_theme
    menu = [m for m in win._menu_list
            if any(a is theme_act for a in m.actions())][0]
    theme_act.setChecked(True)
    menu.aboutToShow.emit()                  # 弹出前刷新 → 勾选项行尾带 ●
    assert theme_act.text().endswith("\t\u3000●")
    assert theme_act.text().split("\t")[0] == "深色模式"
    theme_act.setChecked(False)
    menu.aboutToShow.emit()
    assert "\t" not in theme_act.text()
    assert theme_act.text() == "深色模式"             # 前缀/后缀完全还原
    plain = [m for m in win._menu_list if not any(
        a.isCheckable() for a in m.actions() if not a.isSeparator())]
    assert plain and all("\t" not in a.text()
                         for m in plain for a in m.actions() if not a.isSeparator())
    # 全刷兜底（menu=None → findChildren 重取，覆盖后建/懒加载菜单）
    win._refresh_menu_markers()
    assert "\t" not in theme_act.text()
    theme_act.setChecked(True)
    win._refresh_menu_markers()
    assert theme_act.text().endswith("\t\u3000●")
    from app.ui import theme as _theme
    import re as _re
    q = _theme.qss("light") + "\n" + _theme.qss("dark")
    assert _re.search(r"QMenu::indicator\s*\{[^}]*width:\s*0", q), \
        "QSS 必须归零 QMenu::indicator（否则勾号列回归、混合菜单错位）"
    # 含子菜单的菜单恢复 24px 右缓冲（QSS 属性选择器），防 ▶ 压到长文字；
    # 纯条目菜单保持 8px 让 ● 贴边
    assert _re.search(r'QMenu\[submenu="true"\]::item', q), \
        "QSS 须含子菜单菜单的右缓冲规则"
    titles = {m.title().replace("&", ""): m for m in win._menu_list}
    assert titles["发送"].property("submenu") is True, "含子菜单的菜单须打属性标"
    assert titles["显示"].property("submenu") is True, "显示含缓冲上限子菜单"
    assert not titles["编码"].property("submenu"), "纯条目菜单不打属性标"


# ---- 串口被占用/重连等待期：按钮转「取消」，点击中止等待并完整还原 ----
def test_link_pending_cancel_button(tmp_path):
    """等待期（会重试，黄灯"重连中…"轮询）按钮转「取消」，点击即
    link.close() 中止轮询并完整还原——此前等待无法取消。不会重试的失败
    （未开自动重连/服务端侦听失败/UDP 绑定失败）按钮保持「打开」，
    直接再点即重试，不出现无谓的「取消」。"""
    win = MainWindow(Settings(str(tmp_path / "cancel.json")))
    win._mode = "serial"
    # 不会重试的失败（服务端侦听失败/UDP 绑定失败/未开自动重连）：
    # 按钮保持「打开」，pending 不置位——直接再点一次就是重试
    win._on_state("error: 监听失败(端口被占用)")
    assert not win._link_pending and win.btn_link.text() == "打开串口"
    assert win.sb_state.text() == "未连接"
    # 会重试（串口被占用且开了自动重连）→ reconnecting 态：按钮转「取消」
    win._on_state("reconnecting")
    assert win._link_pending and win.btn_link.text() == "取消"
    # 等待期点击 → 取消：link 关闭、pending 复位、按钮还原
    class _Fake:
        closed = False

        def is_open(self):
            return False

        def close(self):
            self.closed = True

        def deleteLater(self):
            pass

    fake = _Fake()
    win.link = fake
    win._toggle_link()
    assert fake.closed and win.link is None and not win._link_pending
    assert win.btn_link.text() == "打开串口"


# ---- 接收区鼠标选择复制：选区文本 / 坐标映射 / 复制所选 ----
def test_rxview_selection_copy(tmp_path, monkeypatch):
    win = MainWindow(Settings(str(tmp_path / "sel.json")))
    win._on_rx(b"ABCDEFGH\r\n")
    win._on_rx(b"0123456789\r\n")
    win.builder.split = False   # 关闭时间戳分包：正文顶格，选区映射基准 base=4
    win.builder.flush()
    v = win.rxview
    v.resize(400, 240)
    v.viewport().setGeometry(0, 0, 392, 236)   # 未进事件循环，视口几何手动给足
    v.verticalScrollBar().setValue(v.builder.base)
    # 选区文本：跨行 (0,2)→(1,3)，按逻辑行拼接
    v._sel_anchor = (0, 2)
    v._sel_focus = (1, 3)
    assert v.has_selection() and v._selected_text() == "CDEFGH\n012"
    from app.ui import rxview as rxview_mod
    clip = _ClipStub()
    monkeypatch.setattr(rxview_mod, "QApplication", _QAppStub(clip))
    v.copy_selection()
    assert clip.text() == "CDEFGH\n012"
    # 坐标映射：与绘制同源的等宽字格——行 0 第 c 列字符中心 → (0, c)
    vp = v.viewport()
    lh = v._line_h()
    top = v.verticalScrollBar().value()
    cw = max(1, v._fms["rx"].horizontalAdvance("M"))
    y0 = vp.rect().top() + 2 + (0 - top) * lh + lh // 2
    assert v._pos_to_point(QPoint(4 + cw // 2, y0)) == (0, 0)
    assert v._pos_to_point(QPoint(4 + cw * 5 + cw // 2, y0)) == (0, 5)
    # 清空显示 → 选区复位
    v.clear_all()
    assert not v.has_selection() and v._selected_text() == ""


# ---- 选区拖拽边缘自动滚屏：可跨屏选择屏幕外内容 ----
def test_rxview_selection_autoscroll(tmp_path):
    win = MainWindow(Settings(str(tmp_path / "autoscroll.json")))
    for i in range(30):
        win._on_rx(b"LINE%02d\r\n" % i)
    win.builder.flush()
    v = win.rxview
    v.resize(400, 240)
    v.viewport().setGeometry(0, 0, 392, 236)
    v._update_range()   # 滚动条范围需按行数初始化（测试无 _tick/resize）
    v.verticalScrollBar().setValue(10)
    v._selecting = True
    v._sel_anchor = (10, 0)
    v._sel_focus = (10, 2)
    # 光标在顶边内侧 → 自动向上滚，焦点行号前移
    v._sel_cursor_x = 100
    v._sel_cursor_y = v.viewport().rect().top() + 4
    v._sel_autoscroll()
    assert v.verticalScrollBar().value() < 10
    assert v._sel_focus[0] < 10
    # 光标移到底边内侧 → 自动向下滚（回拨时钟模拟 50ms 间隔）
    import time as _time
    v._autosel_t = _time.perf_counter() - 0.05
    up_val = v.verticalScrollBar().value()
    v._sel_cursor_y = v.viewport().rect().bottom() - 2
    v._sel_autoscroll()
    assert v.verticalScrollBar().value() > up_val


# ---- 折行行的命中映射（十四轮 X1）：列含折行段偏移、行号按实际视觉行回推 ----
def test_rxview_selection_wrapped_mapping(tmp_path):
    win = MainWindow(Settings(str(tmp_path / "wrap.json")))
    v = win.rxview
    v.resize(400, 240)
    v.viewport().setGeometry(0, 0, 392, 236)
    cw = max(1, v._fms["rx"].horizontalAdvance("M"))
    cols = max(8, (392 - 8) // cw)
    text = "A" * cols + "DEF" + "Z" * (200 - cols - 3)     # 200 字符：第 2 视觉行以 DEF 开头
    win._on_rx((text + "\r\n").encode("gbk"))              # 行须收尾，否则下包续接同行
    win.builder.flush()
    win._on_rx(b"BBB\r\n")
    win.builder.flush()
    v._update_range()
    v.verticalScrollBar().setValue(v.builder.base)
    lh = v._line_h()
    y0 = v.viewport().rect().top() + 2
    n_rows = (len(text) + cols - 1) // cols
    # 第二视觉行首字符（'D'）：旧实现返回 (0,0)（映射到 'A'）
    assert v._pos_to_point(QPoint(4 + cw // 2, y0 + lh + lh // 2)) == (0, cols)
    # 从 'D' 拖选 3 字符：复制恰好是 DEF（s=cols≠0，不带行首注记）
    v._sel_anchor, v._sel_focus = (0, cols), (0, cols + 3)
    assert v._selected_text() == "DEF"
    # 长行占 n_rows 个视觉行，其后 BBB 回推到逻辑行 1（旧实现按 (i-top)*lh 串行错位）
    y_bbb = y0 + n_rows * lh + lh // 2
    assert v._pos_to_point(QPoint(6, y_bbb)) == (1, 0)
    # 长行首行（r=0）仍按注记区偏移映射：正文第 1 列的 x → 列 1
    fm = v._fms["rx"]
    la = win.builder.line_at(0)
    base_x = 4 + fm.horizontalAdvance(la.prefix) + fm.horizontalAdvance("收←◆")
    assert v._pos_to_point(QPoint(base_x + cw + cw // 2, y0 + lh // 2)) == (0, 1)

# ---- 转换计算器：HEX 小端/大端字节序互转 ----
def test_calculator_endian(tmp_path):
    from app.ui.calculator_dlg import CalculatorWidget
    w = CalculatorWidget()
    # 数值驱动：0x12345678 → BE 自然序 / LE 逆序
    w._recompute(w._from_hex, "十六进制 HEX", "12345678")
    assert w.fields["HEX 大端 BE"].text() == "12 34 56 78"
    assert w.fields["HEX 小端 LE"].text() == "78 56 34 12"
    # 小端字段输入反推数值
    w._recompute(w._from_hexle, "HEX 小端 LE", "78 56 34 12")
    assert w.fields["十六进制 HEX"].text() == "12345678"
    # 大端字段输入反推数值
    w._recompute(w._from_hexbe, "HEX 大端 BE", "12 34 56 78")
    assert w.fields["HEX 小端 LE"].text() == "78 56 34 12"
    # 单字节与 0 值
    w._recompute(w._from_dec, "十进制 DEC", "0")
    assert w.fields["HEX 小端 LE"].text() == "00"
    w._recompute(w._from_dec, "十进制 DEC", "255")
    assert w.fields["HEX 小端 LE"].text() == "FF"
    assert w.fields["HEX 大端 BE"].text() == "FF"


# ---- 复制/保存含时间戳和收发标记：选区/全部文本/保存文件（与显示设置独立）----
def _feed_two_packets(win):
    """灌入两个独立收包（各带时间戳前缀），返回两行。"""
    win._on_rx(b"AAAA\r\n")
    win.builder.flush()                     # 包 1
    win._on_rx(b"BBBB\r\n")
    win.builder.flush()                     # 包 2
    return win.builder.visible()


def test_rxview_copy_anno_selection_and_all(tmp_path, monkeypatch):
    from app.core.display import dir_marker
    win = MainWindow(Settings(str(tmp_path / "anno.json")))
    la, lb = _feed_two_packets(win)
    v = win.rxview
    assert v.copy_with_anno and v.save_with_anno      # 默认含（与显示默认一致）
    # 关闭显示分包：显示层不带注记，复制选项仍能带出（选项与显示独立）
    win.builder.split = False
    assert v.plain_text() == "AAAA\nBBBB"
    assert v.plain_text(False) == "AAAA\nBBBB"
    with_anno = v.plain_text(True)
    assert with_anno == (la.prefix + dir_marker("rx") + "AAAA\n"
                         + lb.prefix + dir_marker("rx") + "BBBB")
    # 选区复制：全行选中 → 行首注记随行带出
    v.resize(400, 240)
    v.viewport().setGeometry(0, 0, 392, 236)
    v._sel_anchor, v._sel_focus = (0, 0), (1, 4)
    assert v._selected_text() == with_anno
    from app.ui import rxview as rxview_mod
    clip = _ClipStub()
    monkeypatch.setattr(rxview_mod, "QApplication", _QAppStub(clip))
    v.copy_selection()
    assert clip.text() == with_anno
    # 半途起选（列 > 0）的行不带注记——用户没选到行首
    v._sel_anchor = (0, 2)
    assert v._selected_text() == ("AA\n" + lb.prefix + dir_marker("rx") + "BBBB")
    # 关闭复制选项：纯正文（与显示开关无关）
    v.copy_with_anno = False
    v._sel_anchor = (0, 0)
    assert v._selected_text() == "AAAA\nBBBB"


def test_save_rx_text_respects_save_anno(tmp_path):
    win = MainWindow(Settings(str(tmp_path / "save_anno.json")))
    _feed_two_packets(win)
    out = tmp_path / "out.txt"
    win._save_rx(str(out))                              # 默认含注记
    data = out.read_text(encoding="gbk")
    assert "收←◆AAAA" in data and data.startswith("[")
    win.rxview.save_with_anno = False                   # 关闭后：纯正文
    win._save_rx(str(out))
    assert out.read_text(encoding="gbk") == "AAAA\nBBBB"


def test_rxview_menu_toggles_anno(tmp_path):
    """右键菜单两项：点击切换开关，勾选态 = 行尾 ●（同主窗口菜单约定）。"""
    win = MainWindow(Settings(str(tmp_path / "menu_anno.json")))
    v = win.rxview
    texts = [a.text() for a in v._build_menu().actions()]
    # 默认勾选：两项行尾带 ●
    assert "复制含时间戳和收发标记\t\u3000●" in texts
    assert "保存含时间戳和收发标记\t\u3000●" in texts
    v._menu_action(v._act_canno)               # 点击"复制"项 → 关闭
    assert not v.copy_with_anno and v.save_with_anno
    texts = [a.text() for a in v._build_menu().actions()]
    assert "复制含时间戳和收发标记" in texts and \
        "复制含时间戳和收发标记\t\u3000●" not in texts
    v._menu_action(v._act_canno)               # 再点 → 重新打开
    assert v.copy_with_anno
    texts = [a.text() for a in v._build_menu().actions()]
    assert "复制含时间戳和收发标记\t\u3000●" in texts
    # 保存项独立切换；菜单取消（None）不改状态
    v._menu_action(v._act_sanno)
    assert not v.save_with_anno and v.copy_with_anno
    v._menu_action(None)
    assert not v.save_with_anno


def test_anno_settings_roundtrip(tmp_path):
    p = tmp_path / "anno_cfg.json"
    win = MainWindow(Settings(str(p)))
    assert win.rxview.copy_with_anno is True and win.rxview.save_with_anno is True
    win.rxview.copy_with_anno = False
    win._save_settings(silent=True)
    s2 = Settings(str(p))
    assert s2.d["copy_with_anno"] is False and s2.d["save_with_anno"] is True
    win2 = MainWindow(s2)
    assert win2.rxview.copy_with_anno is False and win2.rxview.save_with_anno is True


# ---- 网络 IPv6：纯单栈语义（绑哪个族只收发哪个族）+ 远程写法宽容 ----
def test_tcp_v6_loopback_and_v4_isolation(tmp_path):
    """服务端绑 ::1：IPv6 客户端互通；IPv4 客户端必须连不上（单栈）。"""
    from PySide2.QtNetwork import QTcpSocket, QHostAddress, QAbstractSocket
    from app.core.net_link import TcpServerLink
    win = MainWindow(Settings(str(tmp_path / "v6tcp.json")))
    link = TcpServerLink({"local_ip": "::1", "local_port": 0})
    got = bytearray()
    link.rx.connect(got.extend)
    assert link.open()
    port = link.srv.serverPort()
    c6 = QTcpSocket()
    c6.connectToHost(QHostAddress("::1"), port)
    for _ in range(30):
        app.processEvents()
        if link.clients:
            break
    assert link.clients, "IPv6 客户端应能连上 ::1 服务端"
    c6.write(b"ping6")
    for _ in range(30):
        app.processEvents()
        if got:
            break
    assert bytes(got) == b"ping6"
    c4 = QTcpSocket()
    c4.connectToHost(QHostAddress("127.0.0.1"), port)
    for _ in range(30):
        app.processEvents()
        if c4.state() == QAbstractSocket.UnconnectedState:
            break
    assert c4.state() != QAbstractSocket.ConnectedState, \
        "绑 ::1 后 IPv4 客户端不允许连上（单栈语义，探针回归）"
    link.close()
    c6.abort()
    c4.abort()


# ---- TCP 客户端"本机 IP"真实生效：连接前预绑定（十九轮 F1，历史遗留）----
def test_tcp_client_binds_local_ip(tmp_path, monkeypatch):
    """TcpClientLink 按所选本机地址的处理（十九轮 F1 引入预绑定、二十轮 F1 收口）：
    仅"具体网卡地址"才预绑定（限定源地址/出口网卡）；"全部"档（0.0.0.0/::）、
    回环与未配置一律不绑——源与协议族交回 Qt 由目标地址决定，主机名保留
    多地址回退（出厂默认 127.0.0.1 预绑定会钉死源，连局域网设备直接报
    Network unreachable）。绑定不可能的地址（本机未配置）→ 立即报"绑定失败"。"""
    import app.core.net_link as nl
    from PySide2.QtNetwork import QTcpSocket

    binds, dials = [], []

    class _SpySock(QTcpSocket):
        def bind(self, *a):
            binds.append(a[0].toString() if a else "?")
            return True

        def connectToHost(self, name, port, *a):
            dials.append((name, port))

    monkeypatch.setattr(nl, "QTcpSocket", _SpySock)
    for no_bind in ("0.0.0.0", "::", "127.0.0.1", "::1", ""):
        binds.clear()
        dials.clear()
        c = nl.TcpClientLink({"remote_ip": "1.2.3.4", "remote_port": "80",
                              "local_ip": no_bind})
        c.open()
        assert binds == [], "local_ip=%r 不应预绑定" % no_bind
        assert dials and dials[0][0] == "1.2.3.4", dials
        c.close()
    binds.clear()
    c = nl.TcpClientLink({"remote_ip": "1.2.3.4", "remote_port": "80",
                          "local_ip": "198.51.100.7"})
    c.open()
    assert binds == ["198.51.100.7"], "具体网卡地址应预绑定"
    c.close()
    # describe() 与绑定判据同口径：不绑档显示"自动（随目标）"，
    # 具体网卡地址照常显示（二十一轮 F4）
    assert "自动" in nl.TcpClientLink({"remote_ip": "1.2.3.4", "remote_port": "80",
                                       "local_ip": "0.0.0.0"}).describe()
    assert "自动" in nl.TcpClientLink({"remote_ip": "1.2.3.4", "remote_port": "80",
                                       "local_ip": "127.0.0.1"}).describe()
    assert "198.51.100.7" in nl.TcpClientLink({"remote_ip": "1.2.3.4",
                                               "remote_port": "80",
                                               "local_ip": "198.51.100.7"}).describe()
    monkeypatch.undo()               # 以下端到端用例换回真实 QTcpSocket

    from app.core.net_link import TcpClientLink, TcpServerLink
    srv = TcpServerLink({"local_ip": "127.0.0.1", "local_port": 0})
    assert srv.open()
    port = srv.srv.serverPort()
    got = bytearray()
    srv.rx.connect(got.extend)
    c = TcpClientLink({"remote_ip": "127.0.0.1", "remote_port": str(port),
                       "local_ip": "127.0.0.1"})
    assert c.open() is False                     # 异步连接（回环不预绑定，Qt 自行连通）
    for _ in range(100):
        app.processEvents()
        if c.is_open():
            break
    assert c.is_open(), "源地址 127.0.0.1 应能连上本机服务端"
    c.write(b"hi")
    for _ in range(50):
        app.processEvents()
        if got:
            break
    assert bytes(got) == b"hi", "应真实互通"
    c.close()
    srv.close()

    # 本机未配置的具体地址 → bind 立即失败并报错（此前被静默忽略、照样连上）
    c2 = TcpClientLink({"remote_ip": "127.0.0.1", "remote_port": str(port),
                        "local_ip": "198.51.100.7"})       # TEST-NET-2，不可绑定
    states = []
    c2.state_changed.connect(states.append)
    c2.open()
    for _ in range(50):
        app.processEvents()
        if any(s.startswith("error") for s in states):
            break
    assert not c2.is_open()
    assert any("绑定失败" in s for s in states), states

    # 单栈语义收口（二十轮 F1）：TCP 客户端的"全部"档不再预绑定——族由目标
    # 地址决定（0.0.0.0 也能连上 IPv6 服务端）；互斥语义只保留在 UDP/服务端
    # 的 listen 绑定上（各自用例覆盖）
    from PySide2.QtNetwork import QAbstractSocket
    srv6 = TcpServerLink({"local_ip": "::1", "local_port": 0})
    assert srv6.open()
    port6 = srv6.srv.serverPort()
    c4 = TcpClientLink({"remote_ip": "::1", "remote_port": str(port6),
                        "local_ip": "0.0.0.0"})
    c4.open()
    for _ in range(100):
        app.processEvents()
        if c4.is_open():
            break
    assert c4.is_open(), "全部档不绑定：族由目标地址决定，IPv4 全部也能连 IPv6 服务端"
    c4.close()
    srv6.close()


def test_udp_v6_loopback_and_v4_isolation(tmp_path):
    """UDP 绑 ::：IPv6 数据报双向可达；IPv4 数据报收不到。"""
    from PySide2.QtNetwork import QUdpSocket, QHostAddress
    from app.core.net_link import UdpLink
    win = MainWindow(Settings(str(tmp_path / "v6udp.json")))
    link = UdpLink({"local_ip": "::", "local_port": 0,
                    "remote_ip": "::1", "remote_port": 9})
    got = bytearray()
    link.rx.connect(got.extend)
    assert link.open()
    port = link.sock.localPort()
    peer = QUdpSocket()
    peer.bind(QHostAddress("::1"), 0)
    peer.writeDatagram(b"hi6", QHostAddress("::1"), port)
    for _ in range(30):
        app.processEvents()
        if got:
            break
    assert bytes(got) == b"hi6"
    peer.writeDatagram(b"hi4", QHostAddress("127.0.0.1"), port)
    for _ in range(10):
        app.processEvents()
    assert bytes(got) == b"hi6", "IPv4 数据报不允许进入仅 IPv6 绑定（单栈语义）"
    rx = QUdpSocket()
    rx.bind(QHostAddress("::1"), 0)
    link.cfg["remote_port"] = rx.localPort()
    assert link.write(b"out6") == 4
    for _ in range(30):
        app.processEvents()
        if rx.hasPendingDatagrams():
            break
    assert rx.hasPendingDatagrams() and bytes(rx.receiveDatagram().data()) == b"out6"
    link.close()
    peer.close()
    rx.close()


def test_udp_bad_remote_notice_once(tmp_path):
    """远程 IP 无法解析：报一次 error；修正后恢复 open（定时发送不刷屏）。"""
    from app.core.net_link import UdpLink
    win = MainWindow(Settings(str(tmp_path / "udpbad.json")))
    link = UdpLink({"local_ip": "127.0.0.1", "local_port": 0,
                    "remote_ip": "not-an-ip", "remote_port": 9})
    assert link.open()
    states = []
    link.state_changed.connect(states.append)
    assert link.write(b"x") == 0
    assert link.write(b"x") == 0
    errs = [s for s in states if s.startswith("error:")]
    assert len(errs) == 1 and "无法解析" in errs[0]
    link.cfg["remote_ip"] = "127.0.0.1"
    assert link.write(b"ok") == 2
    assert states[-1] == "open"
    link.close()


def test_strip_brackets_and_client_v6_connect(tmp_path):
    """[::1] 写法宽容 + TCP 客户端直连 ::1 服务端（连 v6 远程须绑 v6 本机
    地址——本地下拉选 ::，单栈语义与十九轮 F1 预绑定一致）。"""
    from PySide2.QtNetwork import QTcpServer, QHostAddress
    from app.core.net_link import TcpClientLink, _strip_brackets
    assert _strip_brackets("[::1]") == "::1"
    assert _strip_brackets(" 127.0.0.1 ") == "127.0.0.1"
    assert _strip_brackets("") == ""
    srv = QTcpServer()
    assert srv.listen(QHostAddress("::1"), 0)
    win = MainWindow(Settings(str(tmp_path / "v6cli.json")))
    link = TcpClientLink({"remote_ip": "[::1]", "remote_port": srv.serverPort(),
                          "local_ip": "::"})
    link.open()
    for _ in range(30):
        app.processEvents()
        if link.is_open():
            break
    assert link.is_open(), "剥括号后应能连上 ::1"
    link.close()
    srv.close()


def test_local_ip_combo_lists_v6(tmp_path):
    """本地 IP 下拉：0.0.0.0 首项（仅 IPv4 全部），:: 在列（仅 IPv6 全部），
    本机有 IPv6 地址时下拉里也应出现（全局在前链路本地在后）。"""
    win = MainWindow(Settings(str(tmp_path / "nics.json")))
    win._refresh_nics()
    win.cmb_nic.setCurrentIndex(0)             # 切回"全部网卡"（默认可能自动选中环回网卡）
    win._refresh_local_ips()
    items = [win.cmb_local_ip.itemText(i) for i in range(win.cmb_local_ip.count())]
    assert items[0] == "0.0.0.0"
    assert "::" in items
    from PySide2.QtNetwork import QNetworkInterface, QAbstractSocket
    has_v6 = any(e.ip().protocol() == QAbstractSocket.IPv6Protocol
                 for if_ in QNetworkInterface.allInterfaces()
                 for e in if_.addressEntries())
    if has_v6:
        v6_items = [t for t in items if ":" in t]
        assert v6_items and v6_items[0] == "::"
        assert v6_items[-1].startswith("fe80")     # 链路本地排最后


# ---- UDP 发送失败的状态呈现（十六轮 F2/F3）----
def test_udp_send_fail_state_stays_bound(tmp_path):
    """发送失败时链路仍绑定：界面保持 已连接/关闭（F2），不退化成 未连接/打开
    （否则按钮写「打开」点击却执行关闭）；已断开的失败（绑定失败）维持原语义。"""
    from app.core.net_link import UdpLink
    win = MainWindow(Settings(str(tmp_path / "f2.json")))
    win._set_mode("udp", quiet=True)
    link = UdpLink({"local_ip": "127.0.0.1", "local_port": 0,
                    "remote_ip": "127.0.0.1", "remote_port": 9})
    assert link.open()
    win.link = link
    win._on_state("error: 远程IP无法解析(x)")
    assert link.is_open()
    assert win.sb_state.text() == "已连接"
    assert win.btn_link.text() == "关闭"
    win._on_state("open")                          # 恢复 → 正常
    assert win.sb_state.text() == "已连接"
    # 已断开的失败（如绑定失败，link 未打开）→ 维持 未连接/打开（W1 语义不回归）
    link.close()
    win._on_state("error: 绑定失败(x)")
    assert win.sb_state.text() == "未连接"
    assert win.btn_link.text() == "打开"


def test_write_generic_notify_gated_by_link_error(tmp_path):
    """链路已上报具体原因（远程IP无法解析）时，_write 不再叠加通用提示
    覆盖第 4 格（F3）；通用提示对未上报原因的链路（如 TCP 积压）仍然生效。"""
    from app.core.net_link import UdpLink
    win = MainWindow(Settings(str(tmp_path / "f3.json")))
    win._set_mode("udp", quiet=True)
    link = UdpLink({"local_ip": "127.0.0.1", "local_port": 0,
                    "remote_ip": "not-an-ip", "remote_port": 9})
    assert link.open()
    win.link = link
    link.state_changed.connect(win._on_state)   # 正常打开路径由 _open_link 接线
    win._write(b"abc")
    assert "无法解析" in win.sb_link.text()
    assert "写入失败或对端未就绪" not in win.sb_link.text()
    # 修正地址：发送成功且无残留错误提示
    link.cfg["remote_ip"] = "127.0.0.1"
    win._write(b"abc")
    for _ in range(5):
        app.processEvents()
    assert "无法解析" not in win.sb_link.text()


# ---- 帮助 ▸ 检查更新：打开仓库 Releases 页（应用内不联网，十六轮后用户定稿）----
def test_help_menu_check_update_opens_release_page(tmp_path, monkeypatch):
    from app.ui import mainwindow as mw_mod
    win = MainWindow(Settings(str(tmp_path / "upd.json")))

    class _DS:
        url = None

        @staticmethod
        def openUrl(u):
            _DS.url = u
            return True

    monkeypatch.setattr(mw_mod, "QDesktopServices", _DS)
    win.act_check_update.trigger()
    assert _DS.url.toString() == "https://github.com/smartmx/PyCom/releases/latest"


# ---- 网络模式锁死无关输入框（2026-10-20 用户定稿）----
def test_net_mode_locks_irrelevant_inputs(tmp_path):
    """TCP 客户端锁本机端口、TCP 服务器锁远程 IP/端口（置灰+悬停原因），
    UDP 四项全有效；锁死只禁输入，值仍随配置持久化。"""
    win = MainWindow(Settings(str(tmp_path / "lock.json")))
    win._set_mode("tcp_client", quiet=True)
    assert not win.edit_local_port.isEnabled()
    assert "无需配置本机端口" in win.edit_local_port.toolTip()
    assert win.edit_remote_ip.isEnabled() and win.edit_remote_port.isEnabled()
    assert "IPv4/IPv6" in win.edit_remote_ip.toolTip()
    win._set_mode("tcp_server", quiet=True)
    assert not win.edit_remote_ip.isEnabled() and not win.edit_remote_port.isEnabled()
    assert "无需配置远程IP和端口" in win.edit_remote_ip.toolTip()
    assert "无需配置远程IP和端口" in win.edit_remote_port.toolTip()
    assert win.edit_local_port.isEnabled()
    win._set_mode("udp", quiet=True)
    assert (win.edit_remote_ip.isEnabled() and win.edit_remote_port.isEnabled()
            and win.edit_local_port.isEnabled())


# ---- 对话框标题栏跟随深色（Qt5 无 setColorScheme：应用级单例过滤器 + DWM）----
def test_dialog_titlebar_dark_filter(tmp_path, monkeypatch):
    """单例过滤器把顶层对话框的 Show 转发给"当前"主窗口的 _dark_titlebar_widget；
    非对话框对象 / 非 Show 事件不触发（DWM 调用本身在 offscreen 自动跳过）。
    全局只装一个过滤器、经弱引用转发新窗口——若每窗口自装，QApplication 的
    过滤器列表会强持每个窗口的 Python 包装，事件分派随窗口数 O(N²)
    （2026-10-20 实测把全量跑拖慢 10 倍，故收口为单例）。"""
    import weakref as _wr
    from PySide2.QtCore import QEvent
    from PySide2.QtGui import QShowEvent
    from PySide2.QtWidgets import QApplication, QMessageBox
    win = MainWindow(Settings(str(tmp_path / "dlgbar.json")))
    app = QApplication.instance()
    f = app._dlg_dark_filter
    assert isinstance(f._win, _wr.ref) and f._win() is win   # 弱引用当前窗口
    dlg = QMessageBox(QMessageBox.Information, "标题", "内容")   # 顶层对话框
    called = []
    monkeypatch.setattr(win, "_dark_titlebar_widget", lambda w: called.append(w))
    f.eventFilter(dlg, QShowEvent())
    assert called == [dlg]
    f.eventFilter(win.rxview, QShowEvent())       # 非对话框对象：不触发
    assert called == [dlg]
    f.eventFilter(dlg, QEvent(QEvent.Close))      # 非 Show 事件：不触发
    assert called == [dlg]
    win2 = MainWindow(Settings(str(tmp_path / "dlgbar2.json")))
    assert app._dlg_dark_filter is f              # 仍是同一个单例
    assert f._win() is win2                       # 新窗口接管转发


# ---- 行表跟随缓冲：界面路径（_on_rx 同步 + 运行中调档立即同步）----
def test_rx_keeps_line_table_within_buffer_span(tmp_path):
    """接收区显示的行与 *.bin 可导出的字节保持同一段：缓冲丢掉的旧数据，
    对应的行随之裁掉；运行中把上限调小时立即同步——不再出现"屏幕有、导出没有"。
    裁剪粒度是**包**（同一包内所有行 start_abs 相同），故允许"一包之内"的误差。"""
    win = MainWindow(Settings(str(tmp_path / "trim_hook.json")))
    win._set_buffer_cap(1_048_576)               # 小上限，便于触发丢弃
    unit = b"abcdefghij\r\n" * 1000              # 12KB/次
    for _ in range(120):                         # ≈1.4MB > 1M
        win._on_rx(unit)
        win.builder.flush()                      # 每次一包（真实使用由空闲/tick 结算）
    assert win.buf.dropped > 0
    assert win.builder.lines, "行表不该被裁空"
    # 最旧的一包必须与缓冲保留范围相交（误差 ≤ 一包 = 12KB）
    assert win.builder.lines[0].start_abs >= win.buf.start_abs - 2 * len(unit)
    n_before = len(win.builder.lines)
    win._set_buffer_cap(500_000)                 # 调小 → 缓冲丢更多，行表同步
    assert len(win.builder.lines) < n_before
    assert win.builder.lines[0].start_abs >= win.buf.start_abs - 2 * len(unit)


# ---- 自动滚屏：用户回看自动取消勾选；重新勾选立即跳底（2026-10-20 用户定稿）----
def test_rxview_scroll_pause_resume_near_bottom(tmp_path):
    """滚轮上翻/拖拽选择/拖动滚动条 = 回看 → 主窗口自动取消"自动滚屏"勾选
    （视图停住，持续接收下可安心选择复制）；重新勾选由 set_auto_scroll(True)
    立即跳回最底部，不需要等新数据到达。"""
    win = MainWindow(Settings(str(tmp_path / "scrl.json")))
    v = win.rxview
    v.resize(400, 240)
    for i in range(200):
        win._on_rx(b"LINE-%03d\r\n" % i)
        win.builder.flush()
    v._update_range()
    sb = v.verticalScrollBar()
    assert win.cb_auto_scroll.isChecked() and v._stick   # 默认勾选并跟随
    # 用户回看（暂停请求：滚轮上翻/拖选/拖滚动条三个触发点共用）→ 勾选自动取消
    v._pause_auto_scroll()
    assert not win.cb_auto_scroll.isChecked()            # 勾选自动取消
    assert not v._stick
    pos = sb.value()
    win._on_rx(b"MORE\r\n" * 60)                         # 持续接收
    win.builder.flush()
    v._update_range()
    for _ in range(5):
        app.processEvents()
    assert sb.value() == pos                             # 视图停住不再跟随
    # 再次勾选 → 立即跳到最底部（无新数据也生效）
    win.cb_auto_scroll.setChecked(True)
    assert v._stick
    assert sb.value() == sb.maximum()
    # 拖动滚动条：松手时不在底部 → 勾选自动取消；松手在底部 → 保持勾选
    # （与"勾选=跟随状态"一致，方向无关，十九轮 F2）
    sb.setValue(sb.maximum() - 10)
    v._on_slider_released()
    assert not win.cb_auto_scroll.isChecked()
    win.cb_auto_scroll.setChecked(True)
    sb.setValue(sb.maximum())
    v._on_slider_released()
    assert win.cb_auto_scroll.isChecked()


# ---- 定时发送最小间隔 10ms → 1ms（2026-10-20 用户要求）----
def test_timed_send_min_interval_1ms(tmp_path):
    win = MainWindow(Settings(str(tmp_path / "tmin.json")))
    assert win.sp_timed.minimum() == 1
    assert win.sp_timed.maximum() == 3_600_000


# ---- 状态栏速率：250ms 渲染 + 1s 滑窗，突发不再一拍归零（2026-10-20 用户反馈）----
def test_speed_rate_rolling_window(tmp_path):
    """旧实现两个毛病：①收/发路径直接写"RX: 总量"把速率后缀抹掉；②每秒清零
    累计器导致速率只在突发后 1 秒内可见。现在收发只累计数，速率/总量统一由
    _update_speed 渲染（取最近 1 秒滑窗，now 注入时钟驱动）。"""
    win = MainWindow(Settings(str(tmp_path / "speed.json")))
    win._update_speed(now=0.0)                    # 首拍只建样本，不出速率
    win.tx_bytes += 1000
    win.rx_bytes += 8000
    win._update_speed(now=1.0)                    # 1 秒窗口
    assert "TX: 1000 (1000 B/s)" == win.sb_tx.text()
    assert "RX: 8000 (8000 B/s)" == win.sb_rx.text()
    win._update_speed(now=1.25)                   # 无新数据：突发仍保持（滑窗未滑出）
    assert win.sb_tx.text() == "TX: 1000 (800 B/s)"
    assert win.sb_rx.text() == "RX: 8000 (6400 B/s)"
    win._update_speed(now=2.6)                    # 突发滑出窗口 → 速率归零，总量保留
    assert win.sb_tx.text() == "TX: 1000 (0 B/s)"
    assert win.sb_rx.text() == "RX: 8000 (0 B/s)"
    win._reset_counters()                         # 右键清零：基线一并清掉立即归零
    assert win.sb_tx.text() == "TX: 0 (0 B/s)"
    assert win._speed_samples == []


# ---- 转换计算器：只读"C语言数组"框（2026-10-20 用户要求）----
def test_calculator_c_array_field():
    from PySide2.QtWidgets import QPlainTextEdit

    from app.ui.calculator_dlg import CalculatorWidget
    w = CalculatorWidget()
    assert isinstance(w.c_array, QPlainTextEdit) and w.c_array.isReadOnly()
    w._recompute(w._from_hex, "十六进制 HEX", "12 34 56 78 9A bc")
    assert w.c_array.toPlainText() == "0xBC, 0x9A, 0x78, 0x56, 0x34, 0x12"
    w._recompute(w._from_dec, "十进制 DEC", "65")     # 任意字段联动同步
    assert w.c_array.toPlainText() == "0x41"


# ---- 启动期串口枚举只做一次（启动性能分析_20261021 §4.1）----
def test_startup_does_not_reenumerate_ports(tmp_path, monkeypatch):
    """端口列表在 _apply_settings 里已枚举；_startup 只做列宽校正——慢驱动
    机器上一次 availablePorts() 可达数秒，启动路径不重复做。"""
    win = MainWindow(Settings(str(tmp_path / "norefresh.json")))
    calls = []
    monkeypatch.setattr(win, "_refresh_ports",
                        lambda select=None: calls.append(select))
    win._startup()
    assert calls == []                       # 不再重复枚举
    assert win.serial_box.width() > 0        # 列宽校正仍执行
    assert win.net_box.width() == win.serial_box.width()


# ---- 工具行"新窗口"：单击启动新实例（V0.0.4，2026-10-22 移入工具行）----
def test_new_window_action_launches_instance(tmp_path, monkeypatch):
    import app.ui.mainwindow as mw_mod

    win = MainWindow(Settings(str(tmp_path / "newwin.json")))
    win.show()
    for _ in range(3):
        app.processEvents()
    assert not [a for a in win.menuBar().actions()
                if a.text() == "新窗口"], "新窗口已移出菜单栏"
    # 工具行：清除窗口 查找 新窗口（打开文件之前）——顺序由布局插入序保证
    assert win.btn_new_window.text() == "新窗口"
    calls = []

    def fake_detach(exe, args, cwd=None):
        calls.append((exe, args, cwd))
        return True

    monkeypatch.setattr(mw_mod.QProcess, "startDetached", fake_detach)
    win.btn_new_window.click()
    assert calls and calls[0][0], "应拉起新实例进程"
    assert not win.sb_link.text().startswith("新窗口启动失败")
    # 文件块起始横坐标 = 左下角"发送"按钮右缘（垫片定宽，_startup 实测计算；
    # 精确值依赖运行时布局，真机探针已验证；离屏布局冻结不断言应用宽度）
    assert win._file_pad > 0


# ---- 工具行与 HEX 行对齐 + 小工具菜单（文件资源管理器/扩展页入口）----
def test_tools_menu_explorer_and_ext_pages(tmp_path, monkeypatch):
    import app.ui.mainwindow as mw_mod

    win = MainWindow(Settings(str(tmp_path / "tools.json")))
    win.show()
    for _ in range(3):
        app.processEvents()
    # 小工具菜单两组：扩展页直达（进制转换/ASCII码表/多条字符串发送）
    # + 外部工具（计算器/设备管理器/文件资源管理器）
    from PySide2.QtWidgets import QMenu as _QMenu
    tool_menu = [m for m in win.findChildren(_QMenu) if m.title() == "小工具"][0]
    texts = [a.text() for a in tool_menu.actions() if a.text()]
    assert {"进制转换", "ASCII码表", "多条字符串发送",
            "计算器", "设备管理器", "文件资源管理器"} <= set(texts)
    # 菜单→处理器接线：触发"进制转换"后自动展开扩展面板并切到该页
    # （菜单动作取到后立即触发，避免 PySide2 菜单 GC 坑）
    win.btn_expand.setChecked(False)
    win._toggle_expand(False)
    for a in tool_menu.actions():
        if a.text() == "进制转换":
            a.trigger()
            break
    assert win.btn_expand.isChecked() and win.tabs.currentIndex() == 1
    # 其余入口直调处理器（句柄级验证，不依赖菜单对象存活）
    win._show_ext_tab(2)
    assert win.tabs.currentIndex() == 2
    called = []
    monkeypatch.setattr(mw_mod.os, "startfile",
                        lambda path: called.append(path))
    win._open_explorer()
    assert called and "20D04FE0" in called[0]      # 我的电脑 CLSID


# ---- 多条发送表格深色：原生样式直绘部位改主题手绘（2026-10-20 用户反馈白块）----
def test_multisend_table_dark_delegates(tmp_path):
    """深色下"点击发送"按钮与 HEX 勾选框不再走 Qt5 原生样式（windowsvista
    不吃深色调色板，按钮浅灰、未勾选指示器白块），改按主题 QSS 同源颜色手绘；
    表头角落按钮由 QSS 显式着色。offscreen 抓像素断言三处均为暗色。"""
    win = MainWindow(Settings(str(tmp_path / "msdark.json")))
    win.act_theme.setChecked(True)               # 深色：palette+QSS 即生效
    win.ms_model.rows = [{"note": "", "hex": True, "text": "ABC",
                          "order": 0, "interval": 1000}]
    win.ms_model.layoutChanged.emit()
    t = win.ms_table
    t.resize(420, 140)
    img = t.grab().toImage()
    hdr_h = t.horizontalHeader().height()
    row_y = t.rowViewportPosition(0) + t.rowHeight(0) // 2
    # ① 表头角落按钮（行表头×列表头交角）：QSS 显式着色，不再是白色
    assert img.pixelColor(3, 3).lightness() < 100
    # ② HEX 勾选框（勾选态）：手绘暗框，取样避开对勾笔画
    cx = t.columnViewportPosition(1) + t.columnWidth(1) // 2
    bx, by = cx - 7, row_y - 7                   # 14×14 指示器左上角
    assert img.pixelColor(bx + 10, by + 10).lightness() < 100
    # ③ 发送按钮中心：手绘 btn 底色，不是原生浅灰
    sx = t.columnViewportPosition(0) + t.columnWidth(0) // 2
    assert img.pixelColor(sx, row_y).lightness() < 100
