# PureVox — AI 麦克风降噪

Windows / Linux 桌面应用 + Android 客户端：实时 AI 音频降噪 / 目标说话人提取 / 回声消除，支持本地麦克风和远程网络推流。

**栈**: Python 3.12+ + PySide6 + 纯 Python 组件化音频引擎（`pvengine` 包：numpy + scipy + onnxruntime，无任何自编译二进制）+ pulsectl（Linux 音频桥，ctypes 系统 libpulse）
**桌面入口**: `python run_pyside6.py`
**Android 入口**: `android/` — Kotlin + OkHttp + Opus JNI

---

## 维护准则（发版后管理，所有贡献者必读）

**功能最小化模型** —— 本项目的首要约束：

0. **更新日志写在哪里**：每次发版/改动（新增、删除、行为变化）都在
   `dialog_about.py` 的 `CHANGELOG_TEXT`（内嵌快照）顶部追加一条「日期 — 标题」记录。
   仓库**没有独立的 CHANGELOG.md / 用户手册文件**——用户手册与更新日志全部内嵌于
   `dialog_about.py`（「关于」对话框整页标签展示），写入时保持中文、无 emoji。
   **只写用户可感知的技术变更**（功能/修复/行为/性能/兼容面）；纯开发过程事务——
   换行符与编码归一、git 属性/钩子、CI 与打包脚本调整、目录重组、代码挪动等
   不改变产品行为的内容——**禁止写入更新日志，也禁止写进 README**。

1. **一个功能只有一条规范实现路径**。禁止"功能 ABC 三种都能用"的设计——多套平行实现等于高维护成本。新增功能有多个可行做法时，只保留一种并写进文档，其余不进入代码。
2. **先扩展，再新建**。开新方法 / 新类 / 新文件之前，先搞清楚已有方法能否扩展：优先 改已有函数/类 → 加参数/加配置 → 复用既有抽象；确认确实无法扩展才允许新建，并在更新日志（`dialog_about.py` 的 `CHANGELOG_TEXT`）说明为何不能扩展。
3. **被替代的实现不保留平行代码**。如 Linux 的 PortAudio/GStreamer/JACK、旧虚拟麦克风架构等已弃用方案，直接删除，不留"备选"。
   - **例外：配置键占位不删**。`config_manager.py` / `device_api.py` 里按接口后缀写全的
     设备键（如 `input_device_wasapi` / `input_device_alsa` / …，共 10 接口 × 4 键）属于
     **跨平台共享的占位配置**，即使当前平台实际只用其中一个本地接口（Linux 只原生 PipeWire、
     Windows 只 WASAPI+MME、macOS 只 Core Audio），其余键也**保留不删**——它们不影响运行、
     是强配置结构的一部分，独立于本条的"被替代实现删除"规则；若日后清理，视作待办（TODO）
     而非本次改动目标。
4. **改动前先读对应模块，尊重既有设计意图**；删除功能需在更新日志（`dialog_about.py` 的 `CHANGELOG_TEXT`）记录。

**本项目的单一实现路径（强制执行）**：

- 音频引擎为**纯 Python 组件化管线**（`pvengine/` 包）：Stage 接口（process/reset/release）
  是组件唯一契约，组件按 active_modes 声明生效模式，可随意增删替换；`aimic.py` 仅是
  兼容垫片（re-export pvengine），旧调用方零改动。
- Linux 音频采集/输出走 **pipewire-pulse 兼容层**（`pvplatform/audio/pwpipe_client.py`，
  pulsectl 经 ctypes 调系统 libpulse）。ALSA 备选接口已整体移除（2026-08-22 纯 py 迁移）。
- 虚拟麦克风（Linux）= 单一生产者 + 双出口，全部健康，详见下方「Linux 音频架构」：
  ① 单声道 null-sink `purevox_out`（唯一写入口）；② 内置 monitor
  `purevox_out.monitor`（宽口径源）+ 非 monitor 真源 `purevox_mic`
  （`module-remap-source` 把 monitor 重映射而来，供 OBS 等"只列真源"软件）。
  不用 pw-loopback。**禁建第二路源用 `module-null-sink media.class=Audio/Source/Virtual`
  ——实测会把 pipewire-pulse 协议搞坏（pactl 报协议错误、plasma-pa context kaput、
  系统托盘清空，仅重启 pipewire-pulse 恢复）**，健康方案是 module-remap-source
