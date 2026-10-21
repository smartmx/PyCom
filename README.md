# PyCom 开发工程（总入口）

**PyCom**：用 Python 从头复刻 SSCOM 5.13.1 串口/网络调试器（串口 + TCP
客户端/服务端 + UDP、收发全套、多条发送、校验、发送文件、终端仿真、转换
计算器、原版 sscom51.ini 导入），并增强：多编码显示、大缓冲、搜索高亮、
断线自动重连、深色模式、接收区鼠标选择复制。无广告、无联网。技术栈 PySide
（QtSerialPort，无 pyserial）+ PyInstaller 打包。

本目录收纳**完整的两套工程**与全部文档：

| 目录 | 应用名 | 技术栈 | 目标系统 | 版本 |
|---|---|---|---|---|
| [`win64/`](win64/) | **PyCom** | Python 3.13（64 位）+ PySide6 / Qt6 | Windows 10+ | V0.0.3 |
| [`win32/`](win32/) | **PyCom32** | Python 3.8.10（**32 位**）+ PySide2 / Qt5 | Windows 7 SP1+（32/64 位） | V0.0.3 |

`win32/` 是 `win64/` 的整树回迁副本 + 一层明确的 Qt5/32 位适配；功能只在
win64 主线开发，win32 只同步修复（同步工具见下）。

## 目录结构

```
Develop/
├─ readme.md            本文件（总入口）
├─ CHANGELOG.md         全项目更新日志（汇总两分支；win64/、win32/ 内另有分支细目）
├─ sync_check.py        win64 ↔ win32 同步核对工具（归一化 Qt5 适配，只报功能差异）
├─ docs/                文档（按最新代码重写）
│  ├─ 01-项目总览与功能规格.md     定位、双分支、原版 SSCOM 逆向规格基线、
│  │                              PyCom 功能规格、对照矩阵、界面要点
│  ├─ 02-架构与代码导读.md        模块职责、线程模型、性能路径、缓冲与行表
│  │                              裁剪设计、改码前必读的坑清单、配置持久化
│  ├─ 03-构建与打包.md            两分支环境/命令/产物；单文件版规则；spec
│  │                              裁剪清单；UCRT 血统与产物审计
│  ├─ 04-Win7-32位适配说明.md     回迁原因、代码变更清单、Qt5 运行期坑、
│  │                              32 位内存边界与缓冲定档、sync_check 用法
│  ├─ 05-测试与验收.md            测试跑法与覆盖面、产物审计、内存实测、
│  │                              真机 Win7 验收清单与取证方法
│  └─ 06-维护约定与已知限制.md    双分支维护约定、坑速查表、文件位置、
│                                 已知限制、待办路线
├─ win64/               PyCom 主线工程（源码 + 测试 + 打包配置）
└─ win32/               PyCom32 回迁工程（多 vendor/ucrt-x86、审计与内存工具）
```

每个工程目录内的 `README.md` 是该分支速览（运行/打包/验证）。

## 快速开始

```bash
# —— win64（PyCom，需 Python 3.13）——
cd win64
pip install -r requirements.txt     # 已锁版本（应用依赖只有 PySide6）
python main.py                      # 源码运行
python -m pytest tests/             # 122 项单测
tools\build_exe.bat                 # 打包 → dist\PyCom.exe（单文件，≈24MB）

# —— win32（PyCom32，需 32 位 Python 3.8.10 @ D:\Program Files\Python38-x32）——
cd win32
"D:\Program Files\Python38-x32\python.exe" -m pip install -r requirements.txt
"D:\Program Files\Python38-x32\python.exe" main.py
"D:\Program Files\Python38-x32\python.exe" -m pytest tests/     # 131 项单测
tools\build_exe.bat                 # 打包 → dist\PyCom32.exe（单文件，≈20MB，Win7 可用）
```

打包共同规则：**只出单文件版**（拷单个 exe 即可运行），打包前退出正在运行的
程序；win32 打包后必跑 `python tools\audit_dist_win7.py` 与
`python tools\audit_dist_win7.py vendor\ucrt-x86` 两条审计（期望“未发现
Win7 兼容性风险项”）。详见 [docs/03-构建与打包.md](docs/03-构建与打包.md)。

> 本目录以源码工程为本体；`win32/dist/PyCom32.exe` 是最近一次构建的交付物
> （重新打包覆盖生成）。`build/`、`__pycache__`、运行期 `pycom*.json`、
> `PyCom-Log-*.txt`、`.mimosa/` 均为生成物，发布/备份时排除，可随时删除；
> 按上面命令可一键重新构建。

## 文档索引

| 想做什么 | 看哪篇 |
|---|---|
| 了解项目定位、功能清单、与原版 SSCOM 的对照 | [01-项目总览与功能规格](docs/01-项目总览与功能规格.md) |
| 读代码、加功能、查设计坑 | [02-架构与代码导读](docs/02-架构与代码导读.md) |
| 源码运行 / 跑测试 / 打包出 exe | [03-构建与打包](docs/03-构建与打包.md) |
| 维护 Win7/32 位分支、了解 Qt5 适配与内存边界 | [04-Win7-32位适配说明](docs/04-Win7-32位适配说明.md) |
| 验证产物（审计/内存/真机 Win7 验收） | [05-测试与验收](docs/05-测试与验收.md) |
| 日常维护规则、已知限制、待办 | [06-维护约定与已知限制](docs/06-维护约定与已知限制.md) |
| 版本历史 / 变更日志 | [CHANGELOG.md](CHANGELOG.md)（总览；各工程目录内有分支细目） |

## 同步维护（win64 → win32）

主线改完代码先跑根目录 `python sync_check.py`（`-q` 看摘要）：它把 Qt5 适配
改写（PySide6→PySide2、exec→exec_、Qt6 完全限定枚举等）与注释归一化掉，
只报**真正需要手工移植的功能差异**。移植时按
[docs/04-Win7-32位适配说明.md](docs/04-Win7-32位适配说明.md) 的规则改写，
移植完重跑确认收敛；当前 9 个文件存在有意差异（缓冲档位、terminal 转义、
Qt5 菜单适配、rxview 鼠标坐标 API 等），属正常，勿“修平”。`python sync_check.py --testcount`
另行核对两分支 CHANGELOG（最新一节）/readme/docs 里的测试条数陈述与
pytest 实收是否一致——改测试忘改文档的条数漂移在此兜底（审查复发问题，
机制化消除）。

## 当前状态（2026-10-19）

- 两分支均为 V0.0.3；测试 win64 122 项 / win32 131 项全过（当日实跑）；
- 接收区右键可切换「复制/保存含时间戳和收发标记」（默认含，随 pycom.json 持久化）；
  网络 IPv4/IPv6 纯单栈（本地 IP 下拉含 :: 与各 v6 地址，选哪个族只收发哪个族）；
  鼠标选择复制 + 边缘自动滚屏、转换计算器 HEX 大小端、占用等待可取消均已入库；
- win64：功能完整，产物 `PyCom.exe` 验证可用（ISP 烧写器×2 与数据波形示波器
  确定不做，2026-10-17 定版决策，见 docs/06）；
- win32：**✅ Win7 32/64 位真机验收全部通过**（2026-10-16~17，期间真机发现
  的问题均已修复并复验）；单测 / PE 审计 / UCRT 血统 / 32 位内存实测全绿；
- 已知限制与完整待办清单见 [docs/06-维护约定与已知限制.md](docs/06-维护约定与已知限制.md)。
