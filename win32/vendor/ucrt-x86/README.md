# vendor/ucrt-x86 —— Win7 专用 UCRT 随包文件（勿改内容）

这 41 个 DLL 是微软的 **Universal CRT app-local 部署集**，来源：

```
C:\Program Files (x86)\Windows Kits\10\Redist\ucrt\DLLs\x86\
```

`ucrtbase.dll` 文件版本 `10.0.10240.16384 (th1.150709-1700)`。

## 为什么要单独存一份

- Win7 系统**不带** UCRT，程序必须自己带着（Win10+ 才有系统 UCRT）；
- PyInstaller 默认会去**开发机的 `C:\Windows\System32`** 抓 `ucrtbase.dll` 与
  `api-ms-win-*.dll`。开发机是 Win11，抓到的是 10.0.26100 那份，它导入
  `api-ms-win-core-fibers-l1-1-1`、`api-ms-win-core-kernel32-legacy-l1-1-1`
  等 **Win8+ 才有的 API-Set** —— 在 Win7 上 ucrtbase 一加载就失败，程序直接起不来；
- 而 SDK 的 `Redist\ucrt\DLLs\x86` 是微软官方**面向 Win7 SP1+ 的 app-local
  再发行版**（其 api-ms-win-* 转发目标只有 ucrtbase / kernel32 / ntdll /
  advapi32，全部 Win7 自带）。
- 注意：同一个 SDK 里**按版本号分的**目录（`Redist\10.0.22621.0\ucrt\DLLs\x86`
  等）是 Win10/11 时代那份，不要拿来用；只有**不带版本号的**
  `Redist\ucrt\DLLs\x86` 是 Win7 目标版。

在 Win10+ 上运行时，系统 API-Set 会优先解析到系统自带的 UCRT，随包的这份不会
被用到（PyInstaller 官方 issue #6326 同样结论）；在 Win7 上系统没有对应 API-Set，
加载器回退到 exe 同目录，用到随包这份。两条路都正常。

## 打包时怎么用

`PyCom.spec` 会按下面顺序找 UCRT 目录，找到就把这 41 个文件作为 binaries 随包，
并**丢弃** PyInstaller 从 System32 抓来的同名文件：

1. 环境变量 `PYCOM_UCRT_DIR`
2. 本目录（`vendor/ucrt-x86`，默认走这条）
3. `%ProgramFiles(x86)%\Windows Kits\10\Redist\ucrt\DLLs\x86`

三者都找不到时 **打包直接报错退出**（宁可失败也不要产出 Win7 起不来的包）。
构建完用 `python tools/audit_dist_win7.py` 复核，脚本会检查随包 UCRT 是否还有
Win8+ 依赖、以及有没有导入解析不到的 DLL。

## 版权

这些文件是微软可再发行组件（Microsoft Software License Terms 允许随应用
app-local 分发），仅作打包随包使用，不要修改其内容。
