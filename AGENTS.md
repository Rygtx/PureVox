# PureVox — AI 麦克风降噪

Windows / Linux 桌面应用 + Android 客户端：实时 AI 音频降噪 / 目标说话人提取 / 回声消除，支持本地麦克风和远程网络推流。

**栈**: Python 3.12+ 标准库 Tkinter（桌面 UI）+ 纯 Python 组件化音频引擎（`pvengine` 包：numpy + scipy + onnxruntime，无任何自编译二进制）+ 自研 ctypes libpulse 绑定（Linux 音频桥，系统 libpulse）
**桌面入口**: `python run_tk.py`
**Android 入口**: `android/` — Kotlin + OkHttp + Opus JNI

---

## 维护准则（发版后管理，所有贡献者必读）

**功能最小化模型** —— 本项目的首要约束：

0. **更新日志写在哪里**：每次发版/改动（新增、删除、行为变化）都在
   `about/changelog.md` **顶部**追加一条「日期 — 标题」记录。
   仓库**没有独立的根级 CHANGELOG.md / 用户手册文件**——更新日志与两份使用手册
   是关于对话框的三个 markdown 页（`about/changelog.md` / `about/windows.md` /
   `about/linux.md`，uitk 直接读文件渲染），写入时保持中文、无 emoji。
   **只写用户可感知的技术变更**（功能/修复/行为/性能/兼容面）；纯开发过程事务——
   换行符与编码归一、git 属性/钩子、CI 与打包脚本调整、目录重组、代码挪动等
   不改变产品行为的内容——**禁止写入更新日志，也禁止写进 README**。

1. **一个功能只有一条规范实现路径**。禁止"功能 ABC 三种都能用"的设计——多套平行实现等于高维护成本。新增功能有多个可行做法时，只保留一种并写进文档，其余不进入代码。
2. **先扩展，再新建**。开新方法 / 新类 / 新文件之前，先搞清楚已有方法能否扩展：优先 改已有函数/类 → 加参数/加配置 → 复用既有抽象；确认确实无法扩展才允许新建，并在更新日志（`about/changelog.md`）说明为何不能扩展。
3. **被替代的实现不保留平行代码**。如 Linux 的 PortAudio/GStreamer/JACK、旧虚拟麦克风架构等已弃用方案，直接删除，不留"备选"。
   - **例外：配置键占位不删**。`config_manager.py` / `device_api.py` 里按接口后缀写全的
     设备键（如 `input_device_wasapi` / `input_device_alsa` / …，共 10 接口 × 4 键）属于
     **跨平台共享的占位配置**，即使当前平台实际只用其中一个本地接口（Linux 只原生 PipeWire、
     Windows 只 WASAPI+MME、macOS 只 Core Audio），其余键也**保留不删**——它们不影响运行、
     是强配置结构的一部分，独立于本条的"被替代实现删除"规则；若日后清理，视作待办（TODO）
     而非本次改动目标。
4. **改动前先读对应模块，尊重既有设计意图**；删除功能需在更新日志（`about/changelog.md`）记录。
5. **legacy 历史快照冻结**。`legacy-v2026.08.20.1943/`（提交 c5972ae 的快照，音频链条化重构前的最后一个版本）是**只读历史参考**：
   禁止修改、更新、删除其中任何文件；也不得让它参与构建、CI、打包、测试或任何全局性批量改动
   （格式化、换行符归一、重命名、依赖升级等一律绕过该目录）。需要对照旧实现时只读查阅，
   修复/新功能永远改主线代码，不回写快照。

**本项目的单一实现路径（强制执行）**：

- 音频引擎为**纯 Python 组件化管线**（`pvengine/` 包）：Stage 接口（process/reset/release）
  是组件唯一契约，组件按 active_modes 声明生效模式，可随意增删替换；所有调用方直接用 pvengine。
- Linux 音频采集/输出走 **pipewire-pulse 兼容层**（`pvplatform/audio/pwpipe_client.py`，
  自研 ctypes 绑定直调系统 libpulse）。ALSA 备选接口已整体移除（2026-08-22 纯 py 迁移）。
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
  （**不编译**：下载 python-build-standalone 预编译 CPython install_only 包，
  版本锁定 cpython-3.12.14+20260814，可用 `PUREVOX_CPYTHON_TARBALL` 指定离线包）
