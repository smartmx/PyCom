# -*- coding: utf-8 -*-
"""PyCom 主窗口：布局按原版 SSCOM 5.13.1 的 DFM 树逐区对齐。

原版结构（DFM 坐标）：
  [接收 Memo(0,0,357x404) | Splitter | Panel2 多条发送(360,0,418x404,默认隐藏)]
  Panel5(0,404,778x24)  清除窗口|打开文件|文件名|发送文件|停止|清发送区|最前|保存参数|扩展
  ProgressBar(0,428,778x16)
  Panel1(0,444,778x98)  端口号/打开串口/LED/波特率/RTS/DTR | Panel8 HEX显示/保存/时间戳分包
                        | Panel9 HEX发送/定时/校验 | Edit_Data 多行发送框 | 发送大按钮
  StatusBar(0,561,778x20)
"""
from __future__ import annotations

import os
import sys
import time
import weakref
from functools import partial

from PySide2.QtCore import QEvent, QObject, QPoint, QProcess, QRect, QSize, Qt, QTimer, QUrl
from PySide2.QtGui import QColor, QDesktopServices, QFont, QGuiApplication, \
    QIntValidator, QKeySequence, QPainter, QPalette, QPen
from PySide2.QtWidgets import (QAbstractSpinBox, QActionGroup, QApplication,
                               QCheckBox, QComboBox, QDialog, QFileDialog, QFrame,
                               QGridLayout, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QShortcut,
                               QSpinBox, QSplitter, QStyle,
                               QStyleOptionButton, QStyledItemDelegate,
                               QTabWidget, QTableView, QVBoxLayout, QWidget)

from app.core.display import LineBuilder, dir_marker
from app.core.multisend import COL_HEX, COL_SEND, MultiSendLoop, MultiSendModel
from app.core.rxbuf import RxBuffer
from PySide2.QtSerialPort import QSerialPort
from app.core.settings import BUF_CAP_MAX, ENCODINGS, Settings
from app.version import APP_VERSION


class _NoWheelCombo(QComboBox):
    """禁用滚轮切换条目（误触滚轮会改串口参数），只允许点选下拉或手动输入。
    下拉列表展开后的滚动由弹出的列表视图自行处理，不受影响。"""

    def wheelEvent(self, ev):
        ev.ignore()


# 数字输入框隐藏上下按钮的样式（setButtonSymbols(NoButtons) 会触发 Qt 样式表渲染缺陷：文本不绘制）
_HIDE_SPIN_BTNS_QSS = ("QAbstractSpinBox::up-button { width: 0px; height: 0px; }"
                       "QAbstractSpinBox::down-button { width: 0px; height: 0px; }")


class _DialogDarkFilter(QObject):
    """应用级单例事件过滤器：顶层对话框显示时让"当前"主窗口做 DWM 置暗。
    Qt 会经过滤器列表**强持过滤器对象的 Python 包装**——若每个主窗口都把
    自己装成应用级过滤器，旧窗口将永不释放（且事件分派随窗口数 O(N²) 变慢，
    实测 20 轮建删全量跑拖慢 10 倍），故全局只装一个，经弱引用转发。"""
    _win = None                              # weakref.ref[MainWindow]

    def eventFilter(self, obj, ev):
        w = self._win() if self._win is not None else None
        if w is not None and ev.type() == QEvent.Show \
                and isinstance(obj, QDialog) and obj.isWindow():
            # 对话框标题栏跟随深色主题：Qt5 没有 Qt6.5+ 的
            # styleHints().setColorScheme(Dark)（主线深色标题栏靠它自动跟随），
            # 须对每个顶层窗口单独做 DWM 置暗（Win10 1809+ 生效；Win7 无此
            # DWM 属性，调用自动忽略、标题栏保持系统浅色，属系统限制）。
            w._dark_titlebar_widget(obj)
        return False


def _ui_is_dark() -> bool:
    """当前是否深色主题：深色调色板 Window 亮度远低于浅色（浅色走系统标准
    色板）。原生样式直绘的部位（多条发送表格委托）按它切换手绘/原生。"""
    return QApplication.palette().color(QPalette.Window).lightness() < 100


class _SendBtnDelegate(QStyledItemDelegate):
    """多条发送"点击发送"列：绘制按钮外观（文字=注释），双击进入注释编辑。"""

    def paint(self, painter, option, index):
        if _ui_is_dark():
            # Qt5 原生样式（windowsvista）画按钮不吃深色调色板（win64 靠
            # Qt6.5+ setColorScheme 让原生样式自动跟随，本分支没有该 API），
            # 深色下改按主题 QSS 同款颜色手绘；浅色仍走原生（观感即系统按钮）。
            v = theme.vars("dark")
            r = option.rect.adjusted(3, 2, -3, -2)
            painter.save()
            painter.setPen(QColor(v["border"]))
            painter.setBrush(QColor(v["btn"]))
            painter.drawRoundedRect(r, 2, 2)
            painter.setPen(QColor(v["fg"]))
            painter.drawText(r, Qt.AlignCenter, index.data() or "发送")
            painter.restore()
            return
        btn = QStyleOptionButton()
        btn.rect = option.rect.adjusted(3, 2, -3, -2)
        btn.text = index.data() or "发送"
        btn.state = QStyle.State_Enabled | QStyle.State_Raised
        QApplication.style().drawControl(QStyle.CE_PushButton, btn, painter)


