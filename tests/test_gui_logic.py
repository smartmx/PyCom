# -*- coding: utf-8 -*-
"""需要 Qt 环境的回归测试：txfile 契约、终端按键、多条发送循环、日志游标、TCP 广播。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.core.settings import DEFAULTS, Settings
from app.core.txfile import FileSender
from app.ui.mainwindow import MainWindow

app = QApplication.instance() or QApplication([])


# ---- P0-4: 终端按键映射（Qt6 键值） ----
def test_terminal_keymap_qt6():
    from app.core.terminal import key_to_bytes

    class E:
        def __init__(self, k, t=""):
            self._k, self._t = k, t

        def key(self):
            return self._k

        def text(self):
            return self._t

    assert key_to_bytes(E(int(Qt.Key.Key_Up)), "gbk") == b"\x1b[A"
    assert key_to_bytes(E(int(Qt.Key.Key_Down)), "gbk") == b"\x1b[B"
    assert key_to_bytes(E(int(Qt.Key.Key_Right)), "gbk") == b"\x1b[C"
    assert key_to_bytes(E(int(Qt.Key.Key_Left)), "gbk") == b"\x1b[D"
    assert key_to_bytes(E(int(Qt.Key.Key_Home)), "gbk") == b"\x1b[H"
    assert key_to_bytes(E(int(Qt.Key.Key_End)), "gbk") == b"\x1b[F"
    assert key_to_bytes(E(int(Qt.Key.Key_PageUp)), "gbk") == b"\x1b[5~"
    assert key_to_bytes(E(int(Qt.Key.Key_PageDown)), "gbk") == b"\x1b[6~"
    assert key_to_bytes(E(int(Qt.Key.Key_Delete)), "gbk") == b"\x1b[3~"
    assert key_to_bytes(E(int(Qt.Key.Key_Backspace)), "gbk") == b"\x08"
    assert key_to_bytes(E(int(Qt.Key.Key_A), "a"), "gbk") == b"a"


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
    from PySide6.QtNetwork import QAbstractSocket
    from app.core.net_link import TcpServerLink

    class Slow:
        def __init__(self):
            self.received = bytearray()
            self.free = 30

        def state(self):
            return QAbstractSocket.SocketState.ConnectedState

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
    QTest.qWait(40)
    loop.stop()
    assert sent[0] == "c"                # 顺序号 1 最先
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
    from PySide6.QtNetwork import QAbstractSocket
    from app.core.net_link import TcpServerLink

    class Slow:
        def __init__(self):
            self.received = bytearray()

        def state(self):
            return QAbstractSocket.SocketState.ConnectedState

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
    from PySide6.QtNetwork import QAbstractSocket
    from app.core.net_link import TcpServerLink

    class Slow:
        def __init__(self):
            self.received = bytearray()
            self.free = 10

        def state(self):
            return QAbstractSocket.SocketState.ConnectedState

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
    assert s.d["buffer_cap"] == 1 << 29     # 超大缓冲钳到上界（512MB）
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
    from PySide6.QtWidgets import QMessageBox
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
    assert "超限" in win.sb_state.text()
    win._tx_pend = [(b"y" * 100, True)]
    win._write(b"z" * win._TX_PEND_CAP)     # 合并后超限 → 拒绝
    assert win._tx_pend == [(b"y" * 100, True)]   # 旧余量原样保留


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
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt as _Qt
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
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt as _Qt
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
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtGui import QInputMethodEvent
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
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt as _Qt
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
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt as _Qt
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
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt as _Qt
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
    fw = app.focusWidget()
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
    from PySide6.QtWidgets import QMessageBox
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
    from PySide6.QtGui import QGuiApplication, QPalette
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
    assert QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    win.act_theme.setChecked(False)                            # 切浅色 → 恢复标准色板
    for _ in range(3):
        app.processEvents()
    assert app.palette().color(QPalette.Window).name() != "#252526"
    assert QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Light
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
    from PySide6.QtWidgets import QFrame
    assert win._chk_sep.frameShape() == QFrame.Shape.VLine
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


# ---- 显示缓冲上限：删 1G、加 256M、最大 512M（配置超限自动收敛）----
def test_buffer_sizes_capped_512m(tmp_path):
    from app.ui.mainwindow import BUF_SIZES
    names = [n for n, _ in BUF_SIZES]
    assert "256M" in names and "1G" not in names
    assert max(s for _, s in BUF_SIZES) == 536_870_912
    p = tmp_path / "big.json"
    p.write_text('{"buffer_cap": 1073741824}', encoding="utf-8")
    s = Settings(str(p))
    assert s.d["buffer_cap"] == 536_870_912      # 老配置的 1G 收敛到 512MB


# ---- 窄窗模式：向左缩到配置框宽度，右侧发送/选项区隐藏（对齐 SSCOM）----
def test_narrow_mode_hides_right_panel(tmp_path):
    win = MainWindow(Settings(str(tmp_path / "narrow.json")))
    win.show()
    for _ in range(3):
        app.processEvents()
    win.resize(900, 620)
    for _ in range(3):
        app.processEvents()
    assert win.send_edit.isVisible() and win.sb_link.isVisible()
    win.resize(340, 620)
    for _ in range(5):
        app.processEvents()
    assert win.width() == 340                    # 能缩到配置框宽度附近
    assert not win.send_edit.isVisible()         # 发送输入框隐藏
    assert not win.sb_link.isVisible()           # 状态栏连接信息让位
    assert win.cb_break.isVisible()              # 配置列（线路行）保留
    assert win.cmb_baud.isVisible() and win.btn_link.isVisible()
    win.resize(900, 620)                         # 展宽恢复
    for _ in range(5):
        app.processEvents()
    assert win.send_edit.isVisible() and win.sb_link.isVisible()


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
    win.resize(900, 620)
    for _ in range(3):
        app.processEvents()
    win.find_bar.activate()
    assert win.find_bar.isVisible()
    win.resize(340, 620)                     # 缩窄 → 查找条让位
    for _ in range(5):
        app.processEvents()
    assert not win.find_bar.isVisible()
    win.resize(900, 620)                     # 展宽 → 恢复原打开状态
    for _ in range(5):
        app.processEvents()
    assert win.find_bar.isVisible()
    win.resize(340, 620)                     # 窄窗下再点查找：不开条
    for _ in range(5):
        app.processEvents()
    win._activate_find()
    assert not win.find_bar.isVisible()