- 音频格式一律 **F32 单声道 48kHz**（PipeWire 负责重采样与声道转换，模型永远拿 48k 单声道）
- 设备枚举只用 `pw-dump` 标准 introspection（`pvplatform.audio.pwpipe_client`）

---

## 运行 / 构建

**内嵌 Python 3.12（推荐，独立于系统环境）**：本项目可自带独立 Python 3.12，
与系统 Python 完全隔离。Windows 走 NuGet 下载预编译包；Linux 源码由引导脚本
**按需下载官方 CPython@v3.12.11 tarball**（一次性，缓存于 `~/.cache/purevox`，
可用 `PUREVOX_CPYTHON_TARBALL` 指定离线包）后 out-of-tree 一次性编译。
产物统一放 `packages/`。

- `./bootstrap_python312.sh`（Linux，幂等）→ 生成自包含 `packages/python312/` + 装依赖
- `./bootstrap_python312.ps1`（Windows）→ 生成 `packages\python312w\`（NuGet 完整版，含头文件/链接库）
- 内嵌解释器与系统 Python 互相独立；`packages/python312*`、`.py312-src/` 不进版本库（gitignore）
- **不再使用 git 子模块**（2026-08-23 移除 packages/cpython）：CI 缓存 key 固定为 cpython 版本号

### Windows (PowerShell)

```powershell
chcp 65001
# 方式一（内嵌 3.12，推荐）：
powershell -ExecutionPolicy Bypass -File bootstrap_python312.ps1
# 方式二（系统 Python）：pip install -r requirements.txt -r requirements-win.txt
python run_pyside6.py
powershell -ExecutionPolicy Bypass -File build_win.ps1   # 打包产物目录 dist/PureVox/（自动用 packages\python312w\python.exe）
```

**`.ps1` 脚本必须纯 ASCII（英文）**：`build_win.ps1` / `bootstrap_python312.ps1`
不含中文/非 ASCII/BOM。Windows PowerShell 5.1 对无 BOM 的 UTF-8 脚本按 ANSI
(cp1252/GBK) 误读导致语法错误（`chcp 65001` 只在本机掩盖）；脚本须引用中文
文件名时用通配符（`*.html`）匹配，不写字面量。

### Linux

依赖因发行版而异（参考 `.github/workflows/ci.yml` 与 README）。AOSC 示例：

```bash
sudo oma install -y python3 pipewire
# 内嵌 3.12（推荐）：
./bootstrap_python312.sh
./py312 run_pyside6.py
bash pack_deb.sh                              # deb → dist/PureVox-Linux-x64-<date>-release.deb
bash pack_rpm.sh                              # rpm → dist/PureVox-Linux-x64-<date>-release.rpm
bash pack_appimage.sh                         # AppImage → dist/PureVox-Linux-x64-<date>-release.AppImage
```

deb 布局：`/opt/purevox/` 放全部源码+模型+html+捆绑的内嵌 `python312`（含 numpy/onnxruntime/scipy 等
全部 pip 依赖，无任何自编译 .so）；`/usr/bin/purevox` 启动脚本直接 exec。
`/usr/share/applications/purevox.desktop` + hicolor 图标。Depends 只留 pipewire。
Linux 输入/输出/设备枚举/AEC 全走 pipewire-pulse（pulsectl）；opuslib 缺失时 `pip install --user`。

### Android

```powershell
$env:ANDROID_HOME = "D:\Android\Sdk"; $env:ANDROID_SDK_ROOT = "D:\Android\Sdk"
$env:ANDROID_NDK_HOME = "D:\Android\Sdk\ndk\27.0.12077973"
cd android
.\gradlew.bat assembleDebug    # 输出 android/app/build/outputs/apk/debug/
.\gradlew.bat installDebug     # 安装到设备
```

要求：JDK 17、SDK platform 34、NDK 27、CMake 3.22.1。首次编译需 Opus 源码放到
`android/opus-src/`（gitignore，JNI CMake 引用该路径）。

### CI（`.github/workflows/ci.yml`，精简为通用包 deb/rpm/appimage + 产物目录 + apk）

- **触发方式：push tag 触发 CI 构建 + 自动发 release 两件事**，分支 push 不触发（保持日常快速提交零成本）；需验证分支时可 `workflow_dispatch` 手动跑（仅触发三构建 job，不触发 release）。
- **tag 命名规则**：`v<yyyy.MM.dd.HHmm>`（如 `v2026.08.10.1517`）。tag 名同时定义产物体内版本（`v` 去掉即 `yyyy.MM.dd.HHmm`），所有 job 的产物时间戳/版本都从 `${GITHUB_REF_NAME}` 推导，避免各 job 并发时刻漂移。回复发版即 `git tag v<yyyy.MM.dd.HHmm> && git push origin <tag>`。
- `linux` job：容器矩阵只留 3 项，产出通用安装包——
  - `ubuntu-22.04`：pip 装最新 numpy/onnxruntime/scipy 等 + 引擎冒烟（加载模型跑一帧）+ `pack_deb.sh` 出 deb + `pack_appimage.sh` 出 AppImage（best-effort，捆绑内嵌 python312）
  - `fedora`：冒烟 + `pack_rpm.sh` 出 rpm
  - `python3.12`：官方 `python:3.12-bullseye`，验证纯 Python 引擎在该基线可导入、可推理
- `windows` job：windows-latest + Python 3.12 + 引擎冒烟；`build_win.ps1`（PyInstaller one-folder）出 `dist/PureVox/`，CI 上传该目录（`actions/upload-artifact` 会自动压缩为 zip，命名 `PureVox-Windows-x64-<yyyy-MM-dd-HHmm>-release`）
- `android` job：ubuntu-latest 编 debug APK（JDK17 + SDK 34 + NDK r27）；下载 opus 源码到 `android/opus-src/`，产物改名 `PureVox-Android-arm64-<yyyy-MM-dd-HHmm>-debug.apk`
- `release` job：`needs` 三构建 job + `if: startsWith(github.ref,'refs/tags/')`，tag push 时下载全部产物，Windows 目录重打成 zip（`zip -9`），`gh release create` 把 deb / rpm / AppImage / Windows zip / APK 全部 attach
- **产物命名统一**：`PureVox-<平台>-<架构>-<yyyy-MM-dd-HHmm>-<release|debug>.<ext>`（Windows 上传目录由 CI 自动压缩 / Linux deb / rpm / AppImage 一律 release，Android 为 debug）。文件名时间戳 `yyyy-MM-dd-HHmm`；产物体内版本字段 = `yyyy.MM.dd.HHmm`（如 `2026.08.10.1517`，deb control / rpm / setup.py 一致，**由 tag 名 `v<yyyy.MM.dd.HHmm>` 推导**，避免并发 job 各自 `date` 导致产物版本不一）。
- **窗口标题版本戳 `_build_version.py` 同样由 tag 推导**：`ui_pyside6.py` 顶部 `try: from _build_version import BUILD_DATE`，缺失回退「开发版」。四个打包脚本（`pack_deb.sh` / `pack_rpm.sh` / `pack_appimage.sh` / `build_win.ps1`）都在打包时把 `BUILD_DATE = "yyyy-MM-dd-HHmm"` 写入产物内的 `_build_version.py`（tag 触发取 `GITHUB_REF_NAME`，本地回退当前时间），保证窗口标题与包版本/文件名同源；该文件已在 `.gitignore`，勿提交。新增打包脚本必须照此生成。
  - **Windows(PyInstaller) 的 `_build_version.py` 无需 `--add-data`（2026-08-12 实测）**：PyInstaller
    静态分析 `ui_pyside6.py`/`run_pyside6.py` 顶层的 `from _build_version import BUILD_DATE`，
    会把仓库根的 `_build_version.py` 当作**模块编译进 PYZ**（`dist\PureVox\_internal\` 下看不到
    独立 `.py` 文件，属正常），运行时 import 正常、窗口标题带日期。勿再加 `--add-data="_build_version.py;."`
    ——PYZ 里没有它、且会被 PyInstaller 以模块方式收集，加 add-data 只会让文件重复打包。验证方法：
    启动 `dist\PureVox\PureVox.exe` 后抓窗口标题（Win32 `EnumWindows` + `GetWindowText` 按 PID 过滤）。
- **onnxruntime 走 pip 最新版（2026-08-22 纯 py 迁移）**：不再捆绑预编译 C SDK；
  `requirements.txt` 不锁版本，CI/全新环境安装即最新。模型 opset ≤18，
  onnxruntime 长期向后兼容
- **外部下载全部预置化（2026-08-22）**：`server/opus.dll`（预编译 libopus，BSD）
  直接提交进仓库（`.gitignore` 对其白名单），CI 与本地开发均不再下载；
  Linux 内嵌 python312 编译产物与 appimagetool 二进制走 `actions/cache`
  （key 分别为固定 cpython 版本号与固定版本）；Android opus 源码 zip 同样缓存。
  本地开发 `pack_appimage.sh` 复用 `~/.cache/purevox/appimagetool`，也可用
  `PUREVOX_APPIMAGETOOL` 环境变量指定
- **Linux 的 opus**：opuslib 经 `ctypes.util.find_library('opus')` 加载**系统**
  libopus——deb Depends 带 `libopus0`、rpm Requires 带 `opus`；AppImage 从构建机
  拷贝 `libopus.so*` 进包并经 AppRun 注入 `LD_LIBRARY_PATH`。缺库时
  `opus_codec.OPUS_AVAILABLE=False` 优雅降级（仅网络推流解码不可用），主程序正常
- **Linux job 按发行版分开是刻意设计，勿合并成一个 job**（2026-08-10 决策）：deb 在
  Ubuntu、rpm 在 Fedora 产出，是因为 rpm 打包须依赖 `rpmbuild` 与真实 Fedora 包名解析，
  移到 Ubuntu 上构建可靠性下降；分开还有并行收益与故障隔离
  （一个发行版坏不掉其他产物）。AppImage 在 ubuntu job 内（best-effort，
   `continue-on-error: true`），捆绑内嵌 python312——该 job 需先装 `libssl-dev`
  （否则编译出的 CPython 无 ssl 模块，pip 无网络，`bootstrap_python312.sh` 失败）
  与 `file`（appimagetool 打包必需），并确保 `PyAudio` 不在 Linux 依赖里
  （已移到 `requirements-win.txt`，否则编译缺 `portaudio.h` 让 AppImage 静默失败）。
- **CI 踩坑（实测细节补充，避免重踩）**：
  - 容器 job 在 checkout 前先装系统依赖（含 `git`）——REST API 下载不支持 submodules；
    cpython 子模块已移除（2026-08-23），bootstrap 按需下载 tarball
  - appimagetool 容器无 FUSE → 用 `--appimage-extract-and-run`；`.desktop` 要在
    AppDir 根目录放一份；图标用 `assets/icons/audio_icon_base.png`
    直接生成 256/512 png
  - `pack_deb.sh` 末尾 `| head` 会 SIGPIPE(141) 使 `sh -e` 退出 → 补 `|| true`
  - Ubuntu 容器 pip 装 pillow 遇到匹配版本时用 `--break-system-packages` 兜底
    （`||` 回退普通安装），不再 `pip install --upgrade`
  - Android JNI `CMakeLists.txt` 注释必须用 `#`（CMake 不认 `//`）；`gradlew`
    无执行位，构建前 `chmod +x`
  - Windows pwsh 无 `\` 行继续符

---

## 架构

> **顶层设计与规范见 `DESIGN.md`**（分层架构/节点模型/数据流不变量/SessionPlan 契约/
> 扩展指南）。本节是模块速查；实现与 DESIGN.md 冲突时以 DESIGN.md 为准。

### 桌面端 (Python)

| 模块 | 职责 |
|---|---|
| `run_pyside6.py` | 单实例锁、启动入口，导入 `ui_pyside6.run_app` |
| `ui_pyside6.py` | 主 UI（PySide6）——**单列节点面板**：顶部单一工具条（启动/退出 · 添加节点▾ · 清空 · 设置▾[原菜单栏并入：快捷键/自动运行/开机自启/系统声音/虚拟声卡/关于]）+ PluginPanel（输入/处理/输出/可视化全部为可增删排序的节点行，三级形态 toggle/inline/expand，viz 行内嵌实时控件；排序走**拖拽手柄**，无上下移按钮）；48kHz 检测弹框保留。设备选择在 input/output 节点行内（Linux 存 node.name）。**外观为单一墨黑深色主题**（theme_colors.py 单份定义 + 系统 accent 高亮），无明暗切换 |
| `session_plan.py` | **L3 会话层**——`SessionPlan.from_chain(chain_cfg)` 纯函数：链文档 → 校验后的可执行计划（inputs/outputs/remote_url/viz/fx_chain/problems/warnings）。UI 启动流程只消费计划，不做内联解析 |
| `audio_processor.py` | 核心音频线程 —— `AudioThread`(全双工流/PipeWire 循环/网络循环/**多输入混音+多输出扇出**[Windows extras 回调])、`SpeakerCapture`(AEC loopback)、`RingBuffer`、设备枚举、TSE 参考录音工具(`_recorder`/`load_tse_reference`/`_wsola_time_stretch`) |
| `pvengine/` | **纯 Python 组件化音频引擎**——Stage 接口（process/reset/release）是唯一契约；`components/`(denoise/aec/tse/gain/eq/vad/agc/compressor/clip/recorder/tap) 每文件一个组件、按 active_modes 声明生效模式；`dsp/`(窗/STFT/环形缓冲/重采样/Mel 频谱/ONNX 会话) 可独立复用；`pipeline.py` 按序执行+模式旁路；`processor.py` 是 AudioProcessor 门面（保持旧 API）。模型：v9 降噪（spec [1,1025,1,2] + enc/dec/tfa/inter 四态）、aec9、tse15，全部 numpy + onnxruntime 实现 |
| `aimic.py` | 兼容垫片 —— re-export pvengine（AudioProcessor/RingBuffer/Resampler/compute_spectrum 等），旧调用方零改动；新代码请直接用 pvengine |
| `pvplatform/` | 平台抽象层 —— `audio/`(SpeakerCapture 三端、device_api、pwpipe_client[纯 py pulsectl 桥])、`system/`(单实例/自启动/防火墙/虚拟麦克风，win+posix) |
| `server/` | 远程麦克风 HTTPS/WSS 服务器 —— `https_server.py`、`audio_bridge.py`(RemoteAudioSource)、`opus_codec.py`、`mdns_publisher.py`、`tls_manager.py` |
| `config_manager.py` | JSON 配置读写（强配置，无迁移）；api_type 平台感知默认值、设备键按接口后缀（`<方向>_device_<接口后缀>` / `aec_far_sink_<接口后缀>`，全部接口显式写全） |
| `model_config.py` | ONNX 模型文件名常量 |
| `dialog_about.py` | 关于对话框（单一菜单「关于」打开，整页标签）—— 介绍 / Windows 使用说明 / Linux 使用说明 / 更新日志 / 许可证，内容全部内嵌 py（中文，无 emoji）。**更新日志的唯一维护位置就是本文件的 `CHANGELOG_TEXT`**：无独立 CHANGELOG.md 文件，发版/改动时在快照顶部追加 |
| `dialog_eq.py` / `dialog_tse_reference.py` | 均衡器 / TSE 参考录音弹框（统一 `dialog_` 前缀） |
| `html/` | 浏览器端远程推流页面 —— `index.html`、`app.js`、`audio-capture.js`、`ws-client.js`、Opus WASM 编码器 |
| `dialog_vbcable_check.py` | VB-CABLE 虚拟声卡检测面板（仅 Windows；只检测不自动安装。统一结构：状态灯 + 双端点说明[CABLE Input 接 PureVox 输出 / CABLE Output 作虚拟麦克风，均 48kHz] + 驱动卡片[打开控制面板/下载/教程]）。检测开关默认开启，但**仅未安装才弹框**，取消勾选即跳过不再提示 |
| `dialog_virtual_mic_linux.py` | Linux 虚拟声卡状态面板 —— 指示灯 + 双出口说明 + 手动「创建/清理」（启动不自动创建，`ensure_virtual_mic`/`remove_virtual_mic` 全幂等） |
| `build_win.ps1` / `pack_deb.sh` / `pack_rpm.sh` / `pack_appimage.sh` | Windows 产物目录打包（PyInstaller，CI 上传自动压缩）/ Linux deb / rpm / AppImage 打包。全部产物为纯 Python（依赖随内嵌 python312 或系统环境携带），无任何自编译二进制 |

### Linux 音频架构（pipewire-pulse 兼容层 + pulsectl，强制）

数据流（本地）：麦克风源 → pulsectl 录制流 → pvengine 降噪 → pulsectl 播放流 → `purevox_out`（虚拟麦克风 sink）
监听：独立录制流指向扬声器 monitor 源（同一路降噪音频）
AEC far-end：独立录制流指向 `far_sink.monitor`（会话内创建/销毁，恒 48kHz 单声道）

- 实现：`pvplatform/audio/pwpipe_client.py` 的 `PwBridge` 用 **pulsectl**（ctypes 到系统
  libpulse，走 pipewire-pulse 兼容层）——每条流独占一个线程 + 一个 Pulse 连接
  （libpulse 主循环有线程亲和性），录制/播放回调搬运到内部 `_Ring` 缓冲，
  Python 线程 read()/write() 消费。无任何自编译二进制。
- 格式协商 **F32 单声道 48000Hz**：PipeWire 内置重采样 + 声道转换，模型永远拿 48k 单声道，
  输出自动上混到目标设备声道数
- 虚拟麦克风（Linux 虚拟声卡）= **单一生产者 + 双出口**，实现见 `pvplatform/system/_posix.py`：
  - 生产者：单声道 null-sink `purevox_out`（`pw-cli create-node`，唯一写入口，
    `media.class=Audio/Sink`、`audio.position=[MONO]`、`object.linger=true`）。
    PureVox 降噪输出流只写入它。
  - 出口 1 `purevox_out.monitor`（monitor 源，宽口径）；出口 2 `purevox_mic`
    （真源，`module-remap-source` 重映射而来，供 OBS 等"只列真源"软件）。
  - 生命周期全幂等：`virtual_mic_ready()` → `ensure_virtual_mic()` → `remove_virtual_mic()`。
  - **启动不自动创建**：菜单「虚拟声卡」→ `dialog_virtual_mic_linux.py` 状态面板手动「创建/清理」。
- **禁用/踩坑**（违反任一即弄坏系统托盘/协议）：
  - `pw-loopback`：旧虚拟麦克风架构，已弃用，仅防御性 `pkill` 清残留。
  - `module-null-sink media.class=Audio/Source/Virtual` 建第二路真源：实测把
    **pipewire-pulse 协议状态弄坏**（pactl 报协议错误、plasma-pa context kaput、
    系统托盘清空）。真源必须用 `module-remap-source`。
  - **重启 pipewire-pulse "修托盘"**：plasma-pa 的 libpulse context 变 kaput、托盘清空。
  - remap-source 会强制覆盖 node.description（显示 "Remapped ... source"），set-param 改不掉。
  - **ALSA 备选接口已整体移除（2026-08-22）**：旧混合实现（输入 plughw/pulse:、
    输出经 PipeWire 原生流写 purevox_out）连同 alsa_client.c/pvalsa.py 一并删除；
    单一实现路径 = pulsectl。历史踩坑结论（默认 source 抢占回读、snd_pcm_drain 阻塞等）
    不再适用。
- 设备列表（pw-dump）：Linux **按声卡枚举设备**——一个声卡有多个接口各对应真实设备
  （数字麦 Mic1 / 模拟麦 Mic2 / 扬声器 / HDMI 等）。PureVox 自身输入 = Audio/Source 物理
  麦克风（排除 PureVox-* 流、purevox* 虚拟源[对外输出，选它当输入会回授]、error 死节点）。
  **禁止按 api.alsa.path 无 `,dev` 把板载卡接口当"假设备"排除**。输出 = Audio/Sink
  节点（扬声器 + `purevox_out`）
- VU 电平显示**降噪输出峰值**（`_pw_loop` 里取 `out`，勿改成输入 `data`）
- UI 下拉框直接显示节点名（node.name），真实节点名存 userData，读下拉框一律走 `_combo_value()`

### Android 端 (Kotlin)

| 模块 | 职责 |
|---|---|
| `MainActivity.kt` | 主界面 —— 服务器发现、连接、推流控制、VU 显示、调试信息、RTT 追踪 |
| `audio/AudioCapture.kt` | AudioRecord 采集 48kHz/16bit，帧大小 960 (20ms) |
| `audio/OpusEncoder.kt` | JNI 调用 native opus 编码 |
| `network/WsClient.kt` | OkHttp WebSocket 客户端，base64 Opus 推流，ack RTT 追踪 |
| `network/TlsHelper.kt` | 自签名证书信任 |
| `discovery/MdnsDiscovery.kt` / `SubnetScanner.kt` | mDNS 发现 + 子网扫描备用 |
| `service/StreamService.kt` | 前台服务保活 + WakeLock |
| `VuMeterView.kt` | 自定义 VU 表绘制 |

### 网络推流协议

```
浏览器/Android → WSS → Python 服务器 → audio_processor pipeline → 扬声器