class _HexCheckDelegate(QStyledItemDelegate):
    """多条发送"HEX"列：居中绘制勾选框（整格点击由主窗口 clicked 切换）。"""

    def paint(self, painter, option, index):
        checked = index.data(Qt.CheckStateRole) == Qt.Checked
        r = option.rect
        box = QRect(r.x() + (r.width() - 14) // 2,
                    r.y() + (r.height() - 14) // 2, 14, 14)
        if _ui_is_dark():
            # 未勾选的原生指示器是白底块（原生样式不吃深色调色板，同上），
            # 深色下改手绘：input 底 + border 框，勾选画对勾。
            v = theme.vars("dark")
            painter.save()
            painter.setPen(QColor(v["border"]))
            painter.setBrush(QColor(v["input"]))
            painter.drawRoundedRect(box, 2, 2)
            if checked:
                painter.setPen(QPen(QColor(v["fg"]), 2))
                painter.drawPolyline([QPoint(box.x() + 3, box.y() + 7),
                                      QPoint(box.x() + 6, box.y() + 10),
                                      QPoint(box.x() + 11, box.y() + 4)])
            painter.restore()
            return
        opt = QStyleOptionButton()
        opt.rect = box
        opt.state = QStyle.State_Enabled | (QStyle.State_On if checked else QStyle.State_Off)
        QApplication.style().drawPrimitive(QStyle.PE_IndicatorCheckBox, opt, painter)

from app.core.terminal import cr_add_lf, key_to_bytes
from app.core.txfile import FileSender
from app.paths import base_dir
from app.ui import theme
from app.ui.rxview import RxView

LINK_MODES = [("串口", "serial"), ("TCP客户端", "tcp_client"),
              ("TCP服务端", "tcp_server"), ("UDP", "udp")]
BUF_SIZES = [("500K", 500_000), ("1M", 1_048_576),
             ("2M", 2_097_152), ("5M", 5_242_880), ("10M", 10_485_760),
             ("20M", 20_971_520), ("50M", 52_428_800)]
# 显示缓冲上限（本分支是 32 位产物）：菜单到 50M 为止，旧配置里的更大值开窗时收敛。
# 32 位进程用户地址空间只有 2GB，实测（tools/mem_probe.py）：256M 档峰值提交 842MB、
# 512M 档灌到 310MB 就 MemoryError——50M 档（2026-10-20 加入）远离该 OOM 线，运行中
# 真耗尽内存再逐级降档（见 _shrink_on_oom）。口径常量在 settings.BUF_CAP_MAX。
_MAX_BUF = BUF_CAP_MAX
_MIN_BUF = 500_000
# 窄窗阈值：宽度小于此值隐藏右侧发送/选项区（对齐 SSCOM 向左缩到只剩配置框）
_NARROW_W = 920   # 窄窗阈值：与文件块阈值一致（<920 只留串口面板+接收区，
                   # 620~920 的宽度塞不下完整宽布局，列间会互相入侵）


class ClipBox(QWidget):
    """SSCOM 式截断容器：对父布局贡献的最小**宽度**恒为 0，内部控件保持自然
    宽度不被压缩；窗口缩窄时内部内容整体向右溢出、被窗口边缘直接裁掉
    （不隐藏）——对齐原版缩窗行为。前提：内部非伸缩控件已钉住自然宽度。
    高度保持自然最小值：只做横向截断，高度归零会把所在网格行压扁
    （V0.0.4 实测回归：选项两列被挤扁、发送输入框长高）。"""

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings):
        super().__init__()
        self.cfg = settings
        self.setWindowTitle("PyCom32 串口/网络数据调试器")
        # 允许向左缩到串口配置框宽度（窄窗时右侧内容被窗口边缘截断，不隐藏）
        self.setMinimumWidth(330)
        self.menuBar().setNativeMenuBar(False)   # 深色 QSS 才能作用于顶部菜单
        self._mode = self.cfg.d["mode"]
        self._encoding = self.cfg.d["encoding"]   # 由"编码"菜单/自定义对话框设置
        self.builder = LineBuilder(encoding=self.cfg.d["encoding"],
                                   hex_mode=self.cfg.d["hex_mode"],
                                   split=self.cfg.d["split_show"],
                                   split_ms=self.cfg.d["split_ms"])
        self.buf = RxBuffer(self._clamp_cap(self.cfg.d["buffer_cap"]))
        self.link = None
        self.tx_bytes = 0
        self.rx_bytes = 0
        self._speed_samples = []     # (monotonic, tx, rx) 采样，速率取 1s 滑窗
        self._link_err = False       # 线路 ERR 指示
        self._link_pending = False   # 已请求打开但未连上（占用重试/断线重连）：按钮转「取消」
        self._tx_pend: list[tuple[bytes, bool]] = []  # 待发余量分段队列：(字节, 是否记录TX显示)
        self._term_line: list[str] = []  # 终端仿真"回车后发送行"的当前输入行（按字符单元）
        self._term_line_raw: list[bytes] = []  # 各字符单元的实际编码字节（发送与回显严格一致）
        self._rx_log = None           # 接收数据到文件（txt，显示内容）
        self._rx_log_written = 0      # 已写日志行数（绝对行号=base+列表下标）

        self._build_ui()
        self._build_menus()
        self._apply_settings()
        self._wire()
        QTimer.singleShot(0, self._startup)

    # ============================================================ UI 构建
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(4, 3, 4, 3)
        root.setSpacing(3)

        # ---------- 顶区：接收区 | 多条发送（原 Panel2，默认隐藏） ----------
        self.splitter = QSplitter(Qt.Horizontal)
        self.rxview = RxView(self.builder, self.buf)
        self.splitter.addWidget(self.rxview)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        rv.addWidget(self.tabs)

        ms_w = QWidget()
        mv = QVBoxLayout(ms_w)
        mv.setContentsMargins(2, 2, 2, 2)
        mv.setSpacing(2)
        self.ms_model = MultiSendModel(self.cfg.d.get("multisend_rows") or [])
        self.ms_table = QTableView()
        self.ms_table.setModel(self.ms_model)
        self.ms_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)   # 内容列占满剩余宽度
        self.ms_table.setColumnWidth(0, 110)   # 点击发送
        self.ms_table.setColumnWidth(1, 46)    # 格式
        self.ms_table.setColumnWidth(3, 50)    # 顺序
        self.ms_table.setColumnWidth(4, 66)    # 延时ms
        self.ms_table.setItemDelegateForColumn(0, _SendBtnDelegate(self.ms_table))
        self.ms_table.setItemDelegateForColumn(1, _HexCheckDelegate(self.ms_table))
        mv.addWidget(self.ms_table, 1)
        ms_row = QHBoxLayout()
        self.btn_ms_add = QPushButton("+")
        self.btn_ms_del = QPushButton("-")
        self.btn_ms_help = QPushButton("多条发送说明")
        self.cb_ms_loop = QCheckBox("循环发送")
        self.btn_ms_import = QPushButton("导入sscom.ini")
        self.btn_ms_import.setToolTip("仅导入原版 ini 的多条字符串配置")
        for w in (self.btn_ms_add, self.btn_ms_del, self.cb_ms_loop,
                  self.btn_ms_help, self.btn_ms_import):
            ms_row.addWidget(w)
        ms_row.addStretch(1)
        mv.addLayout(ms_row)
        self.tabs.addTab(ms_w, "多条字符串发送")
        from app.ui.calculator_dlg import CalculatorWidget
        self.tabs.addTab(CalculatorWidget(), "进制转换")   # 多字符串发送与ASCII码表之间
        self.tabs.addTab(self._build_ascii_table(), "ASCII码表")
        self.splitter.addWidget(right)
        # 原版比例：接收 Memo 357 : 多条发送 418 ≈ 46:54
        # 扩展面板默认宽一些的接收区（46:54 太宽，用户要求收窄）
        self.splitter.setStretchFactor(0, 62)
        self.splitter.setStretchFactor(1, 38)
        self.splitter.setSizes([62, 38])
        root.addWidget(self.splitter, 1)

        # ---------- 查找条（隐藏，Ctrl+F） ----------
        from app.ui.find_bar import FindBar
        self.find_bar = FindBar(self.rxview.set_matches, self.rxview.scroll_to_line, self)
        self.find_bar.set_builder_provider(lambda: self.builder)
        root.addWidget(self.find_bar)

        # ---------- Panel5 行：清除/文件/收起 ----------
        row5 = QHBoxLayout()
        row5.setSpacing(3)
        self.btn_clear = QPushButton("清除窗口")
        self.btn_find = QPushButton("查找")
        self.btn_new_window = QPushButton("新窗口")
        self.btn_new_window.setToolTip("再启动一个独立的应用实例")
        self.cb_auto_scroll = QCheckBox("自动滚屏")
        self.cb_auto_scroll.setToolTip("关闭后新数据只入缓冲，接收区停在当前位置")
        self.spacer_file = QWidget()   # 文件块对齐垫片（_startup 实测定宽）
        self._file_pad = 0             # 垫片上限（须先于首个 resizeEvent 存在）
        self.btn_open_file = QPushButton("打开文件")
        self.edit_file = QLineEdit()
        self.edit_file.setPlaceholderText("发送文件路径")
        self.btn_send_file = QPushButton("发送文件")
        self.btn_stop_file = QPushButton("停止")
        self.btn_clear_send = QPushButton("清发送区")
        self.btn_collapse = QPushButton("收起")
        self.btn_collapse.setCheckable(True)
        self.btn_collapse.setToolTip("收起/展开底部串口参数面板")
        self.btn_keep_top = QPushButton("最前")
        self.btn_keep_top.setCheckable(True)
        self.btn_save_params = QPushButton("保存参数")
        self.btn_expand = QPushButton("扩展")
        self.btn_expand.setCheckable(True)
        for w in (self.btn_clear, self.btn_find, self.btn_new_window):
            row5.addWidget(w)
        row5.addSpacing(16)               # 与"自动滚屏"间隔一个汉字宽
        row5.addWidget(self.cb_auto_scroll)
        # 右侧可截断区（SSCOM 式缩窗）：文件块与右侧按钮保持自然宽度，
        # 缩窗时整区向右溢出、被窗口边缘裁掉，左侧各键永不压缩
        self.tool_right = ClipBox()
        tr = QHBoxLayout(self.tool_right)
        tr.setContentsMargins(0, 0, 0, 0)
        tr.setSpacing(3)
        for w in (self.spacer_file, self.btn_open_file, self.edit_file,
                  self.btn_send_file, self.btn_stop_file,
                  self.btn_clear_send):
            tr.addWidget(w)
        tr.setStretchFactor(self.edit_file, 1)
        tr.addStretch(1)
        for w in (self.btn_collapse, self.btn_keep_top,
                  self.btn_save_params, self.btn_expand):
            tr.addWidget(w)
        row5.addWidget(self.tool_right, 1)
        root.addLayout(row5)

        # ---------- 进度条（原 ProgressBar1） ----------
        self.progress = QProgressBar()
        self.progress.setMaximumHeight(14)
        self.progress.hide()
        root.addWidget(self.progress)

        # ---------- 底部 Panel1 ----------
        self.panel1 = panel1 = QWidget()
        g = QGridLayout(panel1)
        g.setContentsMargins(2, 2, 2, 2)
        g.setHorizontalSpacing(6)
        g.setVerticalSpacing(3)

        # 列0：串口/网络连接区（原 225 宽）
        col0 = QVBoxLayout()
        col0.setSpacing(3)

        self.serial_box = QWidget()
        sv = QVBoxLayout(self.serial_box)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(3)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("端口号"))
        self.cmb_port = QComboBox()
        # 限宽：长端口名截断（弹出列表仍完整显示），保证"线路"行是配置区
        # 最宽行——BREAK 右缘即面板右缘
        self.cmb_port.setMaximumWidth(190)
        self.cmb_port.view().setMinimumWidth(220)
        self.btn_refresh = QPushButton("刷新")
        r1.addWidget(self.cmb_port, 1)
        r1.addWidget(self.btn_refresh)
        sv.addLayout(r1)
        r2 = QHBoxLayout()        # 打开/关闭(半宽靠左) + 波特率(右半)
        self.lbl_led = QLabel()
        self.lbl_led.setFixedSize(14, 14)
        self.lbl_led.setStyleSheet("border-radius:7px; background:#9E9E9E;")
        self.btn_link = QPushButton("打开串口")
        self.btn_link.setMinimumHeight(26)
        r2.addWidget(self.lbl_led)
        r2.addWidget(self.btn_link, 1)        # 左半：按钮宽度约为原来一半
        r2.addWidget(QLabel("波特率"))
        self.cmb_baud = _NoWheelCombo()
        for b in (300, 600, 1200, 2400, 4800, 9600, 19200, 38400, 57600,
                  115200, 230400, 460800, 921600, 1000000, 1500000,
                  2000000, 3000000):
            self.cmb_baud.addItem(str(b), b)
        self.cmb_baud.addItem("自定义")   # SSCOM 语义：选中即清空等待输入任意波特率
        self.cmb_baud.setEditable(True)
        # 右半随面板拉伸（与按钮等宽），3000000 等长值完整显示
        self.cmb_baud.lineEdit().setValidator(QIntValidator(300, 12000000, self))
        self.cmb_baud.activated.connect(self._on_baud_activated)
        r2.addWidget(self.cmb_baud, 1)
        sv.addLayout(r2)
        r3 = QHBoxLayout()        # 数据位 / 停止位 / 校验位 一行
        r3.addWidget(QLabel("数据位"))
        self.cmb_data = _NoWheelCombo()
        self.cmb_data.addItems(["8", "7", "6", "5"])
        self.cmb_data.setFixedWidth(36)
        r3.addWidget(self.cmb_data)
        r3.addWidget(QLabel("停止位"))
        self.cmb_stop = _NoWheelCombo()
        self.cmb_stop.addItems(["1", "1.5", "2"])
        self.cmb_stop.setFixedWidth(44)
        r3.addWidget(self.cmb_stop)
        r3.addWidget(QLabel("校验位"))
        self.cmb_parity = _NoWheelCombo()
        for _lbl, _kind in (("无校验", "none"), ("偶校验", "even"), ("奇校验", "odd"),
                            ("Mark", "mark"), ("Space", "space")):
            self.cmb_parity.addItem(_lbl, _kind)
        # 闭合态收窄（拉伸占满行尾），弹出列表仍按最长项展开
        self.cmb_parity.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cmb_parity.setMinimumContentsLength(3)
        self.cmb_parity.view().setMinimumWidth(96)
        r3.addWidget(self.cmb_parity, 1)      # 占满行尾：右缘与面板右缘对齐
        sv.addLayout(r3)
        r4 = QHBoxLayout()        # 流控（加宽） + DTR / RTS
        r4.addWidget(QLabel("流控"))
        self.cmb_flow = _NoWheelCombo()
        for _lbl, _kind in (("无流控", "none"), ("RTS/CTS", "rtscts"), ("XON/XOFF", "xonxoff")):
            self.cmb_flow.addItem(_lbl, _kind)
        self.cmb_flow.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cmb_flow.setMinimumContentsLength(4)
        self.cmb_flow.view().setMinimumWidth(96)
        r4.addWidget(self.cmb_flow, 1)        # 原来的流控加宽
        self.cb_dtr = QCheckBox("DTR")
        self.cb_rts = QCheckBox("RTS")
        r4.addWidget(self.cb_dtr)
        r4.addWidget(self.cb_rts)
        sv.addLayout(r4)
        r4 = QHBoxLayout()
        self.lbl_line = QLabel("线路")
        r4.addWidget(self.lbl_line)
        self.pin_leds = {}
        for nm in ("DCD", "DSR", "CTS", "RI"):
            lbl = QLabel(nm)
            lbl.setFixedSize(34, 18)
            lbl.setAlignment(Qt.AlignCenter)
            self.pin_leds[nm] = lbl
            self._style_pin(lbl, False)
            r4.addWidget(lbl)
        err_lbl = QLabel("ERR")
        err_lbl.setFixedSize(34, 18)
        err_lbl.setAlignment(Qt.AlignCenter)
        self.pin_leds["ERR"] = err_lbl
        self._style_pin(err_lbl, False, err=True)
        r4.addWidget(err_lbl)
        self.cb_break = QCheckBox("BREAK")    # 紧跟 ERR 之后
        r4.addWidget(self.cb_break)
        r4.addStretch(1)
        sv.addLayout(r4)
        col0.addWidget(self.serial_box)

        self.net_box = QWidget()
        nv = QVBoxLayout(self.net_box)
        nv.setContentsMargins(0, 0, 0, 0)
        nv.setSpacing(3)
        # 网络配置宽度不设上限：与串口页同一列（col0），两页配置宽度自然对齐
        nled = QHBoxLayout()
        self.lbl_led2 = QLabel()
        self.lbl_led2.setFixedSize(14, 14)
        self.lbl_led2.setStyleSheet("border-radius:7px; background:#9E9E9E;")
        nled.addWidget(self.lbl_led2)
        self.lbl_net_mode = QLabel("")    # 当前网络模式：TCP服务器/TCP客户端/UDP
        nled.addWidget(self.lbl_net_mode)
        nled.addStretch(1)
        nv.addLayout(nled)
        nr1 = QHBoxLayout()
        nr1.addWidget(QLabel("远程"))
        self.edit_remote_ip = QLineEdit()     # tooltip 由 _sync_net_locks 按模式管理
        self.edit_remote_port = QSpinBox()
        self.edit_remote_port.setRange(1, 65535)
        self.edit_remote_port.setMaximumWidth(70)
        self.edit_remote_port.setStyleSheet(_HIDE_SPIN_BTNS_QSS)
        nr1.addWidget(self.edit_remote_ip, 1)
        nr1.addWidget(self.edit_remote_port)
        nv.addLayout(nr1)
        nr2 = QHBoxLayout()               # 连接/断开紧跟远程设置之下（加长各占一半）
        self.btn_net_connect = QPushButton("连接")
        self.btn_net_disc = QPushButton("断开")
        nr2.addWidget(self.btn_net_connect, 1)
        nr2.addWidget(self.btn_net_disc, 1)
        nv.addLayout(nr2)
        nr3 = QHBoxLayout()               # 网卡选择：选中后本地 IP 只列该网卡地址
        nr3.addWidget(QLabel("网卡"))
        self.cmb_nic = _NoWheelCombo()
        self.cmb_nic.setToolTip("选择本机网络接口；本地IP随之联动")
        nr3.addWidget(self.cmb_nic, 1)
        self.btn_nic_refresh = QPushButton("刷新")
        self.btn_nic_refresh.setFixedWidth(70)    # 与端口号输入框同宽，右缘对齐
        self.btn_nic_refresh.setToolTip("重新枚举本机网络接口（热插拔后使用）")
        nr3.addWidget(self.btn_nic_refresh)
        nv.addLayout(nr3)
        nr4 = QHBoxLayout()               # 本地 IP 选择放最下面
        nr4.addWidget(QLabel("本地"))
        self.cmb_local_ip = _NoWheelCombo()
        nr4.addWidget(self.cmb_local_ip, 1)
        self.edit_local_port = QSpinBox()
        self.edit_local_port.setRange(1, 65535)
        self.edit_local_port.setMaximumWidth(70)
        self.edit_local_port.setStyleSheet(_HIDE_SPIN_BTNS_QSS)
        nr4.addWidget(self.edit_local_port)
        nv.addLayout(nr4)
        col0.addWidget(self.net_box)

        self.btn_send = QPushButton("发  送")
        self.btn_send.setMinimumHeight(34)
        col0.addStretch(1)                # 发送按钮压到底：与发送输入框底边对齐
        col0.addWidget(self.btn_send)
        g.addLayout(col0, 0, 0, 2, 1)

        # 列1（原 Panel8）：接收选项
        p8 = QVBoxLayout()
        p8.setSpacing(3)
        p8r1 = QHBoxLayout()
        self.cb_hex_show = QCheckBox("HEX显示")
        self.btn_save = QPushButton("保存数据")
        self.cb_rxfile = QCheckBox("接收数据到文件")
        for w in (self.cb_hex_show, self.btn_save, self.cb_rxfile):
            p8r1.addWidget(w)
        p8r1.addStretch(1)
        p8.addLayout(p8r1)
        p8r2 = QHBoxLayout()
        self.cb_timestamp = QCheckBox("加时间戳和分包显示")
        self.sp_split_ms = QSpinBox()
        self.sp_split_ms.setRange(1, 5000)
        p8r2.addWidget(self.cb_timestamp)
        self.btn_ts_color = QPushButton()
        self.btn_ts_color.setFixedSize(20, 18)
        self.btn_ts_color.setToolTip("时间戳字体颜色")
        p8r2.addWidget(self.btn_ts_color)     # 颜色设置在逗号前面（对齐 SSCOM）
        p8r2.addWidget(QLabel(", 超时"))
        p8r2.addWidget(self.sp_split_ms)
        p8r2.addWidget(QLabel("ms"))
        p8r2.addStretch(1)
        p8.addLayout(p8r2)
        p8.addStretch(1)        # 选项行贴顶，剩余高度让给发送输入框
        # 两列选项同包一个可截断容器：缩窗时保持各自宽度、被窗口边缘裁掉
        # （对齐 SSCOM 窄窗下 HEX显示/加时间戳 被截半的样子），不再整列隐藏
        self.opt_box = ClipBox()
        ob = QHBoxLayout(self.opt_box)
        ob.setContentsMargins(0, 0, 0, 0)
        ob.setSpacing(6)
        ob.addLayout(p8)
        g.addWidget(self.opt_box, 0, 1, 1, 3)   # 跨 1~3 列：右缘与发送编辑框对齐

        # 列2（原 Panel9）：发送选项
        p9 = QVBoxLayout()
        p9.setSpacing(3)
        p9r1 = QHBoxLayout()
        self.cb_send_hex = QCheckBox("HEX发送")
        self.cb_timed = QCheckBox("定时发送:")
        self.sp_timed = QSpinBox()
        self.sp_timed.setRange(1, 3_600_000)   # 最小 1ms（Windows 实际精度受系统定时器粒度限制）
        self.cb_newline = QCheckBox("加回车换行")
        for w in (self.cb_send_hex, self.cb_timed, self.sp_timed,
                  QLabel("ms/次"), self.cb_newline):
            p9r1.addWidget(w)
        p9r1.addStretch(1)
        p9.addLayout(p9r1)
        p9r2 = QHBoxLayout()
        self.sp_chk_start = QSpinBox()
        self.sp_chk_start.setRange(1, 9999)
        self.sp_chk_start.setFixedWidth(48)     # 只显示 3 位数字宽，收窄整行
        self.cmb_chk_place = QComboBox()        # 对齐 SSCOM N1075 的放置档位
        for _lbl, _v in (("第1.末尾1字节是帧尾,校验插帧尾前", 1),
                         ("第2.末尾2字节是帧尾,校验插帧尾前", 2),
                         ("第3.末尾3字节是帧尾,校验插帧尾前", 3),
                         ("第4.末尾4字节是帧尾,校验插帧尾前", 4),
                         ("末尾追加校验[旧版无帧尾]", 9)):
            self.cmb_chk_place.addItem(_lbl, _v)
        self.cmb_chk_place.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cmb_chk_place.setMinimumContentsLength(4)
        self.cmb_chk_place.view().setMinimumWidth(210)
        self.cmb_checksum = QComboBox()
        # 闭合态收窄以少占行宽；点开下拉仍按最长项展开（不挤同行其它控件）
        self.cmb_checksum.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cmb_checksum.setMinimumContentsLength(4)
        self.cmb_checksum.view().setMinimumWidth(132)
        for _label, _kind in (("无校验", "none"), ("ModbusCRC16", "modbus_crc16"),
                              ("CCITT_CRC16", "ccitt_crc16"), ("CRC8", "crc8"),
                              ("XOR8", "xor8"), ("ADD8", "add8"), ("ADD16", "add16")):
            self.cmb_checksum.addItem(_label, _kind)
        self.edit_checksum_view = QLineEdit()
        self.edit_checksum_view.setReadOnly(True)
        self.edit_checksum_view.setMinimumWidth(96)
        self.edit_checksum_view.setToolTip("按当前发送内容实时计算的校验字节（HEX）")
        self.edit_checksum_view.setPlaceholderText("校验值")
        self._chk_sep = QFrame()                # 校验配置与前面分组之间的竖分隔线
        self._chk_sep.setFrameShape(QFrame.VLine)
        self._chk_sep.setFrameShadow(QFrame.Sunken)
        self._chk_sep.setFixedHeight(18)
        for w in (QLabel("第"), self.sp_chk_start, QLabel("字节"),
                  self.cmb_chk_place, self._chk_sep, QLabel("加校验:"),
                  self.cmb_checksum, self.edit_checksum_view):
            p9r2.addWidget(w)
        p9r2.addStretch(1)
        p9.addLayout(p9r2)
        p9.addStretch(1)        # 同上：校验行贴顶
        ob.addStretch(1)        # 左右两组撑开：接收选项靠左、发送选项贴右（V0.0.3 观感）
        ob.addLayout(p9)

        # 多行发送编辑框（原 Edit_Data 550x56，右侧留白对齐原版）
        self.send_edit = QPlainTextEdit()
        self.send_edit.setPlaceholderText("发送内容（HEX 格式如: 01 02 FF），Ctrl+Enter 发送")
        self.send_edit.setMinimumHeight(90)   # 加高输入框（吸收底部面板剩余高度）
        g.addWidget(self.send_edit, 1, 1, 1, 3)   # 跨 1~3 列：多余宽度全归编辑框，右缘贴窗口边（V0.0.3 观感）
        # 选项区控件清单：[0] 是发送输入框（保持可伸缩），其余在启动沉降时
        # 钉住自然宽度——缩窗时随 opt_box 整体被窗口边缘截断而不压缩
        self._opt_widgets: list = [self.send_edit]
        for _lay in (p8r1, p8r2, p9r1, p9r2):
            for _i in range(_lay.count()):
                _w = _lay.itemAt(_i).widget()
                if _w is not None:
                    self._opt_widgets.append(_w)

        g.setColumnStretch(0, 0)
        g.setColumnStretch(1, 1)
        g.setColumnStretch(2, 0)
        g.setColumnStretch(3, 1)      # 发送编辑框跨到本列（行 1）：右缘贴窗口边
        # 数字输入框：隐藏上下按钮，宽度限制在 5 位数字左右。
        # IME 数字模式：数字直上屏、防中文输入法候选窗拦截（已在真实桌面验证不影响键盘输入）。
        for sb in (self.sp_split_ms, self.sp_timed, self.sp_chk_start):
            sb.setStyleSheet(_HIDE_SPIN_BTNS_QSS)
            sb.setMaximumWidth(62)
            sb.setInputMethodHints(Qt.ImhDigitsOnly)
        self.cmb_baud.setInputMethodHints(Qt.ImhDigitsOnly)
        self.cmb_baud.lineEdit().setInputMethodHints(Qt.ImhDigitsOnly)
        root.addWidget(panel1)

        # ---------- 状态栏（对齐 SSCOM：事件通知 | TX速率 | RX速率 | 连接 | 版本） ----------
        def _seg(lbl):
            lbl.setObjectName("sbSeg")            # QSS: 每个模块带清晰边框（对齐 SSCOM）
            lbl.setFrameStyle(QFrame.StyledPanel | QFrame.Sunken)
            lbl.setMinimumWidth(96)
            return lbl

        class _StateLabel(QLabel):
            def setText(self, t):     # 事件框定宽：长消息以悬浮提示查看全文
                super().setText(t)
                super().setToolTip(t)

        self.sb_state = _seg(_StateLabel("未连接"))   # 最左：事件通知（定宽 1/3）
        self.sb_state.setFixedWidth(75)
        self.sb_tx = _seg(QLabel("TX: 0 (0 B/s)"))
        self.sb_rx = _seg(QLabel("RX: 0 (0 B/s)"))
        self.sb_link = _seg(QLabel(""))
        self._sb_link_base = ""                 # 第4框常驻内容（串口/网络连接信息）
        self._sb_notice = QTimer(self)          # 第4框临时通知：超时回显常驻内容
        self._sb_notice.setSingleShot(True)
        self._sb_notice.timeout.connect(
            lambda: self.sb_link.setText(self._sb_link_base))
        bar = self.statusBar()
        for w in (self.sb_state, self.sb_tx, self.sb_rx, self.sb_link):
            bar.addWidget(w)
        bar.addPermanentWidget(QLabel(APP_VERSION))   # 右下角版本号
        bar.customContextMenuRequested.connect(lambda p: self._reset_counters())
        bar.setContextMenuPolicy(Qt.CustomContextMenu)
        self._ui_ready = True             # 窄窗切换等 resize 处理的前置条件

    def _build_menus(self):
        mb = self.menuBar()

        m_port = mb.addMenu("通讯端口")
        self.act_refresh = m_port.addAction("刷新串口列表")
        self.act_refresh.triggered.connect(lambda: self._refresh_ports(
            select=self.cmb_port.currentData()))
        m_port.addSeparator()
        from PySide2.QtWidgets import QActionGroup
        grp = QActionGroup(self)
        self.net_actions = {}
        for name, mode in LINK_MODES:
            act = m_port.addAction(name)
            act.setCheckable(True)
            act.setActionGroup(grp)
            # PySide2 的 triggered 不传参（PySide6 传 checked）：首参必须带默认值
            act.triggered.connect(lambda _=False, m=mode: self._set_mode(m))
            self.net_actions[mode] = act

        m_serial = mb.addMenu("串口设置")
        self.act_auto_reconnect = m_serial.addAction("断线自动重连(串口/网络)")
        self.act_auto_reconnect.setCheckable(True)
        self.act_open_start = m_serial.addAction("启动立刻打开串口")
        self.act_open_start.setCheckable(True)
        m_serial.addSeparator()                       # 对齐 SSCOM：帮助独立成条
        m_serial.addAction("帮助").triggered.connect(self._serial_help)

        m_enc = mb.addMenu("编码")
        self.enc_actions = {}
        enc_grp = QActionGroup(m_enc)
        enc_grp.setExclusive(True)
        for name, enc in ENCODINGS:
            act = enc_grp.addAction(name)
            act.setCheckable(True)
            act.triggered.connect(lambda _=False, e=enc: self._apply_encoding(e))
            m_enc.addAction(act)
            self.enc_actions[enc] = act
        act_custom = m_enc.addAction("自定义编码…")
        act_custom.triggered.connect(self._custom_encoding)

        m_view = mb.addMenu("显示")
        self.act_send_font = m_view.addAction("发送字体…")
        self.act_recv_font = m_view.addAction("接收字体…")
        self.act_bg = m_view.addAction("背景颜色…")
        m_buf = m_view.addMenu("显示缓冲上限")
        m_view.setProperty("submenu", True)   # 含子菜单：QSS 恢复 24px 右缓冲防 ▶ 压字
        # ⚠ 勿在菜单上调用 QAction.menu()：其包装器被 GC 会连坐删掉 C++ 子菜单
        # （真机复现：findChildren 后三个子菜单全灭）。子菜单含属性行在此显式标。
        self.buf_actions = {}
        for name, size in BUF_SIZES:
            act = m_buf.addAction(name)
            act.setCheckable(True)
            act.triggered.connect(lambda _=False, s=size: self._set_buffer_cap(s))
            self.buf_actions[size] = act
        m_view.addSeparator()
        self.act_theme = m_view.addAction("深色模式")
        self.act_theme.setCheckable(True)
        act_find = m_view.addAction("查找")
        act_find.setShortcut(QKeySequence.Find)
        act_find.triggered.connect(self._activate_find)

        m_send = mb.addMenu("发送")
        m_term = m_send.addMenu("终端仿真设置")       # 对齐 SSCOM 子菜单
        self.act_term_enter = m_term.addAction("回车后发送新行(带回显)")
        self.act_term_enter.setCheckable(True)
        self.act_term_key = m_term.addAction("按键立即发送该键值")
        self.act_term_key.setCheckable(True)
        self.act_term_echo = m_term.addAction("本地回显按键值")
        self.act_term_echo.setCheckable(True)
        self.act_term_echo.setToolTip("仅与【按键立即发送该键值】方式搭配使用")
        m_term.addSeparator()
        self.act_term_lf = m_term.addAction("发送回车0x0d后,自动加上换行符0x0a")
        self.act_term_lf.setCheckable(True)
        # 两种发送方式互斥：勾选一个自动取消另一个（对齐 SSCOM，仅用户点击时触发）
        for _act in (self.act_term_enter, self.act_term_key):
            _act.triggered.connect(partial(self._on_term_mode, _act))
        m_filedelay = m_send.addMenu("发送文件延时设置")   # 对齐 SSCOM 子菜单
        self.act_file_delay_group = QActionGroup(self)
        self.act_file_delay_group.setExclusive(True)
        self.act_file_delays = {}
        for _lbl, _ms in (("连续发送", 0),
                          ("每次发送256字节延时1ms", 1),
                          ("每次发送256字节延时10ms", 10),
                          ("每次发送256字节延时50ms", 50),
                          ("每次发送256字节延时100ms", 100)):
            act = m_filedelay.addAction(_lbl)
            act.setCheckable(True)
            act.setData(_ms)
            self.act_file_delay_group.addAction(act)
            self.act_file_delays[_ms] = act
        self.act_file_delays[0].setChecked(True)     # 默认连续发送
        m_send.setProperty("submenu", True)   # 含两个子菜单：同上，恢复右缓冲
        # PYCOM-OK-TRIGGERED-ARG：QActionGroup.triggered 只有"带 action"一个签名，
        # PySide2 下必传参（与 QAction.triggered 不同），所以这里可以要求必填首参
        self.act_file_delay_group.triggered.connect(
            lambda act: (self.cfg.d.__setitem__("file_delay_ms", act.data()),
                         self._notify(
                             "发送文件延时: %s" % act.text(), 2500)))
        m_send.addSeparator()
        m_send.addAction("帮助").triggered.connect(self._term_help)

        m_tool = mb.addMenu("小工具")
        # 上组：扩展区页签直达；下组：外部工具
        m_tool.addAction("进制转换").triggered.connect(
            lambda: self._show_ext_tab(1))
        m_tool.addAction("ASCII码表").triggered.connect(
            lambda: self._show_ext_tab(2))
        m_tool.addAction("多条字符串发送").triggered.connect(
            lambda: self._show_ext_tab(0))
        m_tool.addSeparator()
        m_tool.addAction("计算器").triggered.connect(self._open_calc)
        m_tool.addAction("设备管理器").triggered.connect(self._open_devmgr)
        m_tool.addAction("文件资源管理器").triggered.connect(self._open_explorer)

        m_help = mb.addMenu("帮助")
        self.act_check_update = m_help.addAction("检查更新…")
        self.act_check_update.triggered.connect(self._open_release_page)
        m_help.addAction("关于 PyCom32").triggered.connect(self._about)

        # 快捷键（Ctrl+F 由显示菜单的"查找"action 绑定，避免重复触发）
        QShortcut(QKeySequence("Ctrl+L"), self, self.rxview.clear_all)
        QShortcut(QKeySequence("Ctrl+Enter"), self, self._send_main)

        # 勾选标记 = 行尾实心圆 `●`（2026-10-16 终版定稿）：真机 Win7 分数
        # 缩放（125%/150%）下一切勾号图形都不可用——原生 indicator 勾号发虚、
        # 自绘位图（路径/✓字符）缺笔或发虚、条目垫图标还会把弹出菜单高度算短
        # （底边被裁）、行首前置标记（几何字符/全角字符）真机字宽下无法对齐。
        # `●` 放行尾：条目前缀完全不动、汉字天然对齐；列对齐用 Qt 菜单自带的
        # 制表符右对齐机制（`文字\t标记`，快捷键提示即此布局）——所有标记由
        # Qt 排到同一右边界，天然成列。标记前垫全角空格（间距 ≥ 两个空格）。
        # 带 QAction 快捷键的条目（如 查找=Ctrl+F）不勾选，互不影响。
        # 注意：菜单对象必须从 findChildren 拿——PySide2 里 QAction.menu() 返回的
        # 包装器一旦被 GC 就会连 C++ 菜单一起删掉（踩过），不能用来遍历菜单树。
        self._menu_list = self.findChildren(QMenu)
        for _m in self._menu_list:
            _m.aboutToShow.connect(partial(self._refresh_menu_markers, _m))

    # ============================================================ 菜单勾选标记

    _MARK_TAB = "\t\u3000●"              # 行尾标记：tab 右对齐 + 全角空格垫距

    def _refresh_menu_markers(self, menu=None):
        """按勾选态写行尾标记（菜单弹出前刷新，幂等）：勾选条目追加
        `\t\u3000●`（Qt 把 tab 后内容右对齐到统一列），其余条目去掉标记。
        以 tab 前的文本为基准，重复调用幂等；带快捷键的条目不勾选、互不影响。
        menu=None（全刷）时重取 findChildren 兜底——indicator 已归零，行尾
        标记是勾选态唯一可见表达，后建/懒加载的菜单漏注册会静默丢标记。"""
        for m in ([menu] if menu is not None else self.findChildren(QMenu)):
            for a in m.actions():
                base = a.text().split("\t")[0]
                if a.isCheckable() and a.isChecked():
                    if "\t" not in a.text():
                        a.setText(base + self._MARK_TAB)
                elif "\t" in a.text():
                    a.setText(base)

    def showEvent(self, ev):
        super().showEvent(ev)
        # setWindowFlags（最前/取消最前）会重建原生窗口，标题栏暗色需重新应用
        self._apply_titlebar(self.act_theme.isChecked())
        # 首次显示后按真实外框再钳制一次（resize 是客户区尺寸，标题栏占高）
        if getattr(self, "_geo_clamped", False):
            return
        self._geo_clamped = True
        scr = self.screen().availableGeometry()
        fg = self.frameGeometry()
        if fg.width() > scr.width() or fg.height() > scr.height():
            self.resize(self.width() - max(0, fg.width() - scr.width()),
                        self.height() - max(0, fg.height() - scr.height()))
        # 底部面板定高（由左侧连接区决定）：发送框底部与"发送"按钮底边对齐，
        # 窗口缩放只影响上方接收区，与原版 SSCOM 行为一致。
        # 注意不能取 panel1.sizeHint()——发送框自身的 hint 会把面板撑高。
        # 高度取 sizeHint 与 minimumSizeHint 的较大者：btn_link/btn_send 设了
        # setMinimumHeight，其真实占用高于 sizeHint，低估会造成按钮与输入框重叠。
        def _need_h(w):
            return max(w.sizeHint().height(), w.minimumSizeHint().height())

        h = (_need_h(self.serial_box) + max(34, self.btn_send.sizeHint().height())
             + self.panel1.layout().verticalSpacing() + 8)   # 行间距 + 边距余量
        self.panel1.setFixedHeight(max(h, 150))

    # ============================================================ 设置应用
    def _apply_settings(self):
        d = self.cfg.d
        # 窗口尺寸钳制到屏幕可用区域，防止保存的大尺寸把底部面板顶出屏幕
        # （显示前的 frameGeometry 是估计值，精确外框修正见 showEvent）
        scr = QApplication.primaryScreen().availableGeometry()
        self.resize(min(d["win_w"], scr.width()), min(d["win_h"], scr.height()))
        self._set_mode(d["mode"], quiet=True)
        self._refresh_ports(select=d["port"])
        self._select_combo(self.cmb_baud, str(d["baud"]))
        # QComboBox 更新文本时会用自身 hints 重置内部输入框，此处重新声明数字模式
        self.cmb_baud.setInputMethodHints(Qt.ImhDigitsOnly)
        self.cmb_baud.lineEdit().setInputMethodHints(Qt.ImhDigitsOnly)
        # 可编辑下拉设置文本会重置内部输入框的 IME 提示，此处重新声明数字模式
        self.cb_dtr.setChecked(d["dtr"])
        self.cb_rts.setChecked(d["rts"])
        self._select_combo(self.cmb_data, str(d["databits"]))
        self._select_combo(self.cmb_stop, "%g" % d["stopbits"])
        _pi = self.cmb_parity.findData(d["parity"])
        self.cmb_parity.setCurrentIndex(_pi if _pi >= 0 else 0)
        _fi = self.cmb_flow.findData(d["flow"])
        self.cmb_flow.setCurrentIndex(_fi if _fi >= 0 else 0)
        self.act_auto_reconnect.setChecked(d["auto_reconnect"])
        self.act_open_start.setChecked(d["open_on_start"])
        self.edit_remote_ip.setText(d["remote_ip"])
        self.edit_remote_port.setValue(d["remote_port"])
        self.edit_local_port.setValue(d["local_port"])
        self._refresh_nics(select_ip=d.get("local_ip"))
        fd = d.get("file_delay_ms", 0)
        if fd not in self.act_file_delays:
            fd = 0
        self.act_file_delays[fd].setChecked(True)    # 延时档位菜单（不触发 triggered）

        self.cb_hex_show.setChecked(d["hex_mode"])
        self.cb_auto_scroll.setChecked(d.get("auto_scroll", True))
        # toggled→RxView 的连接在 _wire() 里才建立，这里需显式同步初始状态
        self.rxview.set_auto_scroll(bool(d.get("auto_scroll", True)))
        # 复制/保存是否带出时间戳与收发标识（接收区右键切换，与显示设置独立）
        self.rxview.copy_with_anno = bool(d.get("copy_with_anno", True))
        self.rxview.save_with_anno = bool(d.get("save_with_anno", True))
        self.cb_timestamp.setChecked(d["split_show"])
        self.sp_split_ms.setValue(d["split_ms"])
        self._encoding = d["encoding"]
        for _e, _act in self.enc_actions.items():
            _act.setChecked(_e == self._encoding)
        self._update_buf_actions()

        self.cb_send_hex.setChecked(d["send_hex"])
        self.send_edit.setPlainText(d["main_text"])
        self.cb_newline.setChecked(d["send_newline"])
        self.sp_timed.setValue(d["send_interval_ms"])
        _ck_idx = self.cmb_checksum.findData(d["checksum_kind"])
        self.cmb_checksum.setCurrentIndex(_ck_idx if _ck_idx >= 0 else 0)
        self.sp_chk_start.setValue(d["checksum_start"])
        _ci = self.cmb_chk_place.findData(d["checksum_tail"])
        self.cmb_chk_place.setCurrentIndex(_ci if _ci >= 0 else 0)

        self.act_term_enter.setChecked(d["term_enter_send"])
        self.act_term_key.setChecked(d["term_key_send"])
        self.act_term_echo.setChecked(d["term_key_echo"])
        self.act_term_lf.setChecked(d["term_0d_add_0a"])
        self._sync_term_echo()
        self.act_theme.setChecked(d["theme"] == "dark")
        # 启动默认收起扩展面板（用户要求，不记忆上会话的展开状态）
        self.btn_expand.setChecked(False)
        self._toggle_expand(False)

        self.cb_ms_loop.setChecked(d["multisend_loop"])
        self._update_checksum_view()      # 启动即显示校验值预览（信号尚未连接时补齐）
        self._apply_theme()
        self._apply_text_fonts()

    def _apply_theme(self):
        name = "dark" if self.act_theme.isChecked() else "light"
        self._apply_colorscheme(name)
        self._apply_palette(name)
        QApplication.instance().setStyleSheet(theme.qss(name))
        self._apply_titlebar(name == "dark")      # Windows 标题栏跟随深色
        self.rxview.apply_theme(
            theme.colors(name),
            bg=None if name == "dark" else self.cfg.d["bg_color"],
            fg=None if name == "dark" else self.cfg.d["fg_color"])
        self._apply_ts_color()

    def _apply_colorscheme(self, name):
        """告知 Qt 当前明暗（Qt 6.5+ 才有 setColorScheme；Qt5 无此 API，
        明暗完全由自身的 QSS+调色板+DWM 标题栏控制，此处为空操作）。"""
        try:
            QGuiApplication.styleHints().setColorScheme(
                Qt.ColorScheme.Dark if name == "dark" else Qt.ColorScheme.Light)
        except AttributeError:
            pass

    def _apply_palette(self, name):
        """原生绘制部分（面板立体边框、状态栏分隔框、勾选框指示器）跟随主题：
        深色用显式深色调色板；浅色恢复系统标准色板。"""
        app = QApplication.instance()
        pal = theme.palette(name)
        app.setPalette(pal if pal is not None else app.style().standardPalette())

    def _apply_titlebar(self, dark: bool):
        """Windows 标题栏跟随深色主题（DWM 沉浸式暗色）；其它平台无操作。"""
        if sys.platform != "win32" or QApplication.platformName() != "windows":
            return
        try:
            import ctypes
            value = ctypes.c_int(1 if dark else 0)
            hwnd = int(self.winId())
            for attr in (20, 19):     # Win11 及 Win10 20H1+ / Win10 1809~1909
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        ctypes.c_void_p(hwnd), attr, ctypes.byref(value),
                        ctypes.sizeof(value)) == 0:
                    break
        except (OSError, AttributeError, ValueError):
            pass

    def _dark_titlebar_widget(self, w):
        """单个顶层窗口的标题栏跟随深色（DWM 沉浸式暗色）。Qt5 没有
        Qt6.5+ 的 styleHints().setColorScheme(Dark)（主线所有窗口的标题栏
        靠它自动跟随），只能对每个顶层窗口单独做 DWM 置暗；Win10 1809+
        生效，Win7 无此 DWM 属性、调用自动忽略（标题栏保持系统浅色）。"""
        if sys.platform != "win32" or QApplication.platformName() != "windows":
            return
        try:
            import ctypes
            value = ctypes.c_int(1 if self._theme_name() == "dark" else 0)
            hwnd = int(w.winId())     # 触发原生窗口创建，才能拿到句柄
            for attr in (20, 19):     # Win11 及 Win10 20H1+ / Win10 1809~1909
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        ctypes.c_void_p(hwnd), attr, ctypes.byref(value),
                        ctypes.sizeof(value)) == 0:
                    break
        except (OSError, AttributeError, ValueError):
            pass

    def _theme_name(self) -> str:
        return "dark" if self.act_theme.isChecked() else "light"

    def _pick_ts_color(self):
        from PySide2.QtGui import QColor
        from PySide2.QtWidgets import QColorDialog
        init = QColor(self.cfg.d["ts_color"]) if self.cfg.d["ts_color"] \
            else theme.colors(self._theme_name())["ts"]
        c = QColorDialog.getColor(init, self, "时间戳颜色")
        if c.isValid():
            self.cfg.d["ts_color"] = c.name()
            self._apply_ts_color()

    def _apply_ts_color(self):
        """时间戳颜色：配置值优先，空则随主题默认；色块同步显示。"""
        custom = self.cfg.d["ts_color"] or None
        self.rxview.set_ts_color(custom)
        shown = custom or theme.ROLES[self._theme_name()]["ts"]
        self.btn_ts_color.setStyleSheet("background:%s; border:1px solid #888;" % shown)

    # ============================================================ 信号连接
    def _wire(self):
        self.btn_refresh.clicked.connect(lambda: self._refresh_ports(select=self.cmb_port.currentData()))
        self.btn_link.clicked.connect(self._toggle_link)
        self.btn_net_connect.clicked.connect(self._open_link)
        self.btn_net_disc.clicked.connect(self._close_link)
        self.btn_nic_refresh.clicked.connect(lambda: self._refresh_nics(
            select_ip=self.cmb_local_ip.currentText()))
        self.cmb_nic.currentIndexChanged.connect(self._on_nic_selected)
        self.rxview.pre_clear_hook = self._flush_log_before_clear
        self.rxview.save_bin_hook = self._save_rx     # 右键保存原始字节走统一落盘逻辑
        self.btn_clear.clicked.connect(self._clear_display)
        self.btn_find.clicked.connect(self.find_bar.activate)
        self.btn_new_window.clicked.connect(self._open_new_window)
        self.btn_clear_send.clicked.connect(self.send_edit.clear)
        self.btn_save.clicked.connect(lambda: self._save_rx())
        self.cb_rxfile.toggled.connect(self._toggle_rxfile)

        self.btn_send.clicked.connect(self._send_main)
        self.cb_timed.toggled.connect(self._toggle_timed)
        self.sp_timed.valueChanged.connect(self._toggle_timed)
        self.btn_open_file.clicked.connect(self._pick_file)
        self.edit_file.editingFinished.connect(lambda: self.cfg.d.__setitem__("_last_file", self.edit_file.text()))
        self.btn_send_file.clicked.connect(self._send_file)
        self.btn_stop_file.clicked.connect(self._stop_file)
        self.btn_keep_top.toggled.connect(self._keep_top)
        self.btn_collapse.toggled.connect(self._toggle_collapse)
        self.btn_save_params.clicked.connect(lambda: self._save_settings())
        self.btn_expand.toggled.connect(self._toggle_expand)

        self.cb_hex_show.toggled.connect(self._on_hex_show_toggled)
        self.cb_auto_scroll.toggled.connect(self.rxview.set_auto_scroll)
        # 用户回看（滚轮上翻/拖选/拖滚动条）→ 自动取消勾选（跟随停住，可安心
        # 选择复制）；重新勾选由 set_auto_scroll(True) 立即跳回底部
        self.rxview.pause_auto_scroll.connect(
            lambda: self.cb_auto_scroll.setChecked(False))
        self.cb_timestamp.toggled.connect(self._set_split)
        self.sp_split_ms.valueChanged.connect(self._set_split)
        self.cb_break.toggled.connect(self._set_break)
        self.btn_ts_color.clicked.connect(self._pick_ts_color)

        # 校验值实时显示联动
        self.cmb_checksum.currentIndexChanged.connect(self._update_checksum_view)
        self.sp_chk_start.valueChanged.connect(self._update_checksum_view)
        self.cmb_chk_place.currentIndexChanged.connect(self._update_checksum_view)
        self.send_edit.textChanged.connect(self._update_checksum_view)
        self.cb_send_hex.toggled.connect(self._update_checksum_view)
        self.cb_newline.toggled.connect(self._update_checksum_view)

        # 线路引脚轮询（DCD/DSR/CTS/RI）与收发速率刷新
        self.pin_timer = QTimer(self)
        self.pin_timer.timeout.connect(self._poll_pins)
        self.pin_timer.start(300)
        self.speed_timer = QTimer(self)
        self.speed_timer.timeout.connect(self._update_speed)
        self.speed_timer.start(250)

        self.btn_ms_add.clicked.connect(self.ms_model.add_row)
        self.btn_ms_del.clicked.connect(self._ms_del)
        self.btn_ms_help.clicked.connect(self._ms_help)
        self.btn_ms_import.clicked.connect(self._import_ms_ini)
        self.cb_ms_loop.toggled.connect(self._toggle_ms_loop)

        self.act_theme.toggled.connect(self._apply_theme)
        self.act_send_font.triggered.connect(lambda: self._pick_text_font("send"))
        self.act_recv_font.triggered.connect(lambda: self._pick_text_font("recv"))
        self.act_bg.triggered.connect(self._pick_bg)
        self.act_auto_reconnect.toggled.connect(
            lambda on: (self.cfg.d.__setitem__("auto_reconnect", on),
                        setattr(self.link, "auto_reconnect", on) if self.link else None))
        self.act_open_start.toggled.connect(
            lambda on: self.cfg.d.__setitem__("open_on_start", on))

        # 终端仿真：接收区可聚焦、可输入（含输入法），对齐 SSCOM 的接收窗内输入。
        # 注意 Qt 的 QAbstractScrollArea 会把"点击 viewport"的键盘焦点交给
        # 外框 RxView 本身（viewport 的焦点代理就是外框），实际按键事件落在
        # RxView 上，因此两个对象都要装过滤器并开输入法属性，缺一收不到按键。
        for _w in (self.rxview, self.rxview.viewport()):
            _w.installEventFilter(self)
            _w.setFocusPolicy(Qt.StrongFocus)
            _w.setAttribute(Qt.WA_InputMethodEnabled, True)
        self.rxview.viewport().setCursor(Qt.IBeamCursor)   # 文本输入区视觉提示

        self.timed_timer = QTimer(self)
        self.timed_timer.timeout.connect(self._send_main)
        self._log_timer = QTimer(self)        # 显示内容 -> txt 日志 增量落盘
        self._log_timer.setInterval(100)
        self._log_timer.timeout.connect(self._rx_log_poll)
        self._tx_timer = QTimer(self)         # 部分写入余量续发
        self._tx_timer.setInterval(100)
        self._tx_timer.timeout.connect(self._flush_tx_pend)
        self._tx_timer.start(100)
        self.rxview.cleared.connect(self._on_display_cleared)
        self.ms_loop = MultiSendLoop(lambda: self.ms_model.rows, self)
        self.ms_loop.send_row.connect(self._ms_send_row)
        self.ms_loop.stopped.connect(self._on_ms_loop_stopped)
        # 配置为循环发送时在此启动（_apply_settings 先于 _wire，无法触发 toggled）
        if self.cb_ms_loop.isChecked():
            self._toggle_ms_loop(True)
        # 单击"点击发送"列 → 立即发送该行；双击 → 视图原生打开注释编辑器
        self.ms_table.clicked.connect(self._on_ms_clicked)
        self.file_sender = FileSender(None, self)
        self.file_sender.progress.connect(self._file_progress)
        self.file_sender.finished.connect(self._file_done)
        self.file_sender.failed.connect(self._file_failed)
        # 全局过滤器（应用级单例 + 弱引用当前窗口，见 _DialogDarkFilter 类注释）：
        # 顶层对话框显示时标题栏跟随深色
        app = QApplication.instance()
        f = getattr(app, "_dlg_dark_filter", None)
        if f is None:
            f = _DialogDarkFilter(app)
            app._dlg_dark_filter = f
            app.installEventFilter(f)
        f._win = weakref.ref(self)

    def eventFilter(self, obj, ev):
        if obj in (self.rxview, self.rxview.viewport()):
            # 终端仿真（对齐 SSCOM）：光标放在接收显示窗口里直接输入指令。
            # 点击接收区后焦点可能在外框 RxView 上（见 _wire 注释），两个对象
            # 的按键都按同一套逻辑处理。
            if ev.type() == ev.Type.KeyPress:
                # 接收区有选区时 Ctrl+C / Ctrl+Insert = 复制所选：透传给
                # 接收区处理，不作终端输入（终端仿真开启时 Ctrl+C 本会发 0x03）
                if (ev.modifiers() & Qt.ControlModifier
                        and ev.key() in (Qt.Key_C, Qt.Key_Insert)
                        and self.rxview.has_selection()):
                    return False
                if self.act_term_key.isChecked():
                    # 【按键立即发送键值】按键即发；record=False：显示由
                    # "本地回显"勾选单独控制，避免链路打开时显示两遍
                    data = key_to_bytes(ev, self._current_encoding())
                    if data:
                        data = cr_add_lf(data, self.act_term_lf.isChecked())
                        self._write(data, record=False)
                        if self.act_term_echo.isChecked():
                            self.builder.feed("tx", data, time.time(),
                                              self.buf.end_abs)
                        return True
                    return False
                if self.act_term_enter.isChecked():
                    # 【回车后发送行】输入即回显到接收区末行，回车发送最后一行+回车
                    if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
                        line = b"".join(self._term_line_raw)
                        self._term_line.clear()
                        self._term_line_raw.clear()
                        self._write(cr_add_lf(line + b"\x0d",
                                              self.act_term_lf.isChecked()),
                                    record=False)      # 回显已在逐字输入时完成
                        self.builder.feed("tx", b"\n", time.time(),
                                          self.buf.end_abs)   # 结束回显行
                        return True
                    if ev.key() == Qt.Key_Backspace and self._term_line:
                        self._term_line.pop()          # 按字符单元弹出（中文不残留半字节）
                        self._term_line_raw.pop()
                        self.builder.feed("tx", b"\x08 \x08", time.time(),
                                          self.buf.end_abs)
                        return True
                    t = ev.text()
                    if t and t.isprintable():
                        self._term_line.extend(t)
                        b = self._term_encode(t)
                        self._term_line_raw.append(b)
                        self.builder.feed("tx", b, time.time(), self.buf.end_abs)
                        return True
            elif ev.type() == ev.Type.InputMethod \
                    and self.act_term_enter.isChecked():
                # 中文输入法提交（接收区已启用 IME）：逐字符入列，
                # 保证退格粒度与编码字节列表一一对应
                cs = ev.commitString()
                for ch in cs:
                    self._term_line.append(ch)
                    b = self._term_encode(ch)
                    self._term_line_raw.append(b)
                    self.builder.feed("tx", b, time.time(), self.buf.end_abs)
                return True
        return super().eventFilter(obj, ev)

    def _term_encode(self, s: str) -> bytes:
        """终端输入字符的发送编码；回显与发送使用同一份字节，杜绝二者错位。"""
        try:
            return s.encode(self._current_encoding())
        except (LookupError, UnicodeEncodeError):
            return s.encode("gbk", "replace")

    def _on_term_mode(self, act, _checked=False):
        """SSCOM 语义：两种发送方式互斥，选中一个自动取消另一个。"""
        if act.isChecked():
            other = (self.act_term_key if act is self.act_term_enter
                     else self.act_term_enter)
            other.setChecked(False)
        self._sync_term_echo()

    def _sync_term_echo(self):
        """本地回显按键值只与【按键立即发送该键值】搭配：其余方式下置灰。"""
        self.act_term_echo.setEnabled(self.act_term_key.isChecked())

    def _startup(self):
        # 端口列表已在 _apply_settings 里枚举过（慢驱动机器上枚举可达数秒，
        # 启动路径不重复做，见 启动性能分析_20261021）；此处只做列宽校正——
        # 列宽以串口配置各行实际需要的宽度为准（取"线路"行与整箱 sizeHint
        # 的较大值）：波特率/数据位等是固定宽控件，字体度量偏大时行宽会超
        # 过"线路"行，此时跟随较大值——保证行与行之间绝不重叠；
        # 三个网络页与串口页同宽对齐
        line_w = (self.lbl_line.sizeHint().width()
                  + sum(self.pin_leds[nm].width() for nm in
                        ("DCD", "DSR", "CTS", "RI", "ERR"))
                  + self.cb_break.sizeHint().width()
                  + 6 * 3 + 2)              # 行内 6 个间距 + 边缘余量
        w = max(line_w, self.serial_box.sizeHint().width())
        self.serial_box.setFixedWidth(w)
        self.net_box.setFixedWidth(w)
        self.btn_send.setFixedWidth(w)
        # 文件块（打开文件/路径/发送文件/停止/清发送区）右移：起始横坐标
        # 对齐左下角"发送"按钮的右缘（用户要求）。字体度量异步沉降（启动早期
        # 偏小），分多拍重算（每拍整体重设、非累积），沉降后自动收敛
        for d in (0, 300, 1000, 2500):
            QTimer.singleShot(d, self._update_file_pad)
        # 菜单"启动立刻打开串口"勾选时自动打开（默认不勾选）
        if self.cfg.d["open_on_start"]:
            self._open_link()

    # ============================================================ 模式
    def _set_mode(self, mode, quiet=False):
        self._mode = mode
        for m, act in self.net_actions.items():
            act.setChecked(m == mode)
        serial = mode == "serial"
        self.serial_box.setVisible(serial)
        self.net_box.setVisible(not serial)
        self.btn_link.setText({"serial": "打开串口"}.get(mode, "打开"))
        # 网络模式：远程行的连接/侦听按钮文字（对齐 SSCOM）
        self.btn_net_connect.setText({"tcp_client": "连接", "tcp_server": "侦听",
                                      "udp": "连接"}.get(mode, "连接"))
        # 变色球旁显示当前网络模式
        self.lbl_net_mode.setText({"tcp_client": "TCP客户端",
                                   "tcp_server": "TCP服务器",
                                   "udp": "UDP"}.get(mode, ""))
        # 切换模式必须关闭旧连接，否则旧连接继续收发且状态错乱
        if self.link:
            self._close_link()
        self._on_state("closed") if not quiet else None
        self._sync_net_locks()

    def _sync_net_locks(self):
        """按模式锁死与本模式无关的输入框并给原因提示（2026-10-20 用户定稿）：
        TCP 客户端不使用本机端口；TCP 服务器不使用远程 IP/端口；UDP 四项全有效。"""
        remote_hint = ("远程主机：IPv4/IPv6 地址或主机名（主机名仅 TCP 客户端支持，"
                       "UDP 只认地址字面量，如 ::1、fe80::x%接口）")
        server_lock = "TCP 服务器无需配置远程IP和端口"
        m = self._mode
        client = m == "tcp_client"
        self.edit_remote_ip.setEnabled(m != "tcp_server")
        self.edit_remote_port.setEnabled(m != "tcp_server")
        self.edit_local_port.setEnabled(not client)
        self.edit_remote_ip.setToolTip(remote_hint if m != "tcp_server" else server_lock)
        self.edit_remote_port.setToolTip("" if m != "tcp_server" else server_lock)
        self.edit_local_port.setToolTip(
            "" if not client else "TCP 客户端无需配置本机端口")

    def _toggle_collapse(self, on):
        """收起/展开底部串口参数面板（对齐 SSCOM 的面板折叠）：收起时只留接收区、
        文件行与状态栏；串口连接状态与收发不受影响。"""
        self.panel1.setVisible(not on)
        self.btn_collapse.setText("展开" if on else "收起")

    def _toggle_expand(self, on):
        w = self.splitter.widget(1)
        w.setVisible(on and not getattr(self, "_narrow", False))
        self.btn_expand.setText("扩展" if not on else "收起")
        if on:
            # QSplitter 重新显示部件时不会沿用 setSizes 的比例，需显式按 62:38 分配
            total = max(1, self.splitter.width())
            self.splitter.setSizes([int(total * 0.62), int(total * 0.38)])

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._apply_narrow(self.width() < _NARROW_W)
        self._apply_file_pad()

    def _apply_narrow(self, on: bool):
        """窄窗（对齐 SSCOM）：右侧控件不再隐藏——工具行右区与选项两列保持
        自然宽度、被窗口边缘截断（ClipBox 溢出）；此处只处理无法截断的浮层
        与停靠件：扩展面板收起、查找条隐藏、状态栏连接信息让位。"""
        if not getattr(self, "_ui_ready", False):
            return
        if getattr(self, "_narrow", None) == on:
            return
        self._narrow = on
        # 扩展面板：窄窗隐藏；展宽后按扩展按钮原状态恢复
        self.splitter.widget(1).setVisible(self.btn_expand.isChecked() and not on)
        self.sb_link.setVisible(not on)
        # 查找条在窄窗下会被挤到不可用宽度：窄窗隐藏，展宽后按原状态恢复
        if on:
            if self.find_bar.isVisible():
                self._find_bar_was_open = True
                self.find_bar.hide()
        elif getattr(self, "_find_bar_was_open", False):
            self._find_bar_was_open = False
            self.find_bar.show()

    def _activate_find(self):
        """查找入口（菜单/Ctrl+F）：窄窗下查找条没有可用宽度，提示先拉宽窗口。"""
        if getattr(self, "_narrow", False):
            self._notify("窗口过窄，拉宽窗口后可使用查找", 3000)
            return
        self.find_bar.activate()

    # ============================================================ 连接管理
    def _link_cfg(self) -> dict:
        d = dict(self.cfg.d)
        d.update({
            "mode": self._mode,
            "port": self.cmb_port.currentData(),
            "baud": self._current_baud(),
            "databits": int(self.cmb_data.currentText()),
            "stopbits": float(self.cmb_stop.currentText()),
            "parity": self.cmb_parity.currentData(),
            "flow": self.cmb_flow.currentData(),
            "dtr": self.cb_dtr.isChecked(),
            "rts": self.cb_rts.isChecked(),
            "remote_ip": self.edit_remote_ip.text().strip() or "127.0.0.1",
            "remote_port": self.edit_remote_port.value(),
            "local_ip": self.cmb_local_ip.currentText(),
            "local_port": self.edit_local_port.value(),
            "auto_reconnect": self.act_auto_reconnect.isChecked(),
            "reconnect_ms": self.cfg.d["reconnect_ms"],
        })
        return d

    def _make_link(self):
        from app.core.net_link import TcpClientLink, TcpServerLink, UdpLink
        from app.core.serial_link import SerialLink
        cfg = self._link_cfg()
        cls = {"serial": SerialLink, "tcp_client": TcpClientLink,
               "tcp_server": TcpServerLink, "udp": UdpLink}[cfg["mode"]]
        link = cls(cfg, self)
        link.auto_reconnect = cfg["auto_reconnect"]
        link.reconnect_ms = cfg["reconnect_ms"]
        link.rx.connect(self._on_rx)
        link.tx_done.connect(self._on_tx)
        link.state_changed.connect(self._on_state)
        return link

    def _toggle_link(self):
        if self.link and self.link.is_open():
            self._close_link()
        elif self._link_pending:
            self._close_link()        # 串口被占用/断线重连的等待期：点击=取消等待
            self._notify("已取消连接等待")
        else:
            self._open_link()

    def _open_link(self):
        self._close_link(silent=True)
        self.link = self._make_link()
        self.file_sender.link = self.link
        self._link_pending = True
        self.link.open()

    def _close_link(self, silent=False):
        self._link_pending = False
        self.file_sender.stop()           # 断开/切模式时先取消进行中的文件发送
        self._file_done()                 # stop 不触发 finished，进度条需同步清理
        self._tx_pend.clear()             # 余量只属于当前连接：断开即丢弃，避免串到新连接
        self._term_line.clear()           # 终端输入行同理，不串到新连接
        self._term_line_raw.clear()
        if self.link:
            self.link.close()
            self.link.deleteLater()
            self.link = None
            self.file_sender.link = None
            if not silent:
                self._on_state("closed")

    def _on_rx(self, data: bytes):
        # 内存耗尽降级（32 位 2GB 地址空间）：上限减半清出空间重试一次，仍失败
        # 就丢这一包——绝不把 MemoryError 抛回 Qt 槽里。
        # 追加缓冲与喂行表**分段**重试：append 一旦成功就不得重复追加（否则同一
        # 包字节会在接收缓冲里出现两份，*.bin 导出与显示都翻倍）。
        for attempt in (0, 1):
            try:
                self.buf.append(data)
                break
            except MemoryError:
                if attempt:
                    self._notify(
                        "内存不足：丢弃 %d 字节接收数据" % len(data), 5000)
                    return
                self._shrink_on_oom()
        for attempt in (0, 1):
            try:
                self.builder.feed("rx", data, time.time(),
                                  self.buf.end_abs - len(data))
                break
            except MemoryError:
                if attempt:
                    # 字节已入接收缓冲（*.bin 会导出）但未生成显示行：
                    # 措辞区分于"丢弃"，且计数照常累加，与缓冲一致
                    self._notify(
                        "内存不足：%d 字节未能显示" % len(data), 5000)
                    break
                self._shrink_on_oom()
        self._trim_lines_to_buf()              # 行表跟随缓冲：屏幕有的必导得出来
        self.find_bar.notify_data_changed()    # 新数据可能带来新命中/裁剪旧命中
        self.rx_bytes += len(data)
        # 不在此处刷新状态栏（旧实现写"RX: %d"会把速率后缀抹掉）——
        # 速率/总量统一由 _update_speed 定时渲染

    def _trim_lines_to_buf(self) -> None:
        """行表保留范围跟随接收缓冲（字节缓冲丢掉的旧字节，对应行也丢掉），
        避免"屏幕上看得到、保存 *.bin 导出不了"。打开接收转存日志时不裁尚未
        写盘的行（传日志游标），防止日志漏行。详见 display.LineBuilder.trim_before_abs。"""
        keep = self._rx_log_written if self._rx_log else None
        self.builder.set_log_keep(keep)    # 硬上限裁剪同样不得越过日志游标
        self.builder.trim_before_abs(self.buf.start_abs, keep_after_line=keep)

    def _clamp_cap(self, size) -> int:
        """把缓冲上限收敛进 32 位可用范围，并写回配置（菜单勾选态据此判断）。"""
        cap = max(_MIN_BUF, min(int(size), _MAX_BUF))
        if cap != self.cfg.d["buffer_cap"]:
            self.cfg.d["buffer_cap"] = cap
        return cap

    def _shrink_on_oom(self) -> None:
        """内存告急：缓冲上限减半并清出空间（32 位实测数据见 _MAX_BUF 注释）。"""
        cap = self._clamp_cap(max(_MIN_BUF, self.buf.cap // 2))
        self.buf.set_cap(cap)
        self._trim_lines_to_buf()      # 缓冲已丢旧字节，行表同步
        self._update_buf_actions()
        self._notify("内存不足，显示缓冲上限降为 %d 字节" % cap, 5000)

    def _on_tx(self, n: int):
        self.tx_bytes += n        # 状态栏同样交由 _update_speed 定时渲染

    @staticmethod
    def _style_pin(lbl, active, err=False):
        color = ("#FF4136" if err else "#2ECC40") if active else "#9E9E9E"
        lbl.setStyleSheet("border-radius:4px; background:%s; color:#111;" % color)

    def _poll_pins(self):
        """轮询串口输入引脚（DCD/DSR/CTS/RI），仅串口模式且已打开时有效。"""
        sp = getattr(self.link, "sp", None) if self.link else None
        sigs = None
        if sp is not None and sp.isOpen():
            try:
                sigs = sp.pinoutSignals()      # QSerialPort.PinoutSignal 标志集
            except Exception:
                sigs = None
        for nm, fl in (("DCD", QSerialPort.DataCarrierDetectSignal),
                       ("DSR", QSerialPort.DataSetReadySignal),
                       ("CTS", QSerialPort.ClearToSendSignal),
                       ("RI", QSerialPort.RingIndicatorSignal)):
            self._style_pin(self.pin_leds[nm], bool(sigs) and bool(sigs & fl))
        self._style_pin(self.pin_leds["ERR"], self._link_err, err=True)

    def _update_speed(self, now=None):
        """收发速率/总量定时渲染（250ms 一次）：速率取最近 1 秒滑窗的字节增量，
        突发后能保持满一秒才衰减归零——不再"偶尔显示"。now 供测试注入时钟。"""
        now = time.monotonic() if now is None else now
        self._speed_samples.append((now, self.tx_bytes, self.rx_bytes))
        while len(self._speed_samples) > 2 and \
                now - self._speed_samples[0][0] > 1.5:
            self._speed_samples.pop(0)
        base = self._speed_samples[0]
        dt = now - base[0]
        if dt < 0.2:
            return                       # 首拍样本太近：下一拍再算
        tx_bps = max(0, int((self.tx_bytes - base[1]) / dt))
        rx_bps = max(0, int((self.rx_bytes - base[2]) / dt))
        self.sb_tx.setText("TX: %d (%d B/s)" % (self.tx_bytes, tx_bps))
        self.sb_rx.setText("RX: %d (%d B/s)" % (self.rx_bytes, rx_bps))

    def _set_break(self, on: bool):
        sp = getattr(self.link, "sp", None) if self.link else None
        if sp is not None and sp.isOpen():
            sp.setBreakEnabled(on)

    def _set_link_base(self, text: str):
        """第4框常驻内容（串口参数/网络对端信息）。有临时通知在显示时只更新底稿，
        待通知超时回显。"""
        self._sb_link_base = text
        if not self._sb_notice.isActive():
            self.sb_link.setText(text)

    def _notify(self, msg: str, timeout: int = 3000):
        """第4框临时通知：期内有新通知则刷新，超时回显常驻连接信息
        （第1框通知栏只显示 已连接/未连接，其余提示一律走这里）。"""
        self.sb_link.setText(msg)
        self._sb_notice.start(timeout)        # 单次定时器，重复 start 即刷新

    def _open_new_window(self):
        """菜单栏最左"新窗口"：再启动一个独立的应用实例（QProcess 脱离本进程
        独立运行；同一串口/端口资源仍由系统互斥，两边开同一串口会走占用提示）。
        打包版拉起 exe 自身；源码模式带 main.py 路径。"""
        args = []
        if not getattr(sys, "frozen", False):
            args = [os.path.abspath(sys.argv[0])]   # 源码：python main.py
        if not QProcess.startDetached(sys.executable, args, base_dir()):
            self._notify("新窗口启动失败")

    def _on_state(self, state: str):
        # 通知栏（第1框）只显示 已连接/未连接；连接细节与其余提示走第4框
        # 临时通知（重连中每次尝试都会刷新，间隔小于通知超时，持续可见）
        open_txt = {"serial": "打开串口"}.get(self._mode, "打开")
        if state == "open":
            self._link_pending = False
            self._link_err = False
            self.sb_state.setText("已连接")
            self.btn_link.setText("关闭")
            led_bg = "#2ECC40"
            self._set_link_base(self.link.describe() if self.link else "")
            sp = getattr(self.link, "sp", None)
            if sp is not None:
                sp.setBreakEnabled(self.cb_break.isChecked())
        elif state == "reconnecting":
            self._link_pending = True
            self.sb_state.setText("未连接")
            self.btn_link.setText("取消")    # 占用重试/断线重连的等待期可取消
            led_bg = "#FFDC00"
            self._notify("重连中…")
        elif state == "closed":
            self._link_pending = False
            self._link_err = False
            self.sb_state.setText("未连接")
            self.btn_link.setText(open_txt)
            led_bg = "#9E9E9E"
            self._set_link_base("")
        else:
            # 打开失败。是否转「取消」由紧随的 reconnecting 状态决定（串口/
            # TCP 客户端开了自动重连才会发）；不会重试的失败（服务端侦听失败、
            # UDP 绑定失败、未开自动重连）按钮保持「打开」，直接点即再次尝试。
            # UDP 发送失败是例外：链路仍绑定、可收可发（is_open），界面保持
            # 「已连接/关闭」只亮 ERR 灯并通知原因——按钮动作与真实动作一致
            #（十六轮 F2：否则按钮写「打开」点击却执行关闭）。
            self._link_pending = False
            self._link_err = True
            still = bool(self.link and self.link.is_open())
            self.sb_state.setText("已连接" if still else "未连接")
            self.btn_link.setText("关闭" if still else open_txt)
            led_bg = "#FF4136"
            self._notify(state[7:] if state.startswith("error: ") else state)
        for lbl in (self.lbl_led, self.lbl_led2):
            lbl.setStyleSheet("border-radius:7px; background:%s;" % led_bg)

    _TX_PEND_CAP = 16 << 20   # 发送积压上限：链路长时间堵死时丢弃新数据，避免无限积累

    def _tx_pend_len(self) -> int:
        return sum(len(seg) for seg, _rec in self._tx_pend)

    def _enqueue_tx(self, data: bytes, record: bool) -> None:
        # 同语义相邻段合并，减少链路写次数
        if self._tx_pend and self._tx_pend[-1][1] == record:
            seg, _ = self._tx_pend[-1]
            self._tx_pend[-1] = (seg + data, record)
        else:
            self._tx_pend.append((data, record))

    def _write(self, data: bytes, record: bool = True) -> None:
        # 待发余量为分段队列 [(字节, 是否记录TX显示)]：每段保留自己的 record
        # 语义（终端数据已逐字回显=False，普通发送=True）。队列总量超限（含
        # 单次超大块）整体拒绝，避免积压无限增长。
        # 顺序保证：先排空旧余量，再决定当前数据是直写还是入队——当前数据
        # 绝不先入队，否则会被 flush 连同旧段一起送走后又直写一次（重复发送）
        if self._tx_pend_len() + len(data) > self._TX_PEND_CAP:
            self._notify("发送积压超限（>%dMB），本次数据已丢弃"
                                  % (self._TX_PEND_CAP >> 20))
            return
        if not self.link or not self.link.is_open():
            self._notify("未连接，无法发送")
            self._enqueue_tx(data, record)      # 整队待发，连接恢复后由定时器续发
            return
        self._flush_tx_pend()                   # 先排空旧余量（按各自语义记录）
        if self._tx_pend:                       # 旧余量未排空（链路暂满）：当前数据接队尾
            self._enqueue_tx(data, record)
            return
        n = self.link.write(data)
        if n <= 0:
            # 链路已自行上报具体原因（如 UDP 远程地址无法解析，十六轮 F3）
            # 时不再叠加通用提示，避免第 4 格的具体原因被通用文案覆盖
            if not getattr(self.link, "send_err_active", False):
                self._notify("写入失败或对端未就绪（0 字节）")
            self._enqueue_tx(data, record)
            return
        if n < len(data):                       # 部分接受：剩余接队尾待续发
            self._enqueue_tx(data[n:], record)
            self._notify("部分发送: %d/%d 字节（余量自动续发）"
                                  % (n, len(data)))
        if record:
            self.builder.feed("tx", data[:n], time.time(),
                              self.buf.end_abs - (len(data) - n))
        self.find_bar.notify_data_changed()

    def _flush_tx_pend(self):
        """把待发余量分段续发到当前连接（TX 计数由 link.tx_done 上报）。
        每段按自己的 record 语义决定是否记录 TX 显示：
        终端已逐字回显的段续发时不重复记录。"""
        sent_total = 0
        while self._tx_pend and self.link and self.link.is_open():
            seg, rec = self._tx_pend[0]
            n = self.link.write(seg)
            if n <= 0:
                break                           # 链路暂不可写，下个 tick 继续
            if rec:
                self.builder.feed("tx", seg[:n], time.time(),
                                  self.buf.end_abs - (len(seg) - n))
            sent_total += n
            if n < len(seg):                    # 部分接受：段剩余留队首
                self._tx_pend[0] = (seg[n:], rec)
                self._notify("部分发送: %d/%d 字节（余量自动续发）"
                                      % (n, len(seg)))
                break
            self._tx_pend.pop(0)
        if sent_total:
            self.find_bar.notify_data_changed()
            self._notify("余量已续发: %d 字节" % sent_total)

    # ============================================================ 发送
    def _build_payload(self, text: str, hex_mode: bool, add_newline: bool,
                       checksum: bool) -> bytes:
        from app.core.checksum import append_checksum
        from app.core.hexutil import try_parse_hex
        if hex_mode:
            data = try_parse_hex(text.replace("\n", " ")) or b""
        else:
            try:
                data = text.encode(self._current_encoding())
            except (LookupError, UnicodeEncodeError):
                data = text.encode("gbk", "replace")
        if add_newline:
            data += b"\x0D\x0A"
        if checksum:
            data = append_checksum(data, self.cmb_checksum.currentData(),
                                   self.sp_chk_start.value(),
                                   self.cmb_chk_place.currentData())
        return data

    def _send_main(self):
        data = self._build_payload(self.send_edit.toPlainText(),
                                   self.cb_send_hex.isChecked(),
                                   self.cb_newline.isChecked(),
                                   self.cmb_checksum.currentData() != "none")
        if data:
            self._write(data)

    def _toggle_timed(self):
        if self.cb_timed.isChecked():
            self.timed_timer.start(max(1, self.sp_timed.value()))
        else:
            self.timed_timer.stop()

    def _pick_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择要发送的文件", "", "所有文件 (*.*)")
        if path:
            self.edit_file.setText(path)
            self.cfg.d["_last_file"] = path

    def _send_file(self):
        if not (self.link and self.link.is_open()):
            self._notify("未连接，无法发送文件")
            return
        path = self.edit_file.text().strip() or self.cfg.d.get("_last_file", "")
        if path and os.path.exists(path):
            self.progress.setRange(0, os.path.getsize(path))
            self.progress.show()
            self.file_sender.start(path, self.cfg.d["file_delay_ms"])
        else:
            self._notify("文件不存在: %s" % path)

    def _stop_file(self):
        self.file_sender.stop()
        self._file_done()

    def _file_progress(self, done, total):
        self.progress.setValue(done)

    def _file_done(self, *args):
        self.progress.hide()

    def _file_failed(self, msg: str):
        self._file_done()
        if msg:
            QMessageBox.warning(self, "发送文件", msg)

    # ============================================================ 多条发送
    def _ms_del(self):
        # 点击发送按钮会吃掉点击，表格 currentIndex 可能无效：此时删除最后一行
        idx = self.ms_table.currentIndex()
        row = idx.row() if idx.isValid() else (len(self.ms_model.rows) - 1 if self.ms_model.rows else -1)
        if row >= 0:
            self.ms_model.remove_row(row)

    def _ms_send_row(self, row):
        data = self._build_payload(row["text"], row["hex"], self.cb_newline.isChecked(),
                                   self.cmb_checksum.currentData() != "none")
        if data:
            self._write(data)

    def _ms_help(self):
        """多条发送说明（对齐 SSCOM 帮助条目，按 PyCom 实际行为改写）。"""
        QMessageBox.information(self, "多条发送说明", "\n".join([
            "自定义多条数据串帮助:",
            "1. 勾选 HEX 表示该条为 16 进制数据串，书写方式如 13 00 FF 表示三个字节；不勾选则按字符串发送。",
            "2. \"内容\"列是要发送到串口的数据。",
            "3. 双击\"点击发送\"列，可以修改该条目的注释，注释显示在发送按钮上。",
            "4. 点击行内\"发送\"按钮，将发送该条数据。",
            "5. 勾选\"循环发送\"后，将按顺序号从小到大依次循环发送各条目。",
            "6. 顺序号为 0 时不发送该条目。",
            "7. 每条发送后，将延时该条设定的\"延时ms\"再发送下一条。",
            "8. 主界面上的加回车换行和加校验对多条发送同样有效。",
            "9. 拖动分割线可以改变多条发送面板的宽度。",
            "10. 点 +/- 按钮可以增加/删除数据条目。",
            "11. 小工具菜单的\"导入sscom.ini配置\"可以导入之前保存的 ini 文件。",
        ]))

    def _toggle_ms_loop(self, *_):
        if self.cb_ms_loop.isChecked():
            self._ms_loop_starting = True
            try:
                ok = self.ms_loop.start_loop()
            finally:
                self._ms_loop_starting = False
            if not ok:
                self.cb_ms_loop.setChecked(False)
                self._notify("没有顺序号大于 0 的条目，无法循环发送", 4000)
        else:
            self.ms_loop.stop()

    def _on_ms_loop_stopped(self):
        """调度器因没有可发送条目（运行中行被清空/order 全改 0）自行停止：
        同步取消勾选，避免"勾选还在、实际已停"的状态不一致。"""
        if not self.cb_ms_loop.isChecked():
            return
        self.cb_ms_loop.setChecked(False)     # toggled → _toggle_ms_loop 再 stop（无害）
        if not getattr(self, "_ms_loop_starting", False):
            self._notify("没有顺序号大于 0 的条目，循环发送已停止", 4000)

    def _on_ms_clicked(self, idx):
        # 单击"点击发送"列 → 立即发送该行（双击编辑注释由视图原生编辑器处理）
        if idx.column() == COL_SEND and idx.row() < len(self.ms_model.rows):
            self._ms_send_row(self.ms_model.rows[idx.row()])
        # 单击 HEX 列任意位置 → 整格切换勾选
        elif idx.column() == COL_HEX and idx.row() < len(self.ms_model.rows):
            r = self.ms_model.rows[idx.row()]
            self.ms_model.setData(idx, Qt.Unchecked if r["hex"] else Qt.Checked,
                                  Qt.CheckStateRole)

    # ============================================================ 显示/接收
    def _detach_rx_log(self):
        """日志对象脱钩（正常关闭或写失败）：关闭句柄并复位转存游标——
        游标是否存在只有本类知道，残留值会把行数硬上限的裁剪永久锁死。
        全部脱钩路径统一走这里（幂等）：不要再裸调 _rx_log.close() 或
        裸写 _rx_log = None，否则要么漏复位、要么写失败后 AttributeError。"""
        if self._rx_log is not None:
            try:
                self._rx_log.close()
            except Exception:         # 脱钩本身就是目的：句柄再坏也不得中断脱钩
                pass
            self._rx_log = None
            self.builder.set_log_keep(None)

    def _toggle_rxfile(self, on):
        """勾选即存（无对话框）：默认文件名 PyCom-Log-<YYYYMMDD_HHMMSS>.txt
        —— 内容为接收区显示的文本（含时间戳前缀，随显示模式 ASCII/HEX），
        按勾选时设置的编码保存，每次勾选新建。"""
        if on:
            ts = time.strftime("%Y%m%d_%H%M%S")
            path = os.path.join(base_dir(), "PyCom-Log-%s.txt" % ts)
            k = 1
            while os.path.exists(path):      # 同秒多次开关不覆盖旧日志
                path = os.path.join(base_dir(), "PyCom-Log-%s-%d.txt" % (ts, k))
                k += 1
            enc = self._current_encoding()
            try:
                import codecs
                codecs.lookup(enc)
                self._rx_log = open(path, "w", encoding=enc, errors="replace")
            except (OSError, LookupError, ValueError):
                self._detach_rx_log()
                QMessageBox.warning(self, "接收数据到文件",
                                    "当前目录不可写或编码无效! 请检查后重试.")
                self.cb_rxfile.setChecked(False)
                return
            # 只记录"之后"的内容：游标从当前已完成行上界开始，不回写旧显示内容
            self._rx_log_written = self.builder.base + len(self.builder.lines)
            self._log_timer.start()
            self._notify("接收数据将存到：%s（编码 %s）"
                         % (os.path.basename(path), enc))
        else:
            self._log_timer.stop()
            self._rx_log_poll()               # 收尾补写已完成的行
            self._rx_log_flush_tail()         # 补写未收尾行
            self._detach_rx_log()             # 关句柄并复位游标（幂等，写失败时已自行脱钩）

    def _on_display_cleared(self):
        self._rx_log_written = 0             # 显示行清空，游标归零

    def _rx_log_poll(self):
        """增量写入：按绝对行号（base 换算）推进游标，逐行写未写出的部分。
        写入成功后才推进游标（失败可重试）；行内已转存尾巴补行终止符，
        不会漏写/重写/拼接。"""
        if not self._rx_log:
            return
        base = self.builder.base
        upper = base + len(self.builder.lines)      # 已完成行的绝对上界
        start = max(self._rx_log_written, base)     # 已裁剪掉的旧行不再遍历
        if start >= upper:
            self._rx_log_written = upper
            return
        parts = []
        staged = []
        for i in range(start, upper):
            l = self.builder.line_at(i)
            if l is None:
                continue                            # 被裁剪且早已写过的行
            written = getattr(l, "written", 0)
            nl = getattr(l, "nl_done", False)
            if nl:
                continue                            # 行终止符已写：无内容可补
            # 真实空行也要写出（时间戳前缀/方向标识 + 行终止符），与显示一致；
            # 两者都只在"加时间戳和分包显示"勾选时带出，方向标识仅包首行有
            anno = self.builder.split
            prefix = l.prefix if (written == 0 and anno) else ""
            marker = dir_marker(l.dir) if (written == 0 and prefix) else ""
            chunk = l.text[written:]
            parts.append(prefix + marker + chunk + "\n")
            staged.append(l)
        try:
            if parts:
                self._rx_log.write("".join(parts))
                self._rx_log.flush()
        except (OSError, ValueError):     # ValueError: 文件已关闭
            self._detach_rx_log()
            self._log_timer.stop()
            self.cb_rxfile.setChecked(False)
            self._notify("日志写入失败，已停止接收数据到文件")
            return
        for l in staged:                    # 写入成功后才推进游标
            l.written = len(l.text)
            l.nl_done = True
        self._rx_log_written = upper

    def _rx_log_flush_tail(self):
        """把未收尾行未写出的尾部补写进日志并补行终止符（末行收尾）。
        重复调用安全：成功写入后才推进游标。"""
        if not self._rx_log:
            return
        text = self.builder.open_line_delta()
        if not text:
            return
        try:
            self._rx_log.write(text + "\n")
            self._rx_log.flush()
        except (OSError, ValueError):
            self._detach_rx_log()
            self._log_timer.stop()
            self.cb_rxfile.setChecked(False)
            self._notify("日志写入失败，已停止接收数据到文件")
            return
        self.builder.commit_open(nl_written=True)

    def _build_ascii_table(self) -> QWidget:
        """ASCII 码表页：0-127 全表；双击行复制对应字符。"""
        from PySide2.QtWidgets import QAbstractItemView, QTableWidget, QTableWidgetItem

        abbrs = ["NUL", "SOH", "STX", "ETX", "EOT", "ENQ", "ACK", "BEL",
                 "BS", "HT", "LF", "VT", "FF", "CR", "SO", "SI",
                 "DLE", "DC1", "DC2", "DC3", "DC4", "NAK", "SYN", "ETB",
                 "CAN", "EM", "SUB", "ESC", "FS", "GS", "RS", "US", "SP"]
        descs = ["空字符", "标题开始", "正文开始", "结束传输", "传输结束", "询问",
                 "确认", "响铃", "退格", "水平制表符", "换行", "垂直制表符", "换页",
                 "回车", "移出", "移入", "数据链路转义", "设备控制1", "设备控制2",
                 "设备控制3", "设备控制4", "否定确认", "同步空闲", "传输块结束",
                 "取消", "介质结束", "替换", "转义", "文件分隔符", "组分隔符",
                 "记录分隔符", "单元分隔符", "空格"]

        def desc_of(d: int) -> str:
            if d <= 32:
                return descs[d]
            if d == 127:
                return "删除"
            if 48 <= d <= 57:
                return "数字"
            if 65 <= d <= 90:
                return "大写字母"
            if 97 <= d <= 122:
                return "小写字母"
            return "标点符号"

        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(2, 2, 2, 2)
        table = QTableWidget(128, 5)
        table.setHorizontalHeaderLabels(["十进制", "十六进制", "二进制", "字符", "缩写/说明"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setWordWrap(False)
        for d in range(128):
            ch = chr(d)
            printable = 32 <= d <= 126
            vals = (str(d), "0x%02X" % d, format(d, "08b"),
                    ch if printable else "", abbrs[d] if d <= 32 else
                    ("DEL" if d == 127 else ch))
            for col, val in enumerate(vals):
                it = QTableWidgetItem(val)
                if col in (0, 1, 2):
                    it.setTextAlignment(Qt.AlignCenter)
                table.setItem(d, col, it)
        table.resizeColumnsToContents()
        table.setColumnWidth(4, 150)
        table.horizontalHeader().setStretchLastSection(True)   # 末列撑满：不留原生白底

        def copy_char(idx):
            ch = chr(idx.row()) if 32 <= idx.row() <= 126 else \
                (abbrs[idx.row()] if idx.row() <= 32 else "DEL")
            QApplication.clipboard().setText(ch)
            self._notify("已复制: %s" % ch, 2000)

        table.doubleClicked.connect(copy_char)
        v.addWidget(table)
        return w

    def _set_split(self, *_):
        self.builder.set_split(self.cb_timestamp.isChecked(), self.sp_split_ms.value())
        self.rxview.viewport().update()   # 前缀/方向标识显示立即跟随开关

    def _on_hex_show_toggled(self, on: bool):
        self.builder.set_hex_mode(on)
        self.find_bar.notify_data_changed()    # 搜索域变了，活动搜索需重算

    def _update_checksum_view(self):
        """实时显示校验字节（HEX）：帧尾档=插入到帧尾前的值，旧版档=追加值。"""
        kind = self.cmb_checksum.currentData()
        tail = self.cmb_chk_place.currentData()
        text = self.send_edit.toPlainText()
        if kind == "none" or not tail or not text:
            self.edit_checksum_view.clear()
            return
        data = self._build_payload(text, self.cb_send_hex.isChecked(),
                                   self.cb_newline.isChecked(), False)
        from app.core.checksum import checksum_appendix, checksum_body_slice
        body = checksum_body_slice(data, self.sp_chk_start.value(), tail)
        self.edit_checksum_view.setText(" ".join("%02X" % b
                                                 for b in checksum_appendix(kind, body)))

    # ---- 编码 ----
    def _current_encoding(self) -> str:
        """当前生效编码（由"编码"菜单或自定义对话框设置）。"""
        return self._encoding

    def _apply_encoding(self, enc: str) -> None:
        import codecs
        try:
            info = codecs.lookup(enc)
            if not getattr(info, "_is_text_encoding", True):
                raise ValueError("非文本编码")
        except (LookupError, ValueError):
            self._notify("无效编码: %s（可输入如 utf-8/koi8-r/cp1251）" % enc, 4000)
            return
        self._encoding = enc
        self.builder.set_encoding(enc)
        self.find_bar.notify_data_changed()    # HEX 搜索的解码域变了，需重算
        for _e, _act in self.enc_actions.items():
            _act.setChecked(_e == enc)
        self._update_checksum_view()
        self._notify("显示/发送编码: %s" % enc, 2000)

    def _custom_encoding(self):
        from PySide2.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self, "自定义编码",
                                        "输入任意 Python 编码名（如 utf-16-be、koi8-r）:")
        if ok and text.strip():
            self._apply_encoding(text.strip())

    def _set_buffer_cap(self, size):
        size = self._clamp_cap(size)
        self.cfg.d["buffer_cap"] = size
        self.buf.set_cap(size)
        self._trim_lines_to_buf()      # 调小时缓冲立即丢旧字节，行表同步丢
        self._update_buf_actions()
        self._notify("显示缓冲上限: %d 字节" % size, 3000)

    def _update_buf_actions(self):
        for size, act in self.buf_actions.items():
            act.setChecked(size == self.cfg.d["buffer_cap"])

    def _refresh_ports(self, select=None):
        from app.core.serial_link import SerialLink
        self.cmb_port.clear()
        for name, desc in SerialLink.list_ports():
            # 端口下拉不可编辑，currentData 可靠（可编辑下拉的历史坑见 docs/01-项目总览与功能规格.md）
            self.cmb_port.addItem("%s - %s" % (name, desc) if desc else name, name)
        if select:
            i = self.cmb_port.findData(select)
            if i >= 0:
                self.cmb_port.setCurrentIndex(i)
        elif self.cmb_port.count():
            self.cmb_port.setCurrentIndex(0)

    def _refresh_nics(self, select_ip=None):
        """枚举本机网络接口填入"网卡"下拉（"全部网卡" + 各接口，含热插拔后刷新）。
        select_ip 用于自动选中包含该地址的网卡。选中网卡后本地 IP 只列该网卡地址。
        地址表为 IPv4 在前、IPv6 在后（选中即按该地址单栈绑定，两族互不混收）。"""
        from PySide2.QtNetwork import QAbstractSocket, QNetworkInterface
        nics = []                              # [(显示名, [IPv4..., IPv6...])]
        for if_ in QNetworkInterface.allInterfaces():
            if not (if_.flags() & QNetworkInterface.IsUp):
                continue
            v4, v6 = [], []
            for e in if_.addressEntries():
                ip = e.ip()
                t = ip.toString()
                if ip.protocol() == QAbstractSocket.IPv4Protocol:
                    if t not in v4:
                        v4.append(t)
                elif ip.protocol() == QAbstractSocket.IPv6Protocol:
                    if t not in v6:            # 链路本地自带 %作用域（fe80::x%xx）
                        v6.append(t)
            addrs = v4 + v6
            if not addrs:
                continue
            name = if_.humanReadableName() or if_.name()
            nics.append(("%s (%s)" % (name, addrs[0]), addrs))
        self._nics = nics
        cur = self.cmb_nic.currentText() if self.cmb_nic.currentIndex() > 0 else ""
        self.cmb_nic.blockSignals(True)
        self.cmb_nic.clear()
        self.cmb_nic.addItem("全部网卡", None)
        self.cmb_nic.setItemData(0, "本机所有网卡地址（IPv4/IPv6）", Qt.ToolTipRole)
        auto = -1
        for label, addrs in nics:
            self.cmb_nic.addItem(label, addrs)
            self.cmb_nic.setItemData(self.cmb_nic.count() - 1, label,
                                     Qt.ToolTipRole)   # 下拉列表项悬停显示全名
            if select_ip and select_ip in addrs and auto < 0:
                auto = self.cmb_nic.count() - 1
        if auto >= 0:
            self.cmb_nic.setCurrentIndex(auto)     # 启动时自动选中含配置 IP 的网卡
        else:
            i = self.cmb_nic.findText(cur)
            if i > 0:
                self.cmb_nic.setCurrentIndex(i)    # 刷新后尽量保持原选择
        self.cmb_nic.blockSignals(False)
        self._sync_nic_tip()
        self._refresh_local_ips(select=select_ip)

    def _sync_nic_tip(self):
        """闭合态悬停时显示当前选中网卡的完整名称。"""
        i = self.cmb_nic.currentIndex()
        self.cmb_nic.setToolTip(self.cmb_nic.itemText(i) if i > 0
                                else "选择本机网络接口；本地IP随之联动")

    def _on_nic_selected(self, *_):
        self._sync_nic_tip()
        self._refresh_local_ips(select=self.cmb_local_ip.currentText())

    def _refresh_local_ips(self, select=None):
        """按当前网卡选择填充"本地"IP 下拉。单栈语义——选中哪个地址就只绑定
        该族：全部网卡 = 0.0.0.0（仅 IPv4 全部）+ 各 IPv4 + ::（仅 IPv6 全部）
        + 各 IPv6（全局在前、链路本地 fe80::x%作用域 在后）；指定网卡 = 只列
        该网卡地址（IPv4 在前 IPv6 在后）。"""
        from PySide2.QtNetwork import QNetworkInterface
        v4, v6 = [], []
        nic_addrs = self.cmb_nic.currentData()
        if nic_addrs:
            for t in nic_addrs:                # IPv6 必含 ":"，IPv4 必不含
                (v6 if ":" in t else v4).append(t)
        else:
            for if_ in QNetworkInterface.allInterfaces():
                for e in if_.addressEntries():
                    t = e.ip().toString()
                    if ":" in t:
                        if t not in v6:
                            v6.append(t)
                    elif t not in v4:
                        v4.append(t)
            if "127.0.0.1" not in v4:
                v4.append("127.0.0.1")
        v6.sort(key=lambda s: (s.startswith("fe80"), s))   # 全局地址在前
        if nic_addrs:
            addrs = v4 + v6
        else:
            addrs = (["0.0.0.0"] + v4 + ["::"] + v6)
        self.cmb_local_ip.clear()
        self.cmb_local_ip.addItems(addrs)
        if not nic_addrs:                      # 两个"全部地址"项给足语义提示
            self.cmb_local_ip.setItemData(0, "仅 IPv4（所有网卡）", Qt.ToolTipRole)
            i6 = self.cmb_local_ip.findText("::")
            if i6 > 0:
                self.cmb_local_ip.setItemData(i6, "仅 IPv6（所有网卡）；选中后"
                                              "只收发 IPv6，不混收 IPv4",
                                              Qt.ToolTipRole)
        if select:
            i = self.cmb_local_ip.findText(select)
            if i >= 0:
                self.cmb_local_ip.setCurrentIndex(i)

    def _pick_text_font(self, scope: str):
        """发送/接收字体设置对话框：字体族/大小/加粗/下划线/颜色。"""
        from PySide2.QtGui import QColor
        from PySide2.QtWidgets import (QCheckBox, QColorDialog, QDialog,
                                       QDialogButtonBox, QFontComboBox, QGridLayout,
                                       QSpinBox, QVBoxLayout)
        is_send = scope == "send"
        key = "send_font" if is_send else "recv_font"
        title = "发送字体设置" if is_send else "接收字体设置"
        cfg = dict(self.cfg.d[key])

        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        lay = QVBoxLayout(dlg)
        grid = QGridLayout()
        grid.addWidget(QLabel("字体"), 0, 0)
        fam = QFontComboBox()
        fam.setCurrentFont(QFont(cfg.get("family") or "宋体"))
        grid.addWidget(fam, 0, 1)
        grid.addWidget(QLabel("大小"), 1, 0)
        size = QSpinBox()
        size.setRange(6, 72)
        size.setValue(int(cfg.get("size") or 10))
        grid.addWidget(size, 1, 1)
        bold = QCheckBox("加粗")
        bold.setChecked(bool(cfg.get("bold")))
        grid.addWidget(bold, 2, 1)
        under = QCheckBox("下划线")
        under.setChecked(bool(cfg.get("underline")))
        grid.addWidget(under, 3, 1)
        grid.addWidget(QLabel("颜色(空=随主题方向色)"), 4, 0)
        chip = QPushButton()
        chip.setFixedSize(70, 20)
        color = {"v": cfg.get("color") or ""}

        def upd_chip():
            chip.setStyleSheet("background:%s; border:1px solid #888;"
                               % (color["v"] or "#D0D0D0"))

        def pick_color():
            # 注意：QColorDialog.getColor 不接受 None 初始值（抛 TypeError），必须传有效 QColor
            init = QColor(color["v"]) if color["v"] else QColor(Qt.white)
            c = QColorDialog.getColor(init, dlg, "颜色")
            if c.isValid():
                color["v"] = c.name()
                upd_chip()

        chip.clicked.connect(pick_color)
        upd_chip()
        grid.addWidget(chip, 4, 1)
        lay.addLayout(grid)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)

        if dlg.exec_() == QDialog.Accepted:
            self.cfg.d[key] = {"family": fam.currentFont().family(),
                               "size": size.value(),
                               "bold": bold.isChecked(),
                               "underline": under.isChecked(),
                               "color": color["v"]}
            self._apply_text_fonts()

    def _apply_text_fonts(self):
        from app.core.settings import DEFAULTS
        send = self.cfg.d.get("send_font")
        recv = self.cfg.d.get("recv_font")
        if not isinstance(send, dict):
            send = DEFAULTS["send_font"]
        if not isinstance(recv, dict):
            recv = DEFAULTS["recv_font"]
        self.rxview.set_text_fonts(send, recv)

    def _pick_bg(self):
        from PySide2.QtWidgets import QColorDialog
        c = QColorDialog.getColor()
        if c.isValid():
            self.cfg.d["bg_color"] = c.name()
            self._apply_theme()

    def _keep_top(self, on):
        flag = Qt.WindowStaysOnTopHint
        if on:
            self.setWindowFlags(self.windowFlags() | flag)
        else:
            self.setWindowFlags(self.windowFlags() & ~flag)
        self.show()

    # ============================================================ INI 导入
    def _serial_help(self):
        """串口设置-帮助：串口设置帮助（对齐 SSCOM 原文，按本软件行为改写）。"""
        QMessageBox.information(self, "串口设置帮助",
                                "<b>串口设置帮助:</b>"
                                "<p>1.本软件可以使用计算机上的串口号和网络TCP/UDP通讯,"
                                "您可以直接选择对应的通讯通道,数据发送和接收操作都是相同的.</p>"
                                "<p>2.串口号和波特率可以在面板上直接选择,"
                                "下次打开时会继续使用该通道.</p>"
                                "<p>3.串口的\"数据位、停止位、校验位、流控\"在串口参数面板上"
                                "直接设置,有些串口设备不能对所有的选项全部支持.本软件会随其它"
                                "参数一起保存这四个设置.</p>"
                                "<p>4.对于主板上的串口,波特率一般不能超过115200bps,"
                                "对于USB串口,有些可以达到2000000bps以上.</p>"
                                "<p>5.自定义波特率: 选择波特率列表最上面的\"自定义\","
                                "可以自己输入波特率,最高可输入12000000bps.</p>"
                                "<p>6.串口设置错误有可能会打不开串口.这时可以关闭软件后,"
                                "删除配置文件pycom.json,重新打开软件,"
                                "将会按默认设置重新配置串口.</p>"
                                "<p>7.勾选\"启动立刻打开串口\","
                                "在程序启动时会自动打开默认的串口.</p>")

    def _term_help(self):
        """发送菜单-帮助：终端仿真发送帮助（对齐 SSCOM 原文）。"""
        QMessageBox.information(self, "终端仿真发送帮助",
                                "<b>终端仿真发送帮助:</b>"
                                "<p>1. 把光标放在接收数据显示窗口里,输入指令后,"
                                "可以直接发出去,终端收到后,返回信息来显示。</p>"
                                "<p>2. 发送方式选择:【回车后发送新行(带回显)】 在接收显示窗口里按 回车，"
                                "将会发送'在该窗口输入的最后一行字符+回车',该方式默认有本地回显.</p>"
                                "<p>3. 发送方式选择:【按键立即发送该键值】 在接收显示窗口里按键时,"
                                "将会立即发送该键值.两种发送方式互斥,选中一个会自动取消另一个.</p>"
                                "<p>4. 勾选【本地回显按键值】 对于按键立即发送键值方式,"
                                "勾选本项后按键会本地处理回显,显示这个字符.</p>"
                                "<p>5. 勾选【发送回车0x0d后,自动加上换行符0x0a】 在上面两种发送方式下,"
                                "按下回车后将会发送两个字符回车换行(0x0d,0x0a).</p>"
                                "<p>6. 举个例子: 调试类linux系统板, 启动终端板后返回信息,"
                                "最后一行显示'$ &gt;' (有些终端不显示这个提示，也可以直接输入命令)，"
                                "把光标放在接收窗口,输入'help'后，显示为$ &gt;'help',此时再回车，"
                                "将会发送'help'+回车执行，终端收到help命令后返回帮助信息。"
                                "这种类似DOS窗口的输入和返回功能，叫做终端仿真，"
                                "可以很方便地用来调试一些交互终端数据，"
                                "不需要在发送框预先输入发送命令。</p>")

    def _import_ms_ini(self):
        """扩展→多字符串发送：仅导入原版 ini 的多条字符串配置。"""
        path, _ = QFileDialog.getOpenFileName(self, "导入多字符串配置（sscom.ini）",
                                              "", "INI (*.ini)")
        if not path:
            return
        notes = self.cfg.import_ms_ini(path)
        self.ms_model.beginResetModel()
        self.ms_model.rows = self.cfg.d.get("multisend_rows") or []
        self.ms_model.endResetModel()
        self._save_settings(silent=True)
        QMessageBox.information(self, "导入完成", "\n".join(notes) or "已导入")

    # ============================================================ 其它
    def _open_calc(self):
        try:
            os.startfile("calc")             # Windows 系统计算器
            self._notify("已打开系统计算器", 2000)
        except OSError as ex:
            QMessageBox.warning(self, "计算器", "无法打开: %s" % ex)

    def _open_explorer(self):
        """打开 Windows 文件资源管理器，默认定位"此电脑"（我的电脑）。"""
        try:
            os.startfile("::{20D04FE0-3AEA-1069-A2D8-08002B30309D}")
        except OSError as ex:
            self._notify("无法打开文件资源管理器: %s" % ex)

    def _fix_natural_widths(self):
        """把工具行左组、文件块固定键、右侧四键与选项行控件钉在自然宽度
        （SSCOM 式截断的前提：这些控件永不压缩，缩窗时由 ClipBox 溢出裁剪）。
        字体度量异步沉降，故与垫片同拍重钉、整体重设、非累积，沉降后收敛。"""
        for w in (self.btn_clear, self.btn_find, self.btn_new_window,
                  self.cb_auto_scroll, self.btn_open_file, self.btn_send_file,
                  self.btn_stop_file, self.btn_clear_send, self.btn_collapse,
                  self.btn_keep_top, self.btn_save_params, self.btn_expand):
            w.setFixedWidth(w.sizeHint().width())
        for w in self._opt_widgets[1:]:   # [0] 是发送输入框（保持可伸缩）
            w.setFixedWidth(min(w.sizeHint().width(), w.maximumWidth()))

    def _update_file_pad(self):
        """重算并应用文件块垫片：文件块起始横坐标 = 左下角"发送"按钮的右缘。
        用已渲染控件的真实坐标做差（垫片左侧的控件位置与垫片值无关，可安全
        实测）；"自动滚屏"在新窗口右侧，其宽度与行间距必须一并计入。
        每拍整体重设、非累积。"""
        self._fix_natural_widths()
        x_of = lambda wgt: wgt.mapTo(self, wgt.rect().topLeft()).x()
        target = x_of(self.btn_send) + self.btn_send.width()      # 发送按钮右缘
        pre = x_of(self.cb_auto_scroll) + self.cb_auto_scroll.width()
        self._file_pad = max(0, target - pre - 2 * 3)  # 2 处行间距：滚屏→垫片、垫片→打开文件
        self._apply_file_pad()

    def _apply_file_pad(self):
        """应用垫片（用户要求：文件块起始横坐标恒定 = 左下角"发送"按钮的
        右缘，任何窗口宽度都显示并对齐；窗口极窄时路径框/右侧按钮自然裁剪）。"""
        self.spacer_file.setFixedWidth(self._file_pad)

    def _show_ext_tab(self, idx: int):
        """展开扩展面板并切到指定页（0=多条字符串发送 1=转换计算器 2=ASCII码表）。"""
        if not self.btn_expand.isChecked():
            self.btn_expand.setChecked(True)
            self._toggle_expand(True)
        self.tabs.setCurrentIndex(idx)

    def _open_devmgr(self):
        try:
            os.startfile("devmgmt.msc")      # Windows 设备管理器
            self._notify("已打开设备管理器", 3000)
        except OSError as ex:
            QMessageBox.warning(self, "设备管理器", "无法打开: %s" % ex)

    def _on_baud_activated(self, idx: int):
        # 选中"自定义"项（或编辑框为空）：清空等待输入任意波特率
        text = self.cmb_baud.currentText().strip()
        if not text or text == "自定义":
            self.cmb_baud.setEditText("")
            self.cmb_baud.setFocus()
            return
        self._apply_live_baud()

    def _apply_live_baud(self) -> None:
        """串口打开时改波特率：自动用新参数重开串口（等效手动关闭再打开，无需手动操作）。
        不用 setBaudRate 动态切换——CH34x 等驱动动态改波特率不可靠，必须重新打开端口。"""
        if not (self.link and self.link.is_open()):
            return
        baud = self._current_baud()
        if getattr(self.link, "cfg", {}).get("baud") == baud:
            return                      # 波特率没变，不重开
        self._open_link()
        self._notify("波特率已切换: %d（串口已自动重开）" % baud, 2000)

    def _current_baud(self) -> int:
        """波特率取编辑框文本；"自定义"未输入/非法时回退 115200。"""
        try:
            return int(self.cmb_baud.currentText())
        except (TypeError, ValueError):
            return 115200

    def _open_release_page(self):
        """检查更新：打开发布页（最新版发布与下载在网页进行，应用内不联网）。"""
        url = "https://github.com/smartmx/PyCom/releases/latest"
        if not QDesktopServices.openUrl(QUrl(url)):
            self._notify("无法打开浏览器，请手动访问 " + url)

    def _about(self):
        QMessageBox.about(self, "关于 PyCom32",
                          "<b>PyCom32 串口/网络数据调试器</b>"
                          "<p>版本：%s</p>"
                          "<p>系统要求：Windows 7 SP1及以上；32/64位系统</p>"
                          "<p>开发者：CoderNotCutie<br>"
                          "代码仓库：<a href='https://github.com/smartmx/PyCom'>"
                          "github.com/smartmx/PyCom</a></p>"
                          "<p>基于 Python + PySide2（Qt SerialPort）构建。<br>"
                          "功能：串口 / TCP 客户端 / TCP 服务端 / UDP 收发调试，"
                          "多条发送、校验、文件发送、终端仿真等。</p>"
                          "<p>开源许可：MIT License"
                          "（Copyright © 2026 CoderNotCutie）</p>"
                          "<p style='color:#888'>使用库：PySide2（Qt5 / QtSerialPort / QtSvg）"
                          " · Python 标准库</p>" % APP_VERSION)

    def _save_rx(self, path: str | None = None):
        # RxView 右键"保存接收字节"也走这里（直接给路径，跳过对话框）
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, "保存接收数据",
                                                  os.path.join(base_dir(), "received.txt"),
                                                  "文本 (*.txt);;原始字节 (*.bin)")
        if path:
            if path.endswith(".bin"):
                try:
                    with open(path, "wb") as f:
                        f.write(self.buf.tail(len(self.buf)))
                except OSError as ex:
                    QMessageBox.warning(self, "保存数据", "保存失败: %s" % ex)
            else:
                try:
                    with open(path, "w", encoding=self._current_encoding(),
                              errors="replace") as f:
                        f.write(self.rxview.plain_text(self.rxview.save_with_anno))
                except (OSError, LookupError, UnicodeEncodeError) as ex:
                    QMessageBox.warning(self, "保存数据", "保存失败: %s" % ex)

    def _flush_log_before_clear(self):
        self._rx_log_poll()
        self._rx_log_flush_tail()
        self._rx_log_written = 0

    def _reset_counters(self):
        """右键状态栏清零收发计数与速率。"""
        self.tx_bytes = self.rx_bytes = 0
        self._speed_samples = []     # 旧采样基线一并清掉，速率立即归零
        self.sb_tx.setText("TX: 0 (0 B/s)")
        self.sb_rx.setText("RX: 0 (0 B/s)")

    def _clear_display(self):
        # rxview.clear_all 内部会触发 pre_clear_hook（即本类 _flush_log_before_clear），
        # 其中的 poll/tail 写入均为幂等推进，重复触发不会重复写日志
        self.rxview.clear_all()
        self._term_line.clear()               # 回显行已随显示清空
        self._term_line_raw.clear()
        self.find_bar.notify_data_changed()   # 清屏后旧命中全部失效，重算归零

    def _save_settings(self, silent=False):
        d = self.cfg.d
        d["mode"] = self._mode
        d["port"] = self.cmb_port.currentData()
        try:
            d["baud"] = int(self.cmb_baud.currentText())
        except ValueError:
            pass
        d["dtr"] = self.cb_dtr.isChecked()
        d["rts"] = self.cb_rts.isChecked()
        d["databits"] = int(self.cmb_data.currentText())
        d["stopbits"] = float(self.cmb_stop.currentText())
        d["parity"] = self.cmb_parity.currentData()
        d["flow"] = self.cmb_flow.currentData()
        d["auto_reconnect"] = self.act_auto_reconnect.isChecked()
        d["open_on_start"] = self.act_open_start.isChecked()
        d["remote_ip"] = self.edit_remote_ip.text()
        d["remote_port"] = self.edit_remote_port.value()
        d["local_ip"] = self.cmb_local_ip.currentText()
        d["local_port"] = self.edit_local_port.value()

        d["encoding"] = self._current_encoding()
        d["hex_mode"] = self.cb_hex_show.isChecked()
        d["auto_scroll"] = self.cb_auto_scroll.isChecked()
        d["split_show"] = self.cb_timestamp.isChecked()
        d["split_ms"] = self.sp_split_ms.value()
        d["copy_with_anno"] = self.rxview.copy_with_anno
        d["save_with_anno"] = self.rxview.save_with_anno
        d["send_hex"] = self.cb_send_hex.isChecked()
        d["main_text"] = self.send_edit.toPlainText()
        d["send_newline"] = self.cb_newline.isChecked()
        d["send_interval_ms"] = self.sp_timed.value()
        d["checksum_kind"] = self.cmb_checksum.currentData()
        d["checksum_start"] = self.sp_chk_start.value()
        d["checksum_tail"] = self.cmb_chk_place.currentData()
        d["multisend_rows"] = self.ms_model.rows
        d["multisend_loop"] = self.cb_ms_loop.isChecked()
        d["term_enter_send"] = self.act_term_enter.isChecked()
        d["term_key_send"] = self.act_term_key.isChecked()
        d["term_key_echo"] = self.act_term_echo.isChecked()
        d["term_0d_add_0a"] = self.act_term_lf.isChecked()
        d["theme"] = "dark" if self.act_theme.isChecked() else "light"
        d["win_w"], d["win_h"] = self.width(), self.height()
        # 注意：不能在这里停循环发送——保存/导入多条配置都会走本函数，
        # 停掉会造成"勾选还在、调度器已停"的静默不一致（关闭窗口时才停）
        if self.cfg.save():
            if not silent:
                self._notify("参数已保存", 2000)
        elif not silent:
            self._notify("参数保存失败（目录不可写或路径无效）", 4000)

    def closeEvent(self, ev):
        self.timed_timer.stop()
        self.ms_loop.stop()
        self.file_sender.stop()
        self._tx_timer.stop()
        self._tx_pend.clear()
        if self._rx_log:
            self._rx_log_poll()
            self._rx_log_flush_tail()     # 补写未收尾行（写失败时已自行脱钩）
            self._detach_rx_log()         # 收尾后关句柄并复位游标（幂等）
        self._close_link(silent=True)
        self._save_settings(silent=True)
        super().closeEvent(ev)

    @staticmethod
    def _select_combo(cb, text):
        i = cb.findText(text)
        if i >= 0:
            cb.setCurrentIndex(i)
        else:
            cb.setEditText(text)
