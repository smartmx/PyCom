# -*- coding: utf-8 -*-
"""校验算法：对齐原版 SSCOM 的加校验选项。

种类（kind）: none / modbus_crc16 / ccitt_crc16 / crc8 / xor8 / add8 / add16
档位（tail，对齐原版 N1075 下拉；"无校验"由 kind=none 控制）：
  tail=1~4   假设末尾 N 字节是帧尾：校验对"第 start 字节至帧尾前"计算，
             并插在帧尾前面（数据 + 校验 + 帧尾）
  tail=9     旧版行为保留：校验对"第 start 字节至末尾"计算，追加在最末尾
  tail=0     不会由 UI 产生，防御性视为不附加

16 位校验附加字节均为低字节在前（与 Modbus 一致）。ADD/XOR 原版规格未公开，
按通行惯例实现为 8 位累加/异或。
"""
from __future__ import annotations

CHECKSUM_KINDS = ("none", "modbus_crc16", "ccitt_crc16", "crc8", "xor8", "add8", "add16")

# pycom.json 旧配置 kind 名 -> 新名
_LEGACY_KINDS = {"crc": "modbus_crc16", "add": "add8", "xor": "xor8"}


def modbus_crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def ccitt_crc16(data: bytes) -> int:
    """CRC-16/CCITT（poly 0x1021, init 0x0000, MSB first，即 XMODEM 型）。
    校验值 '123456789'->0x31C3。注意：CCITT-FALSE（init 0xFFFF）的向量为 0x29B1，勿混淆。"""
    crc = 0x0000
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if (crc & 0x8000) else (crc << 1)
            crc &= 0xFFFF
    return crc


def crc8(data: bytes) -> int:
    """CRC-8（poly 0x07, init 0x00）。校验值 '123456789'->0xF4"""
    crc = 0x00
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) if (crc & 0x80) else (crc << 1)
            crc &= 0xFF
    return crc


def add_sum(data: bytes) -> int:
    return sum(data) & 0xFF


def add_sum16(data: bytes) -> int:
    return sum(data) & 0xFFFF


def xor_sum(data: bytes) -> int:
    x = 0
    for b in data:
        x ^= b
    return x & 0xFF


def checksum_appendix(kind: str, body: bytes) -> bytes:
    """kind 对应的校验附加字节；none 返回空。"""
    if kind == "modbus_crc16":
        crc = modbus_crc16(body)
        return bytes((crc & 0xFF, crc >> 8))    # 低字节在前
    if kind == "ccitt_crc16":
        crc = ccitt_crc16(body)
        return bytes((crc & 0xFF, crc >> 8))
    if kind == "crc8":
        return bytes((crc8(body),))
    if kind == "xor8":
        return bytes((xor_sum(body),))
    if kind == "add8":
        return bytes((add_sum(body),))
    if kind == "add16":
        s = add_sum16(body)
        return bytes((s & 0xFF, s >> 8))
    return b""


def slice_range(n: int, start: int, tail: int) -> tuple[int, int]:
    """返回 (lo, hi) 校验范围下标。"""
    if n == 0:
        return 0, 0
    if tail > 0:
        hi = max(0, n - tail)
    else:
        hi = n
    lo = max(0, (start - 1) if start > 0 else 0)
    lo = min(lo, hi)
    return lo, hi


def checksum_body_slice(payload: bytes, start: int, tail: int) -> bytes:
    """返回校验值覆盖的字节段（SSCOM 语义，见 append_checksum）。"""
    if tail == 9:                        # 旧版：第 start 字节至末尾
        lo, hi = slice_range(len(payload), start, 0)
        return payload[lo:hi]
    n = max(1, min(4, tail))
    cut = len(payload) - n
    if cut <= 0:
        return b""
    lo = min(max(0, start - 1), cut)
    return payload[lo:cut]


def append_checksum(payload: bytes, kind: str, start: int = 1, tail: int = 0) -> bytes:
    """按 SSCOM 语义附加校验（tail 档位见模块头）。

    帧尾档（1~4）把校验插在帧尾前面：数据 + 校验 + 帧尾；
    数据不比帧尾长时无处插入，原样发送。
    """
    kind = _LEGACY_KINDS.get(kind, kind)
    if kind == "none" or tail == 0:
        return payload
    body = checksum_body_slice(payload, start, tail)
    appendix = checksum_appendix(kind, body)
    if not appendix:
        return payload
    if tail == 9:
        return payload + appendix
    cut = len(payload) - max(1, min(4, tail))
    if cut <= 0:
        return payload
    return payload[:cut] + appendix + payload[cut:]