客户端 JSON: {"type":"audio","data":"<base64 opus>","seq":N,"timestamp":T}
服务器 ACK:  {"type":"ack","seq":N}
服务器 API:  GET /api/status → {"sample_rate":48000, "active_clients":N, ...}
```

帧大小 960 samples (20ms @48kHz) —— Opus 编码器 (JS WASM / Android JNI) 与 Python 解码器对齐。

---

## 命名规范

### 品牌名
**品牌名统一为 `PureVox`**（P、V 大写）。所有用户可见文本——窗口标题、UI 文案、日志、关于对话框、README、更新日志、菜单——一律用 `PureVox`，禁止 `Purevox` / `purevox` / `PUREVOX` 等变体。

### 代码内部标识符
- 含品牌名的 Python 类/标识符统一 `PureVox...`（如 `PureVoxServer`）
- 其它标识符遵循工程约定第 4 条（Python snake_case、C++ PascalCase、Kotlin camelCase）

### 平台强制小写（非品牌变体，勿改）
各处用户可见/系统标识中的 `Purevox` 变体已统一为 `PureVox`（注册表 Run 键、防火墙规则名、单实例 Mutex 名、发行产物 `PureVox.exe`/`PureVoxMic.apk`、WakeLock `PureVoxMic:AudioWakeLock`、settings.gradle rootProject、README/手册/CSS 注释）。以下标识属**平台/协议强制小写**，改小写会破坏功能或违背平台惯例：
- Android 包名 `com.purevox.mic`（Java 包名惯例 + JNI 函数名 `Java_com_purevox_mic_*` 必须与包名逐字符匹配，含 `namespace`/`applicationId`/布局类引用）
- mDNS 服务类型 `_purevox._tcp.local.`（DNS SRV 按 RFC 小写约定）
- 用户数据目录 `~/.purevox/`、日志名 `purevox_*.log`、CA 证书 `purevox-ca.crt`（POSIX 小写路径惯例）
- 模型代号 `purevox9`（内部模型代号）
- 浏览器 localStorage key `purevox_mic_id` / `purevox_theme`
- JNI/CMake 内部名（`purevox_opus_jni`、opus_jni.c 的 native 函数，随包名）

---

## 工程约定

1. **所有设备强制 48kHz** — 启动前逐设备检测，失败弹框阻止，不做重采样或半双工回退。
   - **Windows 下 WASAPI 严格、MME 宽松是刻意的，勿"修"**（2026-08-13 实测）：
     WASAPI 共享模式锁死设备 MixFormat，MixFormat=44.1k 的设备请求 48k 即
     `paInvalidSampleRate (-9997)` 弹框阻止——这是对的，硬上会在建流时失败；
     MME 是 WDM 旧接口，驱动内部自动重采样，44.1k 硬件也能以 48k 打开并正常出声
     （PureVox 侧始终处理 48k，转换由 MME 驱动完成，合规），所以 gate 对 MME
     天然放行不弹框。两者行为差异不是 bug，不要给 MME 加严格 48k 限制。
     判定依据：设备 `defaultSampleRate=44100` 时 WASAPI 弹框、MME 正常。
2. **模型规格 48kHz / 2048 NFFT / 1024 hop** — 任何缓冲区/块大小与此冲突的以此为准。
3. **配置 key 按接口加后缀** — 设备键为 `<方向>_device_<接口后缀>` 与 `aec_far_sink_<接口后缀>`（如 `input_device_wasapi` / `input_device_mme` / `input_device_pulse` / `aec_far_sink_pulse`），后缀表见 `device_api.API_CONFIG_SUFFIX`；`config_manager.py` 的 `ConfigDefaults` 与 `_KEY_ORDER` 把全部接口的键**显式写全**（不做动态生成，阅读直观）；不用 `WASAPI_` 前缀，也不留无后缀的通用设备键。monitor（监听）与 AEC far 各存各的键。
3a. **推理后端（2026-08-22 起）** — 纯 Python 引擎用 onnxruntime Python 包，
   CPU 内核 dispatch 由 onnxruntime 运行时自动完成，无需也不应再做 AVX/SSE/NPU
   探测或编译参数干预。`AudioProcessor.backend_effective/reason` 保留为兼容报告
   （恒报 AVX/OK），UI 启动日志照常打印。
4. **命名** — Python: snake_case 方法和变量；C++: snake_case 方法和 PascalCase 类；Kotlin: camelCase。
5. **错误处理** — 内部用 `try/except` + `_module_log()` 记录，不冒泡到 UI 线程；UI 用 `QMessageBox` / `QDialog` 提示。
6. **日志** — 统一 `logger.py` 的 `Logger` 类，层级 `dev`/`msg`/`warn`/`err`。
7. **DSP 全部收敛在 `pvengine/`** — numpy/scipy/onnxruntime 只允许出现在 pvengine 包内
   （组件 + dsp 基础件）；GUI 层（ui_pyside6/dialog_*）与平台层（pvplatform）不做信号处理，
   仅搬运 `List[float]` / numpy 帧。新增音频功能 = 新增一个 Stage 组件，不改管线骨架。
8. **Android 主题跟随系统** — `Theme.MaterialComponents.DayNight.NoActionBar`，亮色/深色自动切换。
9. **品牌拼写规约** — 品牌名一律 `PureVox`；`purevox` 全小写仅限平台/协议强制标识（见命名规范），改大小写视为破坏行为。
10. **许可证头** — 每个源码文件顶部必须带 GPL-3.0 版权头 + 模型声明 + `SPDX-License-Identifier: GPL-3.0-or-later`（照抄 `audio_processor.py` 顶部，按 `#`/`//` 注释风格替换）；新增文件也必须带。
11. **README 双语约定** — 默认中文 `README.md`，英文单独 `README_EN.md`；改文件名/平台结构/打包命令时两处必须同步，不得改名或删除。
12. **弹框/检测面板文件统一 `dialog_` 前缀** — 独立弹框一律 `dialog_*.py`（如 `dialog_about.py`、`dialog_eq.py`、`dialog_tse_reference.py`、`dialog_vbcable_check.py`）；新增弹框模块必须遵循此前缀，不得用 `*_check.py` / `*_dialog.py` 等变体。