- `./bootstrap_python312.ps1`（Windows）→ 生成 `packages\python312w\`（NuGet 完整版，含头文件/链接库）
- 内嵌解释器与系统 Python 互相独立；`packages/python312*`、`.py312-src/` 不进版本库（gitignore）
- **不再使用 git 子模块、也不再源码编译**（2026-08-25 改预编译）：bootstrap 下载
  python-build-standalone install_only 包解压即用；CI 缓存 key 固定为 pbs 包版本号；
  Linux job 系统依赖不再需要 libssl-dev/libffi-dev/zlib1g-dev/build-essential

### Windows (PowerShell)

```powershell
chcp 65001
# 方式一（内嵌 3.12，推荐）：
powershell -ExecutionPolicy Bypass -File bootstrap_python312.ps1
# 方式二（系统 Python）：pip install -r requirements-win.txt
python run_tk.py
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
./py312 run_tk.py
bash pack_deb.sh                              # deb → dist/PureVox-Linux-x64-<date>-release.deb
bash pack_rpm.sh                              # rpm → dist/PureVox-Linux-x64-<date>-release.rpm
bash pack_appimage.sh                         # AppImage → dist/PureVox-Linux-x64-<date>-release.AppImage
```

deb 布局：`/opt/purevox/` 放全部源码+模型+html+捆绑的内嵌 `python312`（含 numpy/onnxruntime/scipy 等
全部 pip 依赖，无任何自编译 .so）；`/usr/bin/purevox` 启动脚本直接 exec。
rpm（pack_rpm.sh）与 AppImage 是同一实现路径，同样捆绑内嵌 python312，Requires/无系统 Python 依赖。
`/usr/share/applications/purevox.desktop` + hicolor 图标。Depends 只留 pipewire。
Linux 输入/输出/设备枚举/AEC 全走 pipewire-pulse（libpulse 绑定桥）；opuslib 缺失时 `pip install --user`。

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

- **触发方式：push tag 触发 CI 构建 + 自动发 release 两件事**，分支 push 不触发构建（保持日常快速提交零成本）；需验证分支时可 `workflow_dispatch` 手动跑（仅触发三构建 job，不触发 release）。
- **测试工作流独立（`.github/workflows/test.yml`）**：**每次分支 push / 手动
  触发**，零打包、分钟级——compileall + 引擎冒烟 + `tests/run_all.py` 全套
  （`test_session_plan.py` 会话计划纯函数 / `test_playback_sink.py` 播放
  正确性合成测试 / `test_transport.py` 传输层优雅降级 / `test_devices.py`
  设备面[枚举/虚拟麦克风/配置键]）。运行时 = 内嵌 python312（与发行产物
  同款）；cache **只读复用**共享桶（restore 的 key 与 ci.yml 一致，**无
  save 步骤**——落盘仍由 ci.yml 单写者负责，勿在此加缓存写入）。构建
  job 里保留同款轻量测试步（守打包环境），另有产物级冒烟（见下）。
- **产物级冒烟（打包流程自检，tag 时运行）**：deb/rpm 解包后用**包内嵌
  python312** 跑引擎冒烟 + `tests/`（依赖缺装/内嵌解释器问题在此暴露；
  fedora job 的 sysdeps 因此含 `cpio`）；AppImage `--appimage-extract`
  免 FUSE 后同样处理（随 AppImage best-effort）；Windows 断言
  `dist/PureVox` 布局关键项（exe / about 三页 / models / opus.dll / html）。
- **tag 命名规则**：`v<yyyy.MM.dd.HHmm>`（如 `v2026.08.10.1517`）。tag 名同时定义产物体内版本（`v` 去掉即 `yyyy.MM.dd.HHmm`），所有 job 的产物时间戳/版本都从 `${GITHUB_REF_NAME}` 推导，避免各 job 并发时刻漂移。回复发版即 `git tag v<yyyy.MM.dd.HHmm> && git push origin <tag>`。
- `linux` job：容器矩阵只留 3 项，产出通用安装包——
  - `ubuntu-22.04`：pip 装最新 numpy/onnxruntime/scipy 等 + 引擎冒烟（加载模型跑一帧）+ `pack_deb.sh` 出 deb + `pack_appimage.sh` 出 AppImage（best-effort，捆绑内嵌 python312）
  - `fedora`：冒烟 + `pack_rpm.sh` 出 rpm
  - `python3.12`：官方 `python:3.12-bullseye`，验证纯 Python 引擎在该基线可导入、可推理
- `windows` job：windows-latest + Python 3.12 + 引擎冒烟；`build_win.ps1`（PyInstaller one-folder）出 `dist/PureVox/`，CI 上传该目录（`actions/upload-artifact` 会自动压缩为 zip，命名 `PureVox-Windows-x64-<yyyy-MM-dd-HHmm>-release`）
- `android` job：ubuntu-latest 编 debug APK（JDK17 + SDK 34 + NDK r27）；下载 opus 源码到 `android/opus-src/`，产物改名 `PureVox-Android-arm64-<yyyy-MM-dd-HHmm>-debug.apk`
- `release` job：`needs` 三构建 job + `if: startsWith(github.ref,'refs/tags/')`，tag push 时下载全部产物，Windows 目录重打成 zip（`zip -9`），`gh release create` 把 deb / rpm / AppImage / Windows zip / APK 全部 attach
- **产物命名统一**：`PureVox-<平台>-<架构>-<yyyy-MM-dd-HHmm>-<release|debug>.<ext>`（Windows 上传目录由 CI 自动压缩 / Linux deb / rpm / AppImage 一律 release，Android 为 debug）。文件名时间戳 `yyyy-MM-dd-HHmm`；产物体内版本字段 = `yyyy.MM.dd.HHmm`（如 `2026.08.10.1517`，deb control / rpm / setup.py 一致，**由 tag 名 `v<yyyy.MM.dd.HHmm>` 推导**，避免并发 job 各自 `date` 导致产物版本不一）。
- **窗口标题版本戳 `_build_version.py` 同样由 tag 推导**：`uitk/main_window.py` 顶部 `try: from _build_version import BUILD_DATE`，缺失回退「开发版」。四个打包脚本（`pack_deb.sh` / `pack_rpm.sh` / `pack_appimage.sh` / `build_win.ps1`）都在打包时把 `BUILD_DATE = "yyyy-MM-dd-HHmm"` 写入产物内的 `_build_version.py`（tag 触发取 `GITHUB_REF_NAME`，本地回退当前时间），保证窗口标题与包版本/文件名同源；该文件已在 `.gitignore`，勿提交。新增打包脚本必须照此生成。
  - **Windows(PyInstaller) 的 `_build_version.py` 无需 `--add-data`（2026-08-12 实测，入口切 run_tk 后仍成立）**：PyInstaller
    静态分析入口顶层的 `from _build_version import BUILD_DATE`，
    会把仓库根的 `_build_version.py` 当作**模块编译进 PYZ**（`dist\PureVox\_internal\` 下看不到
    独立 `.py` 文件，属正常），运行时 import 正常、窗口标题带日期。勿再加 `--add-data="_build_version.py;."`
    ——PYZ 里没有它、且会被 PyInstaller 以模块方式收集，加 add-data 只会让文件重复打包。验证方法：
    启动 `dist\PureVox\PureVox.exe` 后抓窗口标题（Win32 `EnumWindows` + `GetWindowText` 按 PID 过滤）。
    注意：`about/` 目录与此**不同**——uitk 直接 import `about_content.py`（自动进
    PYZ），但三个 markdown 页按文件路径读取，PyInstaller 必须显式
    `--add-data="about;about"`，否则关于页手册/日志缺失（build_win.ps1 已带）。
- **onnxruntime 走 pip 最新版（2026-08-22 纯 py 迁移）**：不再捆绑预编译 C SDK；
  `requirements-win.txt` / `requirements-linux.txt` 不锁版本，CI/全新环境安装即最新。模型 opset ≤18，
  onnxruntime 长期向后兼容
- **外部下载全部预置化（2026-08-22）**：`server/opus.dll`（预编译 libopus，BSD）
  直接提交进仓库（`.gitignore` 对其白名单），CI 与本地开发均不再下载；
  Linux 内嵌 python312 编译产物与 appimagetool 二进制走 `actions/cache`
  （key 分别为固定 cpython 版本号与固定版本）；Android opus 源码 zip 同样缓存。
  本地开发 `pack_appimage.sh` 复用 `~/.cache/purevox/appimagetool`，也可用
  `PUREVOX_APPIMAGETOOL` 环境变量指定
- **CI 缓存单写者 + 作用域门控（2026-08-27）**：GitHub 缓存作用域 = 触发 ref，
  tag 触发的 save 只进 tag 作用域且未来 tag 永远读不到——纯垃圾副本。故全部
  缓存统一「显式 restore 共享 + save 仅在 main 分支（手动 dispatch）落盘」：
  Linux `~/.cache/purevox` 与 Windows pip 轮子桶（`purevox-windows-pip-v1-`
  前缀 + requirements hash 键）均如此；Lite 工作流纯 restore-only。依赖变更
  后需在 main 上手动 dispatch 一次 ci.yml 暖桶，之后的 tag 全命中
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
  （`requirements-linux.txt` 不含 PyAudio，Windows 专用包只在 `requirements-win.txt`）。
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
| `run_tk.py` | 单实例锁、启动入口，导入 `uitk.main_window.MainWindowTk` |
| `uitk/` | 主 UI（纯标准库 Tkinter）——**单列节点面板**：顶部单一工具条（启动/退出 · 添加节点▾ · 清空 · 设置▾[快捷键/自动运行/开机自启/系统声音/虚拟声卡/关于]）+ PluginPanel（输入/处理/输出/可视化全部为可增删排序的节点行；排序走**拖拽手柄**）；EQ 曲线编辑器 / TSE 参考录音 / 关于页（文档标签）都在 `uitk/dialogs.py`；VB-CABLE 状态卡内嵌在虚拟输出行。设备选择在 input/output 节点行内（Linux 存 node.name）。**外观为星露谷像素浅色主题**（theme.py 单份令牌定义），无明暗切换 |
| `about_content.py` + `about/` | **关于页文本（无 GUI 依赖的单一来源）**——`about/changelog.md` 更新日志、`about/windows.md`、`about/linux.md` 使用手册（uitk 直接读文件渲染）；`about_content.py` 只存元数据（应用信息/URLS/LIBS）与介绍页、许可证页文本，打包须随包携带 `about/` |
| `session_plan.py` | **L3 会话层**——`SessionPlan.from_chain(chain_cfg)` 纯函数：链文档 → 校验后的可执行计划（inputs/outputs/remote_url/viz/fx_chain/problems/warnings）。UI 启动流程只消费计划，不做内联解析 |
| `audio_processor.py` | 核心音频线程 —— `AudioThread`(**统一处理循环** read→process→sinks.write；本地/网络同一循环)、每输出一个 `PlaybackSink`(pvengine)、后端装配(`_create_stream`：Linux=PwBridge / Windows=PaBridge，哑传输)、`SpeakerCapture`(AEC loopback)、`RingBuffer`、设备枚举、TSE 参考录音工具(`_recorder`/`load_tse_reference`/`_wsola_time_stretch`) |
| `pvengine/` | **纯 Python 组件化音频引擎**——Stage 接口（process/reset/release）是唯一契约；`components/`(denoise/aec/tse/gain/eq/vad/agc/compressor/clip/recorder/tap) 每文件一个组件、按 active_modes 声明生效模式；`dsp/`(窗/环形缓冲/重采样/**PlaybackSink 跨时钟域播放**/Mel 频谱/ONNX 会话) 可独立复用；`pipeline.py` 按序执行+模式旁路；`processor.py` 是 AudioProcessor 门面。模型：202609 三件套（denoise/aec/tse，波形 hop [1,480] 进出、STFT 在模型图内、enh_hop 滞后 1 hop），全部 numpy + onnxruntime 实现 |
| `pvplatform/` | 平台抽象层 —— `audio/`(SpeakerCapture 三端、device_api、backends[后端注册表]、pwpipe_client[ctypes libpulse 桥]、pa_backend[Windows PortAudio 桥]、media_session[miniaudio 纯媒体会话]、_libpulse[libpulse 最小绑定])、`system/`(单实例/自启动/防火墙/虚拟麦克风，win+posix) |
| `server/` | 远程麦克风 HTTPS/WSS 服务器 —— `https_server.py`、`audio_bridge.py`(RemoteAudioSource)、`opus_codec.py`、`mdns_publisher.py`、`tls_manager.py` |
| `config_manager.py` | JSON 配置读写（强配置，无迁移）；api_type 平台感知默认值、设备键按接口后缀（`<方向>_device_<接口后缀>` / `aec_far_sink_<接口后缀>`，全部接口显式写全） |
| `model_config.py` | ONNX 模型文件名常量 |
| `html/` | 浏览器端远程推流页面 —— `index.html`、`app.js`、`audio-capture.js`、`pcm-worklet.js`（AudioWorklet 采集恒 480 帧切分）、`ws-client.js`、Opus WASM 编码器 |
| `build_win.ps1` / `pack_deb.sh` / `pack_rpm.sh` / `pack_appimage.sh` | Windows 产物目录打包（PyInstaller，CI 上传自动压缩）/ Linux deb / rpm / AppImage 打包。全部产物为纯 Python（依赖随内嵌 python312 或系统环境携带），无任何自编译二进制 |

### Linux 音频架构（pipewire-pulse 兼容层 + 自研 ctypes libpulse 绑定，强制）

数据流（本地）：麦克风源 → libpulse 录制流（读回调→输入环）→ pvengine 降噪 → 每输出 PlaybackSink → libpulse 播放流（写回调按设备时钟 pull）→ `purevox_out`（虚拟麦克风 sink）
监听：独立录制流指向扬声器 monitor 源（同一路降噪音频）
AEC far-end：独立录制流指向 `far_sink.monitor`（会话内创建/销毁，恒 48kHz 单声道）

- 实现：`pvplatform/audio/_libpulse.py`（系统 libpulse 的最小 ctypes 绑定，
  `pa_threaded_mainloop` + pa_stream 读写回调）+ `pwpipe_client.py` 的
  `PwBridge`。**不用 pulsectl**——其流式 API（connect_recording 等）在
  PyPI 全版本中不存在（旧代码调的是未记录 fork，干净安装必断）。
  无任何自编译二进制。
- 时钟模型：**设备回调是唯一主时钟**。播放 = libpulse 写回调(nbytes) →
  `out_pull[i](n)`（PlaybackSink.pull，速率差由 sink 伺服消化）→ 写流；
  录制 = 读回调 → 各输入独立环形缓冲（200ms）→ 引擎线程 read(hop) 混合。
  桥内零缓冲策略，播放正确性只在 `pvengine/dsp/playback.py`。
- 格式协商 **F32 单声道 48000Hz**：PipeWire 内置重采样 + 声道转换，模型永远拿 48k 单声道，
  输出自动上混到目标设备声道数
- 虚拟麦克风（Linux 虚拟声卡）= **单一生产者 + 双出口**，实现见 `pvplatform/system/_posix.py`：
  - 生产者：单声道 null-sink `purevox_out`（`pw-cli create-node`，唯一写入口，
    `media.class=Audio/Sink`、`audio.position=[MONO]`、`object.linger=true`）。
    PureVox 降噪输出流只写入它。
  - 出口 1 `purevox_out.monitor`（monitor 源，宽口径）；出口 2 `purevox_mic`
    （真源，`module-remap-source` 重映射而来，供 OBS 等"只列真源"软件）。
  - 生命周期全幂等：`virtual_mic_ready()` → `ensure_virtual_mic()` → `remove_virtual_mic()`。
  - **启动不自动创建**：菜单「虚拟声卡」→ Tk 状态面板手动「创建/清理」。
- **禁用/踩坑**（违反任一即弄坏系统托盘/协议）：
  - `pw-loopback`：旧虚拟麦克风架构，已弃用，仅防御性 `pkill` 清残留。
  - `module-null-sink media.class=Audio/Source/Virtual` 建第二路真源：实测把
    **pipewire-pulse 协议状态弄坏**（pactl 报协议错误、plasma-pa context kaput、
    系统托盘清空）。真源必须用 `module-remap-source`。
  - **重启 pipewire-pulse "修托盘"**：plasma-pa 的 libpulse context 变 kaput、托盘清空。
  - remap-source 会强制覆盖 node.description（显示 "Remapped ... source"），set-param 改不掉。
  - **ALSA 备选接口已整体移除（2026-08-22）**：旧混合实现（输入 plughw/pulse:、
    输出经 PipeWire 原生流写 purevox_out）连同 alsa_client.c/pvalsa.py 一并删除；
    单一实现路径 = libpulse 绑定桥。历史踩坑结论（默认 source 抢占回读、snd_pcm_drain 阻塞等）
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
| `audio/AudioCapture.kt` | AudioRecord 采集 48kHz/16bit，帧大小 480 (10ms) |
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

帧大小 480 samples (10ms @48kHz) —— Opus 编码器 (JS WASM / Android JNI) 与 Python 解码器、引擎 hop 对齐。

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
2. **10ms hop 规约（全局统一时间粒度）** — 所有数据面一律按 10ms hop 前进：
   `hop = SAMPLE_RATE // 100`（48kHz → 480 样本；NFFT = 2×hop = 960），**按时间派生
   而非固定样本数**，未来多采样率/重采样时规约不变。202609 模型三件套契约与此一致
   （波形 hop 进出、STFT 在模型图内、enh_hop 滞后 1 hop）。落点清单：
   引擎 Stage 进出帧（`pvengine.context.HOP_LENGTH`，`process()` 严格校验 hop 长度）、
   平台回调块（PipeWire 桥接 `pwpipe_client.HOP`、Windows `frames_per_buffer`、
   媒体会话 `_HOP`）、桥接 FIFO 分块、网络 Opus 帧（480 样本=10ms）、Android/浏览器
   采集帧。缓冲水位（网络 acc、输出环、loopback 缓冲）取 hop 整数倍。任何新代码
   不得引入与 10ms 网格错位的固定样本块（1024/2048 等）——频谱可视化、流式解码、
   浏览器采集等一切旁路同样遵守，变换/重采样输出同样按 10ms 粒度切片。
   FFT/OLA 窗长恒为 2×hop（NFFT=960，COLA/无损重构要求，随 hop 派生，非豁免）。
