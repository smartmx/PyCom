# -*- coding: utf-8 -*-
"""配置持久化（JSON）+ 原版 sscom51.ini 导入。

键名尽量沿用原版语义（N1067 缓冲上限、N1081 波特率……），便于对照与迁移。
"""
from __future__ import annotations

import json
import math
import os

CHECKSUM_KINDS = ("none", "modbus_crc16", "ccitt_crc16", "crc8", "xor8", "add8", "add16")
INI_CHECKSUM_ORDER = ("none", "modbus_crc16", "add8", "xor8")   # N1060 索引 0..3 的原版语义
# 显示/发送编码下拉表（下拉框可编辑，任意 Python 编码名可直接手输）
ENCODINGS = [
    ("GBK (简体中文)", "gbk"),          # 默认；GB2312 的超集
    ("GB2312 (简体中文)", "gb2312"),
    ("GB18030", "gb18030"),
    ("UTF-8", "utf-8"),
    ("UTF-16LE", "utf-16-le"),
    ("UTF-16BE", "utf-16-be"),
    ("Big5 (繁体中文)", "big5"),
    ("Shift-JIS (日文)", "shift_jis"),
    ("EUC-JP (日文)", "euc_jp"),
    ("EUC-KR (韩文)", "euc_kr"),
    ("Windows-1252 (西欧)", "cp1252"),
    ("Latin-1 (字节直通)", "latin-1"),
]

DEFAULTS = {
    # 连接
    "mode": "serial",            # serial | tcp_client | tcp_server | udp
    "port": "",                  # COMx
    "baud": 115200,
    "databits": 8,
    "stopbits": 1,               # 1 | 2 | 1.5
    "parity": "none",            # none|even|odd|mark|space
    "flow": "none",              # none|rtscts|xonxoff
    "dtr": True,
    "rts": False,
    "auto_reconnect": True,
    "reconnect_ms": 2000,
    "open_on_start": False,     # 启动立刻打开串口（默认不勾选）
    "remote_ip": "127.0.0.1",
    "remote_port": 8080,
    "local_ip": "127.0.0.1",
    "local_port": 777,
    # 显示
    "encoding": "gbk",
    "hex_mode": False,
    "auto_scroll": True,         # 自动滚屏：关闭后新数据只入缓冲，视图停在当前位置
    "split_show": True,          # 时间戳+分包
    "split_ms": 20,
    "ts_color": "",              # 时间戳字体颜色（空=随主题）
    "buffer_cap": 128 * 1024 * 1024,
    "send_font": {"family": "宋体", "size": 10, "bold": False,
                  "underline": False, "color": ""},
    "recv_font": {"family": "宋体", "size": 10, "bold": False,
                  "underline": False, "color": ""},
    "bg_color": "#FFFFFF",
    "fg_color": "#000000",
    "theme": "light",
    # 发送（主区）
    "send_hex": False,
    "send_interval_ms": 1000,    # 定时发送间隔，0=关闭
    "send_newline": True,
    "checksum_kind": "none",
    "checksum_start": 1,
    # 档位（对齐 SSCOM N1075 下拉，"无校验"由算法下拉的 none 控制）：
    # 1~4=末尾N字节是帧尾,校验插在帧尾前面 9=旧版行为(追加在最末尾)
    "checksum_tail": 1,
    "checksum_tail_v2": True,    # 新档位语义标记：缺省时按旧语义迁移一次
    "main_text": "",
    # 发送文件
    "file_delay_ms": 0,          # 每发 256 字节延时
    # 多条发送
    "multisend_rows": [],        # [{note,hex,text,order,interval}]
    "_last_file": "",            # 上次发送的文件路径
    "multisend_loop": False,
    # 终端仿真（回车/按键两种发送方式互斥，本地回显仅按键方式可用）
    "term_enter_send": True,     # 回车后发送新行(带回显)；默认开启，对齐 SSCOM
    "term_key_send": False,      # 按键立即发送该键值
    "term_key_echo": False,      # 本地回显按键值（仅按键方式）
    "term_0d_add_0a": True,      # 发送回车 0x0d 后自动加 0x0a
    # 窗口（对齐原版 794x639 比例）
    "win_w": 900, "win_h": 660,
}