---

## 注意事项

- **AEC SpeakerCapture**: Linux 端 AEC far 走 `PwBridge.set_far(sink_name, True)`（监听 `far_sink.monitor` 源，恒 48k 单声道免重采样，会话内创建/销毁）。Windows 用 WASAPI loopback 采集扬声器（共享模式**必须用引擎 MixFormat**）；音频引擎 `set_aec_far_sample_rate()` 将 far-end 重采样到 48kHz。
- **网络模式缓冲**（未做低延迟优化，目标以稳为主，不追求最小延迟）:
  - `_output_buffer`: `RingBuffer(48000)` + 预填充 `1024*3` (64ms)
  - `_network_loop TARGET_ACC`: `1024*5` (107ms), `MAX_ACC`: `1024*8` (171ms)
  - 速率补偿: 输出缓冲 >128ms 时主动丢弃多余帧
- **强配置（无迁移）**: `ConfigManager.load_config` 不做旧配置迁移，只保留已知键；
  旧 `WASAPI_*` / 通用设备键一律丢弃回退默认。设备键为带接口后缀的
  `<方向>_device_<接口后缀>`（如 `input_device_wasapi`、`input_device_mme`）。

### 长时间运行稳定性观察（2026-08-10 走查 + 2026-08-22 纯 py 迁移后复核）