3. **配置 key 按接口加后缀** — 设备键为 `<方向>_device_<接口后缀>` 与 `aec_far_sink_<接口后缀>`（如 `input_device_wasapi` / `input_device_mme` / `input_device_pulse` / `aec_far_sink_pulse`），后缀表见 `device_api.API_CONFIG_SUFFIX`；`config_manager.py` 的 `ConfigDefaults` 与 `_KEY_ORDER` 把全部接口的键**显式写全**（不做动态生成，阅读直观）；不用 `WASAPI_` 前缀，也不留无后缀的通用设备键。monitor（监听）与 AEC far 各存各的键。
3a. **推理后端（2026-08-22 起）** — 纯 Python 引擎用 onnxruntime Python 包，
   CPU 内核 dispatch 由 onnxruntime 运行时自动完成，禁止再做 AVX/SSE/NPU
   探测或编译参数干预（后端探测与恒值兼容报告接口已删除）。
4. **命名** — Python: snake_case 方法和变量；C++: snake_case 方法和 PascalCase 类；Kotlin: camelCase。
5. **错误处理** — 内部用 `try/except` + `_module_log()` 记录，不冒泡到 UI 线程；Tk UI 用 `messagebox` 提示。
6. **日志** — 统一 `logger.py` 的 `Logger` 类，层级 `dev`/`msg`/`warn`/`err`。
7. **DSP 全部收敛在 `pvengine/`** — numpy/scipy/onnxruntime 只允许出现在 pvengine 包内
   （组件 + dsp 基础件）；GUI 层（uitk）与平台层（pvplatform）不做信号处理，
   仅搬运 `List[float]` / numpy 帧。新增音频功能 = 新增一个 Stage 组件，不改管线骨架。