def _as_flag(v) -> bool:
    """宽容布尔归一化（顶层与嵌套字段共用）：字符串按 as_bool 口径判定，
    "false"/"0"/"no"/"off" 等一律为 False，其余真值转 bool。"""
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "y", "yes", "on")
    return bool(v)


class Settings:
    def __init__(self, path: str):
        self.path = path
        self.d: dict = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                user = json.load(f)
            if isinstance(user, dict):
                legacy = "checksum_tail_v2" not in user   # 旧校验语义配置
                self.d.update({k: v for k, v in user.items() if k in DEFAULTS})
                if legacy:
                    self._migrate_checksum_tail()
        except (OSError, ValueError, TypeError, AttributeError):
            pass    # 配置损坏/类型不对时回退默认值，不让应用崩溃
        self._normalize()
        # 旧配置校验 kind 名迁移（crc/add/xor -> 新七种命名）
        k = self.d.get("checksum_kind")
        if isinstance(k, str):
            k = {"crc": "modbus_crc16", "add": "add8",
                 "xor": "xor8"}.get(k, k)
        self.d["checksum_kind"] = k if k in CHECKSUM_KINDS else "none"

    def _migrate_checksum_tail(self) -> None:
        """旧配置（无 v2 标记）的 checksum_tail 是"倒数M字节"语义：
        有算法且 tail=0（原为整包追加）→ 档位 9（保留旧版追加行为）；
        tail>0 → 帧尾档 min(tail,4)。新语义保存过的配置不再迁移。"""
        t = self.d.get("checksum_tail")
        try:
            t = int(t)
        except (TypeError, ValueError):
            t = 0
        if t > 4:
            t = 4
        if t == 0 and self.d.get("checksum_kind") not in (None, "none"):
            t = 9
        self.d["checksum_tail"] = t

    def _normalize(self) -> None:
        """字段级归一化：任意合法 JSON 都不会让后续初始化崩溃；
        非法字段回退 DEFAULTS 对应值。"""

        def as_int(key: str, lo: int, hi: int) -> None:
            v = self.d.get(key)
            try:
                if isinstance(v, float) and not math.isfinite(v):
                    raise OverflowError          # JSON 的 1e309 → inf
                v = int(v)
            except (TypeError, ValueError, OverflowError):
                v = DEFAULTS[key]
            self.d[key] = min(max(v, lo), hi)

        def as_str(key: str) -> None:
            v = self.d.get(key)
            # None 归一为空串（避免变成 "None" 误导性参数）
            self.d[key] = "" if v is None else str(v)

        def as_bool(key: str) -> None:
            self.d[key] = _as_flag(self.d.get(key))

        def as_dict(key: str) -> None:
            if not isinstance(self.d.get(key), dict):
                self.d[key] = dict(DEFAULTS[key])

        def _to_int(v) -> int:
            try:
                if isinstance(v, float) and not math.isfinite(v):
                    raise OverflowError
                return int(v)
            except (TypeError, ValueError, OverflowError):
                return 0

        def as_font(key: str) -> None:
            """字体嵌套结构归一化：family/size/bold/underline/color 逐项校验。"""
            as_dict(key)
            f = self.d[key]
            fam = f.get("family")
            f["family"] = str(fam) if isinstance(fam, str) and fam else DEFAULTS[key]["family"]
            try:
                s = f.get("size")
                if isinstance(s, float) and not math.isfinite(s):
                    raise OverflowError          # JSON 的 1e309 → inf
                f["size"] = min(max(int(s or 10), 6), 72)
            except (TypeError, ValueError, OverflowError):
                f["size"] = DEFAULTS[key]["size"]
            f["bold"] = _as_flag(f.get("bold"))
            f["underline"] = _as_flag(f.get("underline"))
            c = f.get("color")
            f["color"] = c if isinstance(c, str) else ""

        as_int("win_w", 200, 20000)
        as_int("win_h", 200, 20000)
        as_int("split_ms", 1, 5000)
        as_int("buffer_cap", 1024, 1 << 29)      # 上限 512MB（菜单最大档）
        as_int("local_port", 1, 65535)
        as_int("remote_port", 1, 65535)
        as_int("file_delay_ms", 0, 1000)
        as_int("send_interval_ms", 0, 3_600_000)
        as_int("reconnect_ms", 100, 60_000)
        as_int("checksum_start", 1, 9999)
        as_int("checksum_tail", 0, 9999)
        # 合法档位 {1,2,3,4,9}（"无校验"由算法下拉控制）：0 或非法值回退——
        # 0 原表达"校验关闭"，回退默认档位 1 时算法一并置 none 保留该语义
        t = self.d["checksum_tail"]
        if t not in (1, 2, 3, 4, 9):
            if t > 4:
                t = 4
            else:
                t = 1
                if self.d.get("checksum_kind") not in (None, "none"):
                    self.d["checksum_kind"] = "none"
        self.d["checksum_tail"] = t
        as_int("databits", 5, 8)
        as_int("baud", 300, 12_000_000)
        try:                              # stopbits 允许 1/1.5/2
            sb = float(self.d.get("stopbits") or 1)
            self.d["stopbits"] = sb if sb in (1, 1.5, 2) else 1
        except (TypeError, ValueError, OverflowError):
            self.d["stopbits"] = 1
        for key in ("port", "remote_ip", "local_ip", "encoding", "main_text",
                    "_last_file", "ts_color", "theme", "parity", "flow"):
            as_str(key)
        for key in ("dtr", "rts", "hex_mode", "split_show", "auto_scroll",
                    "send_hex", "send_newline", "open_on_start", "auto_reconnect",
                    "term_enter_send", "term_key_send", "term_key_echo",
                    "term_0d_add_0a", "multisend_loop"):
            as_bool(key)
        # 终端仿真两种发送方式互斥（对齐 SSCOM）：同真时按键方式优先
        if self.d["term_enter_send"] and self.d["term_key_send"]:
            self.d["term_enter_send"] = False
        for key in ("send_font", "recv_font"):
            as_font(key)
        # 编码：必须是可用的文本编解码器（base64/hex 等非文本 codec 拒绝）
        enc = self.d.get("encoding")
        try:
            import codecs
            # 空值/None 回退 gbk；校验成功写回已验证的规范名（不回写原始空值）
            e = str(enc) if enc else "gbk"
            info = codecs.lookup(e)
            if not getattr(info, "_is_text_encoding", True):
                raise ValueError
            self.d["encoding"] = info.name
        except (LookupError, ValueError, TypeError):
            self.d["encoding"] = "gbk"
        if self.d["mode"] not in ("serial", "tcp_client", "tcp_server", "udp"):
            self.d["mode"] = "serial"
        if self.d["theme"] not in ("light", "dark"):
            self.d["theme"] = "light"
        if self.d["parity"] not in ("none", "even", "odd", "mark", "space"):
            self.d["parity"] = "none"
        if self.d["flow"] not in ("none", "rtscts", "xonxoff"):
            self.d["flow"] = "none"
        if self.d["stopbits"] not in (1, 1.5, 2):
            self.d["stopbits"] = 1
        if self.d["databits"] not in (5, 6, 7, 8):
            self.d["databits"] = 8
        if not isinstance(self.d.get("multisend_rows"), list):
            self.d["multisend_rows"] = []
        else:
            # 逐行归一化：非字典行丢弃，缺失字段回退该行默认值
            clean = []
            for r in self.d["multisend_rows"]:
                if not isinstance(r, dict):
                    continue
                note = r.get("note")
                text = r.get("text")
                clean.append({
                    # None 归一为空串（与顶层 as_str 同口径，避免 "None" 被发送）
                    "note": "" if note is None else str(note),
                    "hex": _as_flag(r.get("hex", False)),
                    "text": "" if text is None else str(text),
                    # 限幅：order/interval 会进 QTimer 与排序，超大合法整数
                    # 会触发 Shiboken __int64 溢出/OverflowError
                    "order": min(max(_to_int(r.get("order", 0)), 0), 9999),
                    "interval": min(max(_to_int(r.get("interval", 1000)),
                                        0), 3_600_000),
                })
            self.d["multisend_rows"] = clean

    def save(self) -> bool:
        """保存成功返回 True；失败时保留旧配置文件并返回 False。"""
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.d, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
            return True
        except (OSError, TypeError, ValueError):
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return False

    # ---- 原版 sscom51.ini 导入 ----
    def _read_ini_keys(self, ini_path: str) -> dict[str, str] | None:
        """读取 ini：GBK、跳过 ; 注释行；失败返回 None。"""
        try:
            keys: dict[str, str] = {}
            with open(ini_path, encoding="gbk", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith(";"):
                        k, v = line.split("=", 1)
                        keys[k.strip()] = v
            return keys
        except OSError:
            return None

    def _parse_ms_rows(self, keys: dict[str, str]) -> list[dict]:
        """解析原版多条字符串：定义头 N(100+i)=顺序,注释,延时；内容 Ni=H/A,数据。
        兼容带前导逗号的键值（原版部分键形如 "N101=,1,注释,1000"）。"""
        def ini_int(s, default, lo, hi):
            try:
                v = int(s)
            except (TypeError, ValueError):
                return default
            return min(max(v, lo), hi)

        rows = []
        for i in range(1, 100):
            head = keys.get("N%d" % (100 + i), "").strip().lstrip(",")
            body = keys.get("N%d" % i, "").strip().lstrip(",")
            if not head and not body:
                continue
            parts = [p.strip() for p in head.split(",")] if head else []
            bp = [p.strip() for p in body.split(",", 1)]
            is_hex = bool(bp) and bp[0].upper() == "H"
            text = bp[1] if len(bp) > 1 else ""
            interval = (ini_int(parts[2], 1000, 0, 3_600_000)
                        if len(parts) > 2 and parts[2] else 1000)
            order = ini_int(parts[0], 0, 0, 9999) if parts and parts[0] else 0
            rows.append({"order": order,
                         "note": parts[1] if len(parts) > 1 else "",
                         "hex": is_hex, "text": text, "interval": interval})
        return rows

    def import_ms_ini(self, ini_path: str) -> list[str]:
        """仅从原版 ini 导入多条字符串，不动其它任何配置。"""
        keys = self._read_ini_keys(ini_path)
        if keys is None:
            return ["读取失败: 无法打开文件"]
        rows = self._parse_ms_rows(keys)
        self.d["multisend_rows"] = rows
        return (["多条字符串 %d 条" % len(rows)] if rows
                else ["未发现多条字符串配置"])

    def import_original_ini(self, ini_path: str) -> list[str]:
        """导入原版配置，返回导入摘要行。"""
        keys = self._read_ini_keys(ini_path)
        if keys is None:
            return ["读取失败: 无法打开文件"]

        # 原版参数键值形如 "N1067=,134217728"（前导逗号是首个空字段），统一剥掉
        g = lambda k, d="": keys.get(k, d).strip().lstrip(",").strip()
        notes: list[str] = []

        def num(k, default):
            try:
                v = float(g(k) or default)
                if not math.isfinite(v):
                    raise OverflowError
                return v
            except (ValueError, OverflowError):
                return default

        def safe_int(k, default, lo=None, hi=None):
            try:
                v = int(num(k, default))
                if lo is not None:
                    v = max(v, lo)
                if hi is not None:
                    v = min(v, hi)
                return v
            except (TypeError, ValueError, OverflowError):
                return default

        # 多条字符串：导入即替换（INI 无多条时清空旧行）
        rows = self._parse_ms_rows(keys)
        self.d["multisend_rows"] = rows
        if rows:
            notes.append("多条字符串 %d 条" % len(rows))

        # 主面板
        if g("N1055", "A").upper().startswith("H"):
            self.d["send_hex"] = True
            self.d["main_text"] = g("N1054")
        else:
            self.d["send_hex"] = False
            self.d["main_text"] = g("N1053")
        self.d["send_interval_ms"] = safe_int("N1056", 1000, 0)
        self.d["send_newline"] = g("N1057", "N").upper().startswith("Y")
        self.d["hex_mode"] = g("N1059", "N").upper().startswith("Y")
        kind = safe_int("N1060", 0)
        self.d["checksum_kind"] = INI_CHECKSUM_ORDER[kind] if 0 <= kind < len(INI_CHECKSUM_ORDER) else "none"
        self.d["checksum_start"] = safe_int("N1074", 1, 1)
        # N1075 是 SSCOM 5.13.1 的放置档位（0=无校验字节,1~4=帧尾N字节）；
        # 旧版 ini 无此键：算法存在即按旧版"追加在末尾"（档位 9）
        if "N1075" in keys:
            self.d["checksum_tail"] = safe_int("N1075", 0, 0, 4)
            if self.d["checksum_tail"] == 0:
                self.d["checksum_kind"] = "none"   # 原档位 0 = 无校验字节
                self.d["checksum_tail"] = 1        # 档位已取消，回退默认
        elif self.d["checksum_kind"] != "none":
            self.d["checksum_tail"] = 9
        self.d["open_on_start"] = g("N1063", "N").upper().startswith("Y")
        self.d["dtr"] = g("N1061", "Y").upper().startswith("Y")
        self.d["rts"] = g("N1062", "N").upper().startswith("Y")
        self.d["split_show"] = g("N1064", "Y").upper().startswith("Y")
        self.d["split_ms"] = safe_int("N1065", 20, 1)
        try:                              # 原版字体键 N1088/89 → 接收字体
            rf = dict(self.d["recv_font"])
            fname = g("N1088", "")
            fsize = safe_int("N1089", 0, 0)
            if fname:
                rf["family"] = fname
            if fsize >= 6:
                rf["size"] = fsize
            self.d["recv_font"] = rf
        except (ValueError, TypeError):
            pass
        try:
            w, h = safe_int("N1082", 0, 0), safe_int("N1083", 0, 0)
            if w >= 200 and h >= 200:
                self.d["win_w"], self.d["win_h"] = w, h
        except ValueError:
            pass
        try:
            v = safe_int("N1066", 0xFFFFFF, 0)
            # 原版 N1066 是 BGR 整数，转 RGB
            self.d["bg_color"] = "#%02X%02X%02X" % (v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF)
        except ValueError:
            pass
        try:
            cap = safe_int("N1067", 0, 0)
            if cap > 0:
                self.d["buffer_cap"] = cap
        except ValueError:
            pass
        if g("N1068"):
            self.d["remote_ip"] = g("N1068")
        if g("N1069"):
            self.d["remote_port"] = safe_int("N1069", 8080, 1)
        if g("N1070"):
            self.d["local_port"] = safe_int("N1070", 777, 1)
        self.d["term_enter_send"] = g("N1071", "N").upper().startswith("Y")
        self.d["term_key_send"] = g("N1072", "N").upper().startswith("Y")
        self.d["term_key_echo"] = g("N1073", "N").upper().startswith("Y")
        self.d["term_0d_add_0a"] = g("N1076", "Y").upper().startswith("Y")
        mode = safe_int("N1080", 0)
        self.d["mode"] = ("serial", "tcp_client", "tcp_server", "udp")[mode] \
            if 0 <= mode <= 3 else "serial"
        try:
            self.d["baud"] = safe_int("N1081", 115200, 300)
        except ValueError:
            pass
        try:
            self.d["file_delay_ms"] = safe_int("N1051", 0, 0)
        except ValueError:
            pass
        # 统一钳制：复用 JSON 归一化的同一套上下界（端口/缓冲/窗口/字体等），
        # 异常 INI 的大数值在这里收口，不再依赖 UI 控件兜底
        self._normalize()
        notes.append("参数键 %d 个" % len(keys))
        return notes