- **viz 内存隐患已根治（2026-08-22）**：旧 C 版 `process_pipeline` 无条件向 viz 缓冲
  追加且只增不减（~1.4GB/小时）。纯 py 版 viz 改为 `BufferTapStage`：有界上限丢最旧 +
  仅在 `process_pipeline` 内临时启用，本地路径零开销，泄漏不可能再发生。
- **无数值溢出/延迟累积（安全）**：环形缓冲游标单调递增、水位阈值夹牢；AGC/EQ/压缩器
  状态皆为有界信号值。网络模式 acc 硬顶 171ms、输出缓冲 >128ms 即丢。
- **事件型弱点（继承自旧架构，待办）**：pulsectl 流无 core error/lost 监听与自动重连；
  运行中 USB 拔插/PipeWire 重启 → 对应流线程异常退出、桥接静默失效。
  「重启」类手动恢复路径可用，自动重连留作 TODO。

---

## 许可证

- 源码 **GPL-3.0**（SPDX: `GPL-3.0-or-later`），见 `LICENSE`
- 内置 AI 模型（`*.onnx`）**不随 GPL 授权**，归 a2heng 所有，禁止提取用于其他项目，仅随 PureVox 经授权使用 → 见 `MODEL-LICENSE.md`
- 作者另有 MIT 模型仓库可自由使用：`lightweight-denoise-48k` / `lightweight-aec-48k`（README 已写）