8. **Android 主题跟随系统** — `Theme.MaterialComponents.DayNight.NoActionBar`，亮色/深色自动切换。
9. **品牌拼写规约** — 品牌名一律 `PureVox`；`purevox` 全小写仅限平台/协议强制标识（见命名规范），改大小写视为破坏行为。
10. **许可证头** — 每个源码文件顶部必须带 GPL-3.0 版权头 + 模型声明 + `SPDX-License-Identifier: GPL-3.0-or-later`（照抄 `audio_processor.py` 顶部，按 `#`/`//` 注释风格替换）；新增文件也必须带。
11. **README 双语约定** — 默认中文 `README.md`，英文单独 `README_EN.md`；改文件名/平台结构/打包命令时两处必须同步，不得改名或删除。
12. **弹框集中在 `uitk/dialogs.py`** — 桌面端独立弹框（关于/EQ 编辑器/TSE 录音等）一律放
    `uitk/dialogs.py`，入口函数走 `open_*` / `show_*` 命名；不得在仓库根重建 `dialog_*.py` 平行实现。

---

## 注意事项

- **AEC SpeakerCapture**: Linux 端 AEC far 走 `PwBridge.set_far(sink_name, True)`（监听 `far_sink.monitor` 源，恒 48k 单声道免重采样，会话内创建/销毁）。Windows 用 WASAPI loopback 采集扬声器（共享模式**必须用引擎 MixFormat**）；音频引擎 `set_aec_far_sample_rate()` 将 far-end 重采样到 48kHz。
- **播放时钟域（2026-09 重构，勿回退）**: 设备回调是唯一主时钟；全部输出路
  （主输出/额外输出/网络输出/媒体从设备）各持一个 `pvengine.dsp.playback.PlaybackSink`
  （PI 伺服 ASRC ±3% + 预热 + 欠载静音重同步 + 封顶丢最旧），速率差/调度抖动
  由 sink 消化。**禁止在任何回调里写缓冲策略**（垫零/丢帧/复用上一帧/手写
  重采样均为平行实现）；播放正确性只在 playback.py 一处，合成测试见
  `tests/test_playback_sink.py`（CI 冒烟运行）。
