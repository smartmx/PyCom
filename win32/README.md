# PyCom32 —— SSCOM 5.13.1 功能复刻增强版（Win7 32 位分支）

从头复刻原版 SSCOM 串口/网络调试工具的核心功能，并内置三项增强 + 三项新功能。
不依赖原版 exe，纯 Python 实现（**Win7 回迁版：32 位 Python 3.8 + PySide2/Qt5**，无 pyserial；
产物 `dist\PyCom32.exe` 为 32 位单文件版，Win7 32/64 位系统均可运行）。
回迁改动明细见 [04-Win7-32位适配说明.md](../docs/04-Win7-32位适配说明.md)。

## 运行

```
"D:\Program Files\Python38-x32\python.exe" -m pip install -r requirements.txt
"D:\Program Files\Python38-x32\python.exe" main.py
```

打包版只有一个：`dist\PyCom32.exe`——**单文件便携版**（32 位程序，拷单个 exe 到任意位置
即可运行，启动时解包到系统临时目录，略慢）。**不再打包目录版。**
重新打包：`tools\build_exe.bat`（自动调用 32 位 Python 3.8；Win7 专用 UCRT
随包文件放在 `vendor\ucrt-x86\`，spec 会带上、缺了直接报错）；打完用
`python tools\audit_dist_win7.py` 复核位数/依赖解析/UCRT 血统。

## 功能清单（对齐原版）

| 分类 | 功能 |
|---|---|
| 连接 | 串口（波特率/数据位/停止位/校验/流控/DTR/RTS）、TCP 客户端、TCP 服务端（多客户端）、UDP、网卡选择（本地 IP 随选定网卡联动，可刷新枚举） |
| 接收 | HEX/ASCII 显示切换、自动滚屏（可关：关闭后新数据只入缓冲不跟随）、时间戳+分包（空闲超时可配；未勾选时为纯文本，包首行带 `[时:分:秒.毫秒]收←◆/发→◇`，续行只有数据）、12 种预置编码（GBK 默认/GB2312/GB18030/UTF-8/UTF-16LE·BE/Big5/Shift-JIS/EUC-JP/EUC-KR/cp1252/Latin-1，另可经菜单“自定义编码…”输入任意 Python 编码名）、显示缓冲上限 500K~50M（默认 5MB；**行表跟随缓冲裁剪**——回看范围 ≈ *.bin 导出范围，行表另有 50 万行硬上限，见 [02-架构与代码导读.md](../docs/02-架构与代码导读.md)）、清屏、保存（文本/原始字节） |
| 发送 | 主区 ASC/HEX、定时发送、加回车换行、发送文件（连续 / 每256B 延时 1/10/50/100ms）、停止 |
| 多条发送 | 最多 100 条、注释、每条 ASC/HEX 与延时、循环发送、+/- 增删 |
| 校验 | 对齐 SSCOM：末尾 1~4 字节视为帧尾、校验插在帧尾前面 / 末尾追加（旧版保留档）；算法（含"无校验"）无 / ModbusCRC16 / CCITT_CRC16 / CRC8 / ADD8 / ADD16 / XOR8，第 N 字节起算 |
| 终端仿真 | 回车后发送新行(带回显)（默认选中，对齐 SSCOM）/ 按键立即发送该键值（两方式互斥）；本地回显按键值仅按键方式可用；回车 0x0d 自动补 0x0a |
| 工具 | 进制转换（DEC/HEX/BIN/ASCII/float 大小端，V0.0.4 更名） |
| 兼容 | 一键导入原版 `sscom51.ini`（参数+多条字符串） |
| 其它 | 状态栏 TX/RX 计数（右键清零）、窗口置顶、参数持久化（**pycom32.json**，与 exe 同目录；与 PyCom 的 pycom.json 相互独立，同目录共存互不覆盖） |

## 内置增强（对应此前对原版的三项补丁需求）

1. **无广告**（天然无）
2. **编码显示**：GBK / UTF-8 / Big5 / Shift-JIS / EUC-KR / Latin-1 增量解码，跨包多字节字符不乱码
3. **大缓冲显示**：默认 5MB、菜单可调至 50M（2026-10-20 由 20M 放宽），溢出自动丢最旧；内存真耗尽时自动降档并在状态栏提示，不崩

## 新功能

1. **接收区搜索+高亮着色**：Ctrl+F 呼出；文本/HEX 模式、大小写、命中计数与上下跳转；发送=蓝色、接收=绿色着色
2. **断线自动重连**：串口拔出/网络断开按间隔自动恢复（可关），状态栏提示"重连中…"
3. **深色模式**：显示菜单切换，浅/深两套主题，着色随主题适配；Windows 标题栏、状态栏/面板立体边框等原生元素同步跟随（调色板+DWM；Qt5 无系统明暗提示 API，明暗完全由应用自身控制）

## 架构

```
main.py                入口（PyCom）
app/core/  纯逻辑+连接层：rxbuf(环形缓冲) display(增量行格式化) checksum
           serial_link/net_link(事件驱动连接+自动重连) multisend txfile terminal
           settings(JSON持久化+原版ini导入) hexutil
app/ui/    rxview(自绘接收视图，仅渲染可视区) mainwindow find_bar
           calculator_dlg theme(浅/深QSS)
tests/     pytest 单测（校验算法/分包/编码跨包/环形缓冲/ini导入）
```

性能要点：接收字节进环形缓冲，行格式化增量进行，视图只绘制可视区行（大缓冲不卡）；
30fps 合并刷新；未收尾行跨包续接；编码用增量解码器保持跨行多字节序列。

## 与原版差异

- ISP 烧写器（STM32/STC）与数据波形示波器**确定不做**（2026-10-17 定版决策；原右侧占位页签已删除）
- 界面为 Qt 风格而非 BCB 原版皮肤；布局按逆向出的 DFM 树对齐
- ADD/XOR 校验为 8 位累加/异或（原版未公开规格，如不符可调 `app/core/checksum.py`）

## 验证

- `"D:\Program Files\Python38-x32\python.exe" -m pytest tests/` → **134 项单测全通过**（32 位 Python 3.8 + PySide2/Qt5 离屏环境，2026-10-22）
- 产物审计：`python tools\audit_dist_win7.py`（单文件 exe：位数/引导器导入）+ `python tools\audit_dist_win7.py vendor\ucrt-x86`（随包 UCRT 血统）→ 均「未发现 Win7 兼容性风险项」
- 打包产物（`dist\PyCom32.exe` 单文件版）已在 Win11 开发机启动冒烟通过（2026-10-16）
- 32 位内存实测 `python tools\mem_probe.py 5 10 20`：128M/256M 档正常（峰值提交 356MB/842MB），512M 档灌到 310MB 分配失败 ⇒ 上限定 20M、默认 5M
- UDP / TCP 服务端 / TCP 客户端三模式回环实测通过
- 深浅主题、时间戳分包、编码切换、多条发送循环实测通过
- ✅ **Win7 32/64 位真机验收全部通过**（2026-10-16~17，期间发现的问题均已修复并复验）

## 文件位置

- 配置 `pycom32.json`：exe（或 main.py）同目录，点"保存参数"或关窗时写入（本分支独立命名，不与 PyCom 的 pycom.json 冲突）
- 默认日志（PyCom-Log-日期_时间.txt / received.txt / received.bin）：同目录
- 接收数据到文件：勾选即存，无对话框，文件名沿用原版规则
  `PyCom-Log-<YYYYMMDD_HHMMSS>.txt`
  内容=接收区显示的文本（含时间戳前缀，随 ASCII/HEX 显示模式），按勾选时设置的编码保存，每次勾选新建
