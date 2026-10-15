# -*- coding: utf-8 -*-
"""统一的基础目录：打包版=exe 所在目录，源码版=项目根目录。
配置文件与默认日志/导出文件都落在这里。"""
from __future__ import annotations

import os
import sys


def base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