- **网络模式缓冲**（未做低延迟优化，目标以稳为主，不追求最小延迟；水位全部按
  `HOP_LENGTH`=10ms 派生，ms 数即准确值）:
  - `_network_reader acc`: 目标 `HOP_LENGTH*5` (50ms)，硬顶 `HOP_LENGTH*8` (80ms，突发兜底截断)
  - 速率补偿: 稳态漂移由 PlaybackSink 伺服连续消化，acc 侧不再做 drop/pad
- **强配置（无迁移）**: `ConfigManager.load_config` 不做旧配置迁移，只保留已知键；
  旧 `WASAPI_*` / 通用设备键一律丢弃回退默认。设备键为带接口后缀的
  `<方向>_device_<接口后缀>`（如 `input_device_wasapi`、`input_device_mme`）。
- **设备列表刷新单一入口**：Tk 走 `MainWindowTk.refresh_devices()`——后台线程枚举，
  严禁 UI 线程同步枚举。
  触发点仅两个：程序启动、点击「启动/停止音频处理」。运行中引擎占着
  PyAudio，扫描会失败——**禁止**在下拉展开（`DarkCombo.on_open` 已随此决策移除）、
  弹框回调等其它时机触发枚举。
  新增触发点必须接到同一入口，禁止自建第二套枚举刷新逻辑。

