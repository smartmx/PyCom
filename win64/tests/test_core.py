# -*- coding: utf-8 -*-
"""纯逻辑模块单测：checksum / rxbuf / display / hexutil / settings 导入。"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.checksum import (modbus_crc16, ccitt_crc16, crc8,
                               checksum_appendix, append_checksum, slice_range)
from app.core.rxbuf import RxBuffer
from app.core.hexutil import parse_hex, to_hex
from app.core.display import LineBuilder
from app.core.settings import Settings


# ---- checksum ----
def test_modbus_crc16_vector():
    # 经典校验向量：CRC16/MODBUS("123456789") = 0x4B37
    assert modbus_crc16(b"123456789") == 0x4B37


def test_append_crc_low_first():
    out = append_checksum(b"123456789", "crc", 1, 9)    # 旧版档：追加在末尾
    assert out[-2:] == b"\x37\x4B"      # 低字节在前


def test_add_xor():
    assert append_checksum(b"\x01\x02\x03", "add", 1, 9)[-1] == 6
    assert append_checksum(b"\xFF\x0F\xF0", "xor", 1, 9)[-1] == 0x00


def test_new_checksum_algorithms():
    """对齐原版 SSCOM 新增的校验种类与附加字节序。"""
    v = b"123456789"
    assert ccitt_crc16(v) == 0x31C3            # CRC-16/CCITT(init 0, 即 XMODEM 型) 经典校验值
    assert crc8(v) == 0xF4                     # CRC-8(poly 0x07) 经典校验值
    assert checksum_appendix("none", v) == b""
    assert checksum_appendix("modbus_crc16", v) == b"\x37\x4B"     # 低字节在前
    assert checksum_appendix("ccitt_crc16", v) == b"\xC3\x31"
    assert checksum_appendix("crc8", v) == b"\xF4"
    assert checksum_appendix("xor8", v) == b"\x31"
    assert checksum_appendix("add8", v) == b"\xDD"
    assert checksum_appendix("add16", v) == b"\xDD\x01"
    # 旧配置 kind 名兼容（旧版档 tail=9）
    assert append_checksum(v, "crc", 1, 9)[-2:] == b"\x37\x4B"
    assert append_checksum(v, "add", 1, 9)[-1] == 0xDD


def test_checksum_sscom_tail_insert():
    """SSCOM 语义：末尾 N 字节是帧尾，校验对帧尾前计算并插在帧尾前面。"""
    payload = b"\x01\x02\x03\x0D\x0A"
    # 帧尾 2 字节：帧 = 数据前段 + CRC + 帧尾
    out = append_checksum(payload, "modbus_crc16", 1, 2)
    body = payload[:3]
    assert out == body + checksum_appendix("modbus_crc16", body) + payload[3:]
    # start=2：从第 2 字节算到帧尾前
    out2 = append_checksum(payload, "add8", 2, 1)
    body2 = payload[1:4]
    assert out2 == payload[:4] + checksum_appendix("add8", body2) + payload[4:]
    # tail=0：末尾[无校验字节]，原样发送
    assert append_checksum(payload, "modbus_crc16", 1, 0) == payload
    # 数据不比帧尾长：无处插入，原样发送
    assert append_checksum(b"\x0D", "modbus_crc16", 1, 2) == b"\x0D"
    # tail=9：旧版追加在最末尾
    out9 = append_checksum(payload, "modbus_crc16", 1, 9)
    assert out9 == payload + checksum_appendix("modbus_crc16", payload)


def test_checksum_kind_migration(tmp_path):
    """旧 pycom.json 的 crc/add/xor 应迁移为新命名。"""
    p = tmp_path / "ck.json"
    p.write_text('{"checksum_kind": "crc"}', encoding="utf-8")
    s = Settings(str(p))
    assert s.d["checksum_kind"] == "modbus_crc16"


def test_checksum_tail_v2_migration(tmp_path):
    """旧档位语义（倒数M字节）一次性迁移；新语义配置不再被改动。"""
    p = tmp_path / "ck2.json"
    # 旧配置：有算法 + tail=0（原为整包追加在末尾）→ 旧版档 9，行为不变
    p.write_text('{"checksum_kind": "modbus_crc16", "checksum_tail": 0}',
                 encoding="utf-8")
    assert Settings(str(p)).d["checksum_tail"] == 9
    # 旧配置：tail=3（原为倒数3字节）→ 帧尾档 3
    p.write_text('{"checksum_kind": "modbus_crc16", "checksum_tail": 3}',
                 encoding="utf-8")
    assert Settings(str(p)).d["checksum_tail"] == 3
    # 新语义（带 v2 标记）：档位 0 已取消——回退默认档位 1，且原"关闭"语义
    # 由算法置 none 保留
    p.write_text('{"checksum_kind": "modbus_crc16", "checksum_tail": 0,'
                 ' "checksum_tail_v2": true}', encoding="utf-8")
    s = Settings(str(p))
    assert s.d["checksum_tail"] == 1
    assert s.d["checksum_kind"] == "none"
    # 超界档位钳制
    p.write_text('{"checksum_kind": "none", "checksum_tail": 777,'
                 ' "checksum_tail_v2": true}', encoding="utf-8")
    assert Settings(str(p)).d["checksum_tail"] == 4


def test_ini_checksum_place(tmp_path):
    """INI N1075 是 SSCOM 的放置档位：1~4=帧尾档；0=无校验字节（主开关）。"""
    p = tmp_path / "sscom51.ini"
    p.write_text("N1060=1\nN1074=1\nN1075=2\n", encoding="gbk")
    s = Settings(str(tmp_path / "a.json"))
    s.import_original_ini(str(p))
    assert s.d["checksum_kind"] == "modbus_crc16"
    assert s.d["checksum_tail"] == 2
    p.write_text("N1060=1\nN1074=1\nN1075=0\n", encoding="gbk")
    s2 = Settings(str(tmp_path / "b.json"))
    s2.import_original_ini(str(p))
    assert s2.d["checksum_kind"] == "none"      # 原档位 0 = 无校验字节
    assert s2.d["checksum_tail"] == 1           # 档位 0 已取消，回退默认


# ---- P0-1: 持续流实时落行（无空闲也显示，pending 有上限） ----
def test_continuous_stream_realtime_lines():
    lb = LineBuilder(split=True, split_ms=20)
    t = 0.0
    for _ in range(100):
        lb.feed("rx", b"abc\n", t, 0)
        t += 0.01
        lb.maybe_flush(t + 0.005)     # UI tick：无空闲也必须落行
    assert lb.line_count() - lb.base >= 99, "持续流应实时显示"
    assert len(lb._pend_buf) < 1024, "pending 应被持续消费"
    lb.maybe_flush(t + 1.0)
    assert lb.lines[0].prefix.startswith("[")


# ---- P0-2: 显示行表上限裁剪 + base 绝对行号 ----
def test_display_line_trim_and_base():
    lb = LineBuilder(split=True, split_ms=20, max_lines=1000)
    for i in range(1300):
        lb.feed("rx", ("line%d\n" % i).encode(), i * 0.001, 0)
        lb.maybe_flush(i * 0.001 + 0.0005)
    total = lb.line_count()
    assert lb.base > 0 and total == lb.base + len(lb.lines)
    assert lb.line_at(total - 1).text == "line1299"   # 最新行可按绝对行号访问


# ---- P1-7: 切换编码时半截多字节序列落字（不丢字节） ----
def test_encoding_switch_flushes_half_sequence():
    lb = LineBuilder(encoding="gbk", split=True, split_ms=20)
    lb.feed("rx", "中".encode("gbk")[:1], 0.0, 0)
    lb.set_encoding("utf-8")
    lb.feed("rx", b"x", 0.1, 1)
    lb.flush()
    t = lb.visible()[0].text
    assert "x" in t and "\ufffd" in t    # 半截序列 → 替换字符（旧编码），x 正常


# ---- CR-only 行尾兼容 ----
def test_cr_only_lines():
    lb = LineBuilder(split=True, split_ms=20)
    lb.feed("rx", b"A\rB\rC", 0.0, 0)
    lb.flush()
    assert [l.text for l in lb.visible()] == ["A", "B", "C"]


# ---- 复审 P3: 单独 CR 结束的包，空行也要带时间戳前缀（方向标识依赖它） ----
def test_cr_only_packet_gets_prefix():
    lb = LineBuilder(split=True, split_ms=20)
    lb.feed("rx", b"\r", 1.0, 0)          # 整包只有一个 CR（空行/心跳）
    lb.flush()
    vis = lb.visible()
    assert len(vis) == 1 and vis[0].text == ""
    assert vis[0].prefix.startswith("[")  # 包首空行同享本包时间戳
    # 未勾选分包显示时前缀仍随行记录（复制/保存选项可独立带出，显示不带出）
    lb2 = LineBuilder(split=False)
    lb2.feed("rx", b"\r", 1.0, 0)
    lb2.flush()
    assert lb2.visible()[0].prefix.startswith("[")
    # 包内已有文本时，尾部挂起 CR 不应再消费第二个前缀
    lb3 = LineBuilder(split=True, split_ms=20)
    lb3.feed("rx", b"abc\r", 1.0, 0)
    lb3.flush()
    vis3 = lb3.visible()
    assert sum(1 for l in vis3 if l.prefix) == 1, [l.prefix for l in vis3]


# ---- 复制/保存含时间戳：分包显示未勾选时前缀同样随行记录（选项与显示独立）----
def test_prefix_recorded_when_split_off():
    lb = LineBuilder(split=False, split_ms=20)
    lb.feed("rx", b"one\r\n", 1.0, 0)
    lb.flush()                            # 包 1
    lb.feed("rx", b"two\r\n", 2.0, 5)
    lb.flush()                            # 包 2：独立时间戳
    vis = lb.visible()
    assert [l.text for l in vis] == ["one", "two"]
    assert all(l.prefix.startswith("[") for l in vis), [l.prefix for l in vis]
    # 未收尾行（持续流增量落行）同样带前缀：复制/保存经由 visible() 取行
    lb.feed("rx", b"tail", 3.0, 10)
    lb.maybe_flush(3.0)
    assert lb.visible()[-1].prefix.startswith("[")


# ---- P1-5: 配置加载/保存防护 ----
def test_settings_bad_json_falls_back(tmp_path):
    from app.core.settings import DEFAULTS
    p = tmp_path / "bad.json"
    p.write_text("[]", encoding="utf-8")
    s = Settings(str(p))
    assert s.d["port"] == DEFAULTS["port"]      # 回退默认值不崩溃


def test_settings_save_failure_keeps_old(tmp_path):
    d = tmp_path / "cfgdir.json"                # 同名目录 → replace 必败
    d.mkdir()
    s = Settings(str(d))
    s.d["baud"] = 9600
    assert s.save() is False


# ---- P1-6: INI BGR 颜色 / N1076 / 窗口尺寸 ----
def test_ini_bgr_n1076_winsize(tmp_path):
    ini = tmp_path / "sscom51.ini"
    ini.write_text(
        "N1066=,255\r\n"          # BGR 蓝色 → 应转 #FF0000
        "N1076=,N\r\n"            # 终端 0D 补 0A 关闭
        "N1082=,1024\r\nN1083=,768\r\n",
        encoding="gbk")
    s = Settings(str(tmp_path / "cfg.json"))
    s.import_original_ini(str(ini))
    assert s.d["bg_color"] == "#FF0000"
    assert s.d["term_0d_add_0a"] is False
    assert s.d["win_w"] == 1024 and s.d["win_h"] == 768


# ---- SSCOM 语义: 终端仿真两种发送方式互斥（配置同真时按键优先）----
def test_settings_term_mode_mutex(tmp_path):
    p = tmp_path / "term.json"
    p.write_text('{"term_enter_send": true, "term_key_send": true, '
                 '"term_key_echo": true}', encoding="utf-8")
    s = Settings(str(p))
    assert s.d["term_enter_send"] is False
    assert s.d["term_key_send"] is True
    assert s.d["term_key_echo"] is True      # 回显偏好保留，仅 UI 按方式置灰
    # 仅回车方式打开时不受影响
    p2 = tmp_path / "term2.json"
    p2.write_text('{"term_enter_send": true, "term_key_send": false}',
                  encoding="utf-8")
    s2 = Settings(str(p2))
    assert s2.d["term_enter_send"] is True
    assert s2.d["term_key_send"] is False
    # 新配置默认对齐 SSCOM：回车方式默认开启
    s3 = Settings(str(tmp_path / "fresh.json"))
    assert s3.d["term_enter_send"] is True
    assert s3.d["term_key_send"] is False


# ---- 复审 P3: 嵌套配置归一化（字符串布尔 / None 字符串）----
def test_nested_config_normalization(tmp_path):
    p = tmp_path / "nested.json"
    p.write_text('{"send_font": {"size": 10, "bold": "false"},'
                 ' "multisend_rows": [{"hex": "false", "order": 1,'
                 ' "note": null, "text": null}]}', encoding="utf-8")
    s = Settings(str(p))
    assert s.d["send_font"]["bold"] is False      # "false" 不能变 True
    row = s.d["multisend_rows"][0]
    assert row["hex"] is False
    assert row["note"] == "" and row["text"] == ""   # None 不能变 "None"
    # 字符串真值仍按真处理
    p2 = tmp_path / "nested2.json"
    p2.write_text('{"send_font": {"bold": "yes", "underline": "0"},'
                  ' "multisend_rows": [{"hex": "1"}]}', encoding="utf-8")
    s2 = Settings(str(p2))
    assert s2.d["send_font"]["bold"] is True
    assert s2.d["send_font"]["underline"] is False
    assert s2.d["multisend_rows"][0]["hex"] is True


# ---- 第三轮 P0-1：持续流消费完 pending 后，空闲 tick 仍要结束包状态 ----
def test_packet_state_ends_on_idle_after_stream():
    lb = LineBuilder(split=True, split_ms=20)
    lb.feed("rx", b"one\n", 0.0, 0)
    lb.maybe_flush(0.0)          # 非空闲 tick：pending 被消费（包上下文保留）
    lb.maybe_flush(0.1)          # 空闲 tick：必须结算包状态
    lb.feed("rx", b"two\n", 0.2, 4)
    lb.maybe_flush(0.2)
    vis = lb.visible()
    assert [l.text for l in vis] == ["one", "two"]
    assert vis[0].prefix.startswith("[") and vis[1].prefix.startswith("["), \
        "第二包首行必须带自己的时间戳前缀"


# ---- 第三轮 P0-2：tail 写出后再完成的行，日志不拼接、不多空行（builder 侧语义） ----
def test_open_line_tail_then_complete_no_duplicate_newline():
    lb = LineBuilder(split=True, split_ms=20)
    lb.feed("rx", b"abc", 0.0, 0)
    lb.flush()
    tail = lb.open_line_delta()
    assert tail and tail.endswith("abc")
    lb.commit_open(nl_written=True)          # tail 已写文本 + 行终止符
    lb.feed("rx", b"\ndef\n", 0.1, 3)        # abc 完成，def 新行
    lb.flush()
    l0 = lb.line_at(lb.base)
    assert l0.text == "abc" and l0.nl_done is True, "已有 tail 的行不应再补换行"
    assert lb.open_line_delta() is None      # def 行已完整收尾，无未写尾巴


# ---- 第三轮 P1-4：单个超大段必须被硬切分成受上限约束的行 ----
def test_oversize_single_chunk_hard_split():
    lb = LineBuilder(split=True, split_ms=20)
    lb.feed("rx", b"x" * 1_000_000, 0.0, 0)  # 无换行、单段远大于上限
    lb.maybe_flush(0.5)
    total = sum(len(l.text) for l in lb.lines)
    if lb._open is not None:
        total += len(lb._open.text)
    assert total == 1_000_000, total
    oversized = [l for l in lb.lines if len(l.text) > lb._open_max_chars]
    assert not oversized, "不应存在超过 _open_max_chars 的行"


# ---- 第六轮 P2-1：已有 open 行 + 超大下一段，任何行都不超上限 ----
def test_open_line_cap_with_existing_open_and_oversize_seg():
    lb = LineBuilder(split=True, split_ms=20)
    lb.feed("rx", b"x" * 99_999, 0.0, 0)
    lb.maybe_flush(0.005)                      # 持续流：99 999 字符留在 open
    lb.feed("rx", b"y" * 150_000, 0.1, 99_999)
    lb.maybe_flush(0.2)
    all_lines = lb.lines + ([lb._open] if lb._open is not None else [])
    oversized = [l for l in all_lines if len(l.text) > lb._open_max_chars]
    assert not oversized, "任何行都不得超上限"
    total = sum(len(l.text) for l in lb.lines)
    if lb._open is not None:
        total += len(lb._open.text)
    assert total == 99_999 + 150_000, total


def test_slice_range():
    assert slice_range(10, 1, 0) == (0, 10)
    assert slice_range(10, 3, 0) == (2, 10)
    assert slice_range(10, 0, 2) == (0, 8)


# ---- rxbuf ----
def test_rxbuf_overflow_drops_oldest():
    b = RxBuffer(cap=1024)
    b.append(b"x" * 1021)
    b.append(b"ABC")
    assert len(b) == 1024
    assert b.dropped == 0            # 未溢出
    b.append(b"D")
    assert len(b) == 1024
    assert b.dropped == 1
    assert b.read(b.end_abs - 4, 4) == b"ABCD"
    assert b.total_rx == 1025


def test_rxbuf_clear_and_cap():
    b = RxBuffer()
    b.set_cap(2048)
    b.append(b"x" * 5000)
    assert len(b) == 2048
    b.clear()
    assert len(b) == 0 and b.start_abs == 0


# ---- hexutil ----
def test_parse_hex_loose():
    assert parse_hex("13 00 FF") == b"\x13\x00\xFF"
    assert parse_hex("0x1A,2b;0c") == b"\x1A\x2B\x0C"
    assert to_hex(b"\x01\x02") == "01 02"


# ---- display ----
def _feed_ascii_stream():
    lb = LineBuilder(encoding="gbk", hex_mode=False, split=True, split_ms=20)
    lb.feed("rx", b"hello\r\nwor", 1.0, 0)
    lb.maybe_flush(1.5)          # 超时切包
    lb.feed("rx", b"ld\n", 2.0, 0)
    lb.flush()
    return lb


def test_ascii_lines_and_carry():
    lb = _feed_ascii_stream()
    texts = [l.text for l in lb.visible()]
    assert "hello" in texts
    assert "world" in texts


def test_split_prefix_and_direction_change():
    lb = LineBuilder(encoding="gbk", hex_mode=False, split=True, split_ms=20)
    lb.feed("rx", b"AA", 1.0, 0)
    lb.feed("tx", b"BB", 1.5, 0)   # 方向变化立即切包
    lb.flush()
    vis = lb.visible()
    rx = [l for l in vis if l.dir == "rx"]
    tx = [l for l in vis if l.dir == "tx"]
    assert rx and rx[0].prefix.startswith("[")
    assert tx       # tx 行可见（含未收尾活动行）


def test_consecutive_packets_each_get_prefix():
    """回归：包尾换行的空游标曾吞掉下一包首行，致分包/时间戳整体失效。"""
    lb = LineBuilder(encoding="gbk", hex_mode=False, split=True, split_ms=20)
    lb.feed("rx", b"one\n", 1.0, 0)
    assert lb.maybe_flush(1.1)                 # 空闲超时切包
    lb.feed("rx", b"two\n", 2.0, 4)
    assert lb.maybe_flush(2.1)
    lb.feed("rx", b"\nthree", 3.0, 8)          # 包首空行 + 未收尾尾行
    assert lb.maybe_flush(3.1)
    vis = lb.visible()
    assert [l.text for l in vis] == ["one", "two", "", "three"]
    assert [l.prefix.startswith("[") for l in vis] == [True, True, True, False]


def test_nul_bytes_stripped_from_lines():
    """回归：0x00 字节解成 U+0000 后，PySide2 drawText 抛 embedded null
    character，接收视图每帧绘制中断（界面假死、滚轮才恢复）。行表不得含 NUL。"""
    lb = LineBuilder(encoding="gbk", hex_mode=False, split=True, split_ms=20)
    lb.feed("rx", b"a\x00bc\x00\r\nNO CARD\r\n", 1.0, 0)
    lb.flush()
    texts = [l.text for l in lb.visible()]
    assert texts == ["abc", "NO CARD"]
    assert not any("\x00" in t for t in texts)


def test_hard_line_cap_respects_log_cursor():
    """回归（审查 P3-5）：行数硬上限裁剪也不得裁掉未写盘行——
    设了转存游标后，裁剪上限=游标处；顶住时宁可暂时超行数上限。
    （构造器 max_lines 有 1000 下限，故用 1600/1200 行构造场景。）"""
    lb = LineBuilder(split=False, max_lines=1600)
    lb.set_log_keep(600)                   # 绝对行号 600 之前的行才允许裁
    for i in range(2400):
        lb.feed("rx", ("L%04d\n" % i).encode("ascii"), 1.0 + i * 0.001, i * 5)
    lb.flush()
    assert lb.base == 600                  # 裁到游标处为止（顶住后宁可超上限）
    assert lb.line_at(600).text == "L0600"  # 游标处的行仍在
    assert len(lb.lines) == 1800           # 2400 - 已裁 600


def test_stale_log_cursor_does_not_disable_trim():
    """回归（审查 N1）：转存游标必须随日志关闭复位（mainwindow 职责），
    否则硬上限被锁死；基址已越过的残留游标视为无游标。"""
    lb = LineBuilder(split=False, max_lines=1600)
    lb.set_log_keep(600)
    for i in range(1000):
        lb.feed("rx", ("L%04d\n" % i).encode("ascii"), 1.0 + i * 0.001, i * 5)
    lb.flush()
    lb.set_log_keep(None)                  # 模拟 _toggle_rxfile(False) 复位
    for i in range(1000, 8000):            # 此后只发不收
        lb.feed("tx", ("T%04d\n" % i).encode("ascii"), 1.0 + i * 0.001, i * 5)
    lb.flush()
    assert len(lb.lines) <= 1600           # 硬上限正常生效
    # 残留路径：游标早已被基址越过（如调用方传入旧值）→ 兜底视为无游标
    lb2 = LineBuilder(split=False, max_lines=1600)
    for i in range(2000):                  # 正常裁剪，base 推进到 400 一带
        lb2.feed("rx", ("M%04d\n" % i).encode("ascii"), 2.0 + i * 0.001, i * 5)
    lb2.flush()
    lb2.set_log_keep(50)                   # 已被越过的旧游标
    for i in range(2000, 4000):
        lb2.feed("rx", ("M%04d\n" % i).encode("ascii"), 2.0 + i * 0.001, i * 5)
    lb2.flush()
    assert len(lb2.lines) <= 1600


def test_hex_rows_16_per_line():
    lb = LineBuilder(encoding="gbk", hex_mode=True, split=True, split_ms=20)
    lb.feed("rx", bytes(range(20)), 1.0, 0)
    lb.flush()
    assert len(lb.lines) == 2
    assert lb.lines[0].text.count(" ") == 15
    assert lb.lines[1].text == "10 11 12 13"


def test_utf8_multibyte_carry_across_packets():
    lb = LineBuilder(encoding="utf-8", hex_mode=False, split=True, split_ms=20)
    half = "串".encode("utf-8")[:1]
    rest = "串".encode("utf-8")[1:]
    lb.feed("rx", half, 1.0, 0)
    lb.maybe_flush(1.5)          # 包切了，但解码器应缓存不完整序列
    lb.feed("rx", rest + "口".encode("utf-8"), 2.0, 0)
    lb.flush()
    vis = lb.visible()
    assert vis and vis[-1].text.endswith("串口")


# ---- settings: 原版 ini 导入 ----
SAMPLE_INI = """;注释
N101=1,十六进制数据串1,1000
N1=H,13 00 FF 88
N102=3,欢迎语,2000
N2=A,欢迎您使用SSCOM!
N1053=,abcdefg
N1055=,A
N1056=,1500
N1059=,Y
N1060=,1
N1067=,134217728
N1068=,192.168.1.10
N1069=,9000
N1070=,8888
N1080=,1
N1081=,9600
N1082=,792
"""


def test_import_original_ini(tmp_path):
    p = tmp_path / "sscom51.ini"
    p.write_text(SAMPLE_INI, encoding="gbk")
    s = Settings(str(tmp_path / "ng.json"))
    notes = s.import_original_ini(str(p))
    d = s.d
    assert d["multisend_rows"][0]["hex"] is True
    assert d["multisend_rows"][0]["text"] == "13 00 FF 88"
    assert d["multisend_rows"][0]["note"] == "十六进制数据串1"
    assert d["multisend_rows"][0]["interval"] == 1000
    assert d["multisend_rows"][1]["text"] == "欢迎您使用SSCOM!"
    assert d["main_text"] == "abcdefg" and d["send_hex"] is False
    assert d["send_interval_ms"] == 1500
    assert d["hex_mode"] is True
    assert d["checksum_kind"] == "modbus_crc16"
    assert d["buffer_cap"] == 100 * 1024 * 1024   # ini 里的 128MB 被上限（100M）钳住
    assert d["remote_ip"] == "192.168.1.10" and d["remote_port"] == 9000
    assert d["local_port"] == 8888
    assert d["mode"] == "tcp_client"
    assert d["baud"] == 9600
    assert notes


# ---- 行表跟随字节缓冲裁剪（避免"屏幕有、保存 .bin 导出没有"）----
def test_line_trim_follows_byte_buffer():
    from app.core.display import LineBuilder
    from app.core.rxbuf import RxBuffer

    cap = 1000
    unit = b"abcdefghij\r\n"                  # 12 字节/行
    buf = RxBuffer(cap)
    b = LineBuilder(split=False)
    for _ in range(500):                      # 6KB ≫ cap，缓冲会一直丢最旧
        buf.append(unit)
        b.feed("rx", unit, 1.0, buf.end_abs - len(unit))
        b.flush()                             # 每行一包（真实使用由空闲/tick 结算）
    b.trim_before_abs(buf.start_abs)          # 模拟 _on_rx 里的同步
    assert buf.dropped > 0
    # 最旧的一包必须与缓冲保留范围相交（否则就是"屏幕有、导出没有"）
    assert b.lines[0].start_abs + len(unit) > buf.start_abs
    # 行表覆盖的字节跨度不超过缓冲容量（误差 ≤ 一包）
    span = b.lines[-1].start_abs + len(unit) - b.lines[0].start_abs
    assert span <= buf.cap + len(unit)


def test_line_trim_keeps_unlogged_rows():
    """打开接收转存日志时，尚未写盘的行不许裁（否则日志静默漏行）。"""
    from app.core.display import LineBuilder
    from app.core.rxbuf import RxBuffer

    unit = b"abcdefghij\r\n"
    buf = RxBuffer(1024)                      # 注：RxBuffer 的下限就是 1024
    b = LineBuilder(split=False)
    for _ in range(200):                      # 2.4KB > 下限，必然触发丢弃
        buf.append(unit)
        b.feed("rx", unit, 1.0, buf.end_abs - len(unit))
        b.flush()
    assert buf.dropped > 0
    # 日志游标停在最前面（一行都还没写出去）→ 一行都不许裁
    assert b.trim_before_abs(buf.start_abs, keep_after_line=b.base) == 0
    # 游标推进到接近末尾（只剩最后一行没写）→ 可以裁到游标之前
    cut = b.trim_before_abs(buf.start_abs,
                            keep_after_line=b.base + len(b.lines) - 1)
    assert cut > 0
    assert len(b.lines) < 200                 # 确实裁掉了旧行
    assert b.line_at(b.base) is not None      # 绝对行号仍连续：base 处就是新首行
    assert b.line_at(b.base - 1) is None      # 被裁掉的行号取不到（越界安全）