### 长时间运行稳定性观察（2026-08-10 走查 + 2026-08-22 纯 py 迁移后复核）

- **viz 内存隐患已根治（2026-08-22）**：旧 C 版 `process_pipeline` 无条件向 viz 缓冲
  追加且只增不减（~1.4GB/小时）。纯 py 版 viz 改为 `BufferTapStage`：有界上限丢最旧 +
  仅在 `process_pipeline` 内临时启用，本地路径零开销，泄漏不可能再发生。
- **无数值溢出/延迟累积（安全）**：环形缓冲游标单调递增、水位阈值夹牢；AGC/EQ/压缩器
  状态皆为有界信号值。网络模式 acc 硬顶 80ms；各输出 sink 水位封顶 300ms。
- **播放时钟域已收敛（2026-09 重构）**：此前 5 条播放路径 4 种时钟策略
  （全双工内联处理/主输出帧长硬对齐/额外输出手写 ASRC/网络 drop+pad/Linux
  无节奏 push），速率差反复成病；现全部收敛到后端哑插件 + 唯一
  PlaybackSink（合成测试可验证：±2% 速率差、抖动、断流、突发均不连续有界）。
- **事件型弱点（继承自旧架构，待办）**：libpulse 流无 core error/lost 监听与
  自动重连；运行中 USB 拔插/PipeWire 重启 → 对应流失败、桥接静默失效
  （统一循环 ~2s 健康探测会退出线程走会话重启路径，但无流级自动恢复）。
  自动重连留作 TODO。

---

## 许可证

- 源码 **GPL-3.0**（SPDX: `GPL-3.0-or-later`），见 `LICENSE`
- 内置 AI 模型（`*.onnx`）**不随 GPL 授权**，归 a2heng 所有，禁止提取用于其他项目，仅随 PureVox 经授权使用 → 见 `MODEL-LICENSE.md`
- 作者另有 MIT 模型仓库可自由使用：`lightweight-denoise-48k` / `lightweight-aec-48k`（README 已写）

