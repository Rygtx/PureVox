# PureVox — AI 麦克风降噪

Windows / Linux 桌面应用 + Android 客户端：实时 AI 音频降噪 / 目标说话人提取 / 回声消除，支持本地麦克风和远程网络推流。

**栈**: Python 3.8+（最低；Win7 需 3.8）+ PySide6 + 纯 C 共享库（gcc/mingw 编译，ctypes 绑定）+ ONNX Runtime（==1.11.1，模型 opset 13/14/15，均 ≤16）
**桌面入口**: `python run_pyside6.py`
**Android 入口**: `android/` — Kotlin + OkHttp + Opus JNI

---

## 维护准则（发版后管理，所有贡献者必读）

**功能最小化模型** —— 本项目的首要约束：

1. **一个功能只有一条规范实现路径**。禁止"功能 ABC 三种都能用"的设计——多套平行实现等于高维护成本。新增功能有多个可行做法时，只保留一种并写进文档，其余不进入代码。
2. **先扩展，再新建**。开新方法 / 新类 / 新文件之前，先搞清楚已有方法能否扩展：优先 改已有函数/类 → 加参数/加配置 → 复用既有抽象；确认确实无法扩展才允许新建，并在 CHANGELOG 说明为何不能扩展。
3. **被替代的实现不保留平行代码**。如 Linux 的 PortAudio/GStreamer/JACK、旧虚拟麦克风架构等已弃用方案，直接删除，不留"备选"。
4. **改动前先读对应模块，尊重既有设计意图**；删除功能需在 CHANGELOG 记录。

**本项目的单一实现路径（强制执行）**：

- Linux 音频采集/输出**只用原生 PipeWire**（`pvpipe`）；PortAudio/GStreamer/JACK 已弃用
- 虚拟麦克风只有一种：单声道 null-sink `purevox_out` 的 monitor；不用 pw-loopback、不暴露第二路 Source
- 音频格式一律 **F32 单声道 48kHz**（PipeWire 负责重采样与声道转换，模型永远拿 48k 单声道）
- 设备枚举只用 `pw-dump` 标准 introspection（`pvplatform.audio.pwpipe_client`）

---

## 运行 / 构建

**内嵌 Python 3.8（推荐，独立于系统环境）**：本项目可自带独立 Python 3.8，
与系统 Python（如 3.14）完全隔离。Windows 走 NuGet 下载预编译包；Linux 无预编译
3.8 可下，源码以 **git 子模块** `packages/cpython`（CPython@v3.8.20）锁定，
由引导脚本 out-of-tree 一次性编译。产物统一放 `packages/`。

- 克隆后先 `git submodule update --init --depth 1 packages/cpython` 拉子模块
- `./bootstrap_python38.sh`（Linux，幂等）→ 生成自包含 `packages/python38/` + 装依赖
- `./bootstrap_python38.ps1`（Windows）→ 生成 `packages\python38w\`（NuGet 完整版，含头文件/链接库）
- 内嵌解释器与系统 Python 互相独立；`packages/python38*`、`.py38-src/` 不进版本库（gitignore）

### Windows (PowerShell)

```powershell
chcp 65001
# 方式一（内嵌 3.8，推荐）：
powershell -ExecutionPolicy Bypass -File bootstrap_python38.ps1
# 方式二（系统 Python）：pip install -r requirements.txt -r requirements-win.txt
python run_pyside6.py
powershell -ExecutionPolicy Bypass -File build_win.ps1   # 打包 EXE（自动用 packages\python38w\python.exe）
# 注：Windows 侧 aimic.dll 用 mingw gcc 编译（setup.py 走 CC 或 PATH 上的 gcc，
# 链接捆绑的 onnxruntime-win-x64-1.11.1）
```

### Windows 7 兼容性（实测结论，勿回退）

纯 PySide6 6.1.3 包无法直接跑 Win7——Qt 6.2+ 官方仅 Win10+，6.6.x import 即报
`DLL load failed ... 找不到指定的程序`；**PySide6==6.1.3 是最后一个支持 Win7 的版本**
（requirements.txt 已锁死并注释原因）。另外两个 Win7 缺失项必须在打包时补：

- **API-Set 转发 DLL**：捆绑的 onnxruntime.dll 还导入 `api-ms-win-core-libraryloader-l1-2-0.dll`
  和 `api-ms-win-core-processtopology-obsolete-l1-1-0.dll`（Win8+ 的 API-Set 由内核虚拟解析，
  Win7 与其构建机 System32 均无物理文件）。仓库在
  `packages/onnxruntime-win-x64-1.11.1/lib/` 固化两个 **x64 转发 stub**（导出符号转发到
  KERNEL32；生成材料见其下 `apiset/*.def`，用 mingw `x86_64-w64-mingw32-gcc -shared`
  复现，例如 `x86_64-w64-mingw32-gcc -shared stub.c apiset/libloader.def -o
  api-ms-win-core-libraryloader-l1-2-0.dll`）。`build_win.ps1` 打包时从仓库拷这两个
  stub 进 `_internal`，勿改回"从构建机 System32 拷"。
- **MSVC 运行库**：onnxruntime 依赖 MSVCP140/VCRUNTIME140 等，`build_win.ps1` 会把构建机
  System32 的 VC runtime 拷进包，避免 Win7 需单独装 VC++ redist。

注意：四件套 wheel 名含 `abi3`；在线安装 PySide6==6.1.3 时会自动带对版本。

**`.ps1` 脚本必须纯 ASCII（英文）**：`build_win.ps1` / `bootstrap_python38.ps1`
不含中文/非 ASCII/BOM。Windows PowerShell 5.1 对无 BOM 的 UTF-8 脚本按 ANSI
(cp1252/GBK) 误读导致语法错误（`chcp 65001` 只在本机掩盖）；中文文件名
（如 `用户手册.html`）在脚本里用通配符（`*.html`）引用，不写字面量。

### Linux

依赖因发行版而异（Ubuntu / Fedora / AOSC 包名不同，参考 `.github/workflows/linux.yml` 与 README）。AOSC 示例：

```bash
sudo oma install -y gcc pkgconf pipewire libpipewire-0.3-devel
# 内嵌 3.8（推荐）：
./bootstrap_python38.sh
./py38 setup.py build_ext --inplace --force   # 产出 libaimic.so + libpvpipe.so
./py38 run_pyside6.py
bash pack_deb.sh                              # 打包 deb → dist/purevox_<ver>_amd64.deb
```

deb 布局：`/opt/purevox/` 放全部源码+libaimic.so/libpvpipe.so+模型+html+捆绑的 `libonnxruntime.so*`（1.11.1）；
`/usr/bin/purevox` 启动脚本（先导出 `LD_LIBRARY_PATH=/opt/purevox` 再 exec）。
`/usr/share/applications/purevox.desktop` + hicolor 图标。Depends 按 AOSC 包名（无 onnxruntime，
PT 已捆绑）。`server/opus.dll`
是 Windows 的，不入 deb。`.so` 为固定名 `libaimic.so`/`libpvpipe.so`（不再用 `sysconfig.EXT_SUFFIX` 定位）。
Linux 不再依赖 PyAudio/PortAudio（输入/输出/设备枚举/AEC 全原生 PipeWire）；opuslib 缺失时 `pip install --user`（写进 Recommends）。

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

### CI（`.github/workflows/`）

- `linux.yml`：分两个 job——
  - `build`（Ubuntu 22.04 / 24.04 / Fedora 容器矩阵）：装系统依赖 + gcc 编纯 C 共享库（libaimic.so + libpvpipe.so），import 冒烟测试；Ubuntu 额外跑 `pack_deb.sh` 出 deb 并上传产物。onnxruntime 用仓库内已捆绑的预编译 1.11.1 SDK，**不 pip 装 onnxruntime**
  - `python38_smoke`：官方 `python:3.8-bullseye` 容器，验证纯 C 库在最低 Python 3.8 环境可编译、可 ctypes 装载
- `windows.yml`：windows-latest + Python 3.8（与内嵌运行时一致），msys2/mingw-w64 gcc 编译 `aimic.dll`
  + 语法/导入冒烟；`package` job 走 `build_win.ps1`（PyInstaller + 7z 自解压）自动产 EXE 并上传
- `android.yml`：ubuntu-latest 编 debug APK（JDK17 + SDK 34 + NDK r27）；先下载 opus 源码到 `android/opus-src/`，产物上传 APK
- **onnxruntime 预编译 SDK（双平台统一 1.11.1）**：Windows 用捆绑 `packages/onnxruntime-win-x64-1.11.1`；Linux/macOS 默认捆绑 `packages/onnxruntime-linux-x64-1.11.1`（`include/`+`lib/`），不再依赖系统 onnxruntime 包。setup.py 仍支持 `ORT_INCLUDE_DIR` / `ORT_LIB_DIR` 环境变量覆盖（CI/pip 场景，wheel 内 .so 带版本号后缀，需先建 `libonnxruntime.so` 软链接再 `-lonnxruntime`，运行时 `LD_LIBRARY_PATH` 指向 capi 目录）

---

## 架构

### 桌面端 (Python/C)

| 模块 | 职责 |
|---|---|
| `run_pyside6.py` | 单实例锁、启动入口，导入 `ui_pyside6.run_app` |
| `ui_pyside6.py` | 主 UI（PySide6）——面板布局、设备选择、模式切换、48kHz 检测弹框 |
| `audio_processor.py` | 核心音频引擎 —— `AudioThread`(全双工流)、`SpeakerCapture`(AEC loopback)、`RingBuffer`、设备枚举、TSE 参考录音工具(`_recorder`/`load_tse_reference`/`_wsola_time_stretch`) |
| `aimic.c` → `libaimic.so`（+ `aimic.py` ctypes 绑定） | C 音频核心（`audio_processor_new`/`denoise_new`/`tse_new`/`aec_new`/STFT/频谱/RingBuffer，ONNX Runtime C API，无任何 C++） |
| `pipewire_client.c` → `libpvpipe.so`（+ `pvpipe.py` ctypes 绑定） | 原生 PipeWire 桥（Linux，纯 C）—— PwBridge，F32 单声道 48kHz 协商 |
| `aimic.py` / `pvpipe.py` | ctypes 绑定层 —— 加载 libaimic.so / libpvpipe.so，Python 类/方法名与旧 pybind11 绑定完全一致（音频热路径仅做 list↔float 数组搬运） |
| `pvplatform/` | 平台抽象层 —— `audio/`(SpeakerCapture 三端、device_api、pwpipe_client)、`system/`(单实例/自启动/防火墙/虚拟麦克风，win+posix) |
| `server/` | 远程麦克风 HTTPS/WSS 服务器 —— `https_server.py`、`audio_bridge.py`(RemoteAudioSource)、`opus_codec.py`、`mdns_publisher.py`、`tls_manager.py` |
| `config_manager.py` | JSON 配置读写，启动时迁移旧 key；api_type/output_device 平台感知默认值 |
| `model_config.py` | ONNX 模型文件名常量 |
| `dialog_about.py` / `dialog_eq.py` / `dialog_tse_reference.py` | 关于 / 均衡器 / TSE 参考录音弹框（统一 `dialog_` 前缀） |
| `html/` | 浏览器端远程推流页面 —— `index.html`、`app.js`、`audio-capture.js`、`ws-client.js`、Opus WASM 编码器 |
| `vbcable_installer.py` | VB-CABLE 驱动静默安装（仅 Windows），驱动包缺失时引导下载 |
| `build_win.ps1` / `pack_deb.sh` / `setup.py` | Windows 打包 / Linux deb 打包 / 纯 C 共享库构建（gcc，`build_ext --inplace` 产出 libaimic.so + libpvpipe.so） |

### Linux 音频架构（原生 PipeWire，强制）

数据流（本地）：麦克风源节点 → `PureVox-input` 流 → 降噪 → `PureVox-output` 流 → `purevox_out`（虚拟麦克风 sink）
监听：独立输出流 `PureVox-monitor` → 扬声器节点（同一路降噪音频）
AEC far-end：独立输入流 `PureVox-far`（`stream.capture.sink=tap 扬声器 sink 输出`，会话内创建/销毁，
无 PyAudio/PulseAudio monitor 依赖；采样率恒 48kHz 单声道）

- 格式协商 **F32 单声道 48000Hz**：PipeWire 内置重采样 + 声道转换，模型永远拿 48k 单声道，输出自动上混到目标设备声道数，不存在"一个通道一个模型 / 通道不匹配 / 采样率不齐"
- 虚拟麦克风 = `purevox_out.monitor`（单声道 48kHz，系统录音列表唯一 PureVox 项）；创建/删除走 `pvplatform.system` 的 `virtual_mic_ready()` / `ensure_virtual_mic()` / `remove_virtual_mic()`
- 关键坑：
  - 所有 pw_stream 操作必须经 `_run_on_loop` 在 PipeWire 主循环线程执行（`pw_loop_invoke` + 条件变量同步；block 参数不可靠会竞态）
  - 进程回调（数据线程）禁止加锁/分配——pvpipe 用无锁 SPSC 环形缓冲（输入环满丢最旧、输出环满丢新），Python 线程读→降噪→写（2s 缓冲吸收调度抖动），回调只搬数据
  - 永不直接打开虚拟 sink（null-sink 须经 monitor 引用，直接打开会触发 PipeWire ALSA 插件崩溃）
  - `pactl load-module` 的 `device.description` 不生效，须用 `pw-cli create-node` 设 `node.description`
  - 设备列表（pw-dump）：输入 = Audio/Source 节点 + `purevox_out.monitor`；排除 PureVox 自身流（`PureVox-*`）、幻影路由（`api.alsa.path` 未指定具体设备，如 `hw:sofhdadsp` 无 `,N`，打开也是静音）、error 状态节点；输出 = Audio/Sink 节点（扬声器 + `purevox_out`）
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
**品牌名统一为 `PureVox`**（P、V 大写）。所有用户可见文本——窗口标题、UI 文案、日志、关于对话框、README、用户手册、CHANGELOG、菜单——一律用 `PureVox`，禁止 `Purevox` / `purevox` / `PUREVOX` 等变体。

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
2. **模型规格 48kHz / 2048 NFFT / 1024 hop** — 任何缓冲区/块大小与此冲突的以此为准。
3. **配置 key 无 API 前缀** — 用 `input_device` / `output_device` / `monitor_device`，不用 `WASAPI_` 前缀。
4. **命名** — Python: snake_case 方法和变量；C++: snake_case 方法和 PascalCase 类；Kotlin: camelCase。
5. **错误处理** — 内部用 `try/except` + `_module_log()` 记录，不冒泡到 UI 线程；UI 用 `QMessageBox` / `QDialog` 提示。
6. **日志** — 统一 `logger.py` 的 `Logger` 类，层级 `dev`/`msg`/`warn`/`err`。
7. **主程序禁止依赖 numpy/torch** — 所有频谱/FFT 在 C++ 端完成；纯 Python 仅做 Qt GUI 和数据中转（`List[float]`）。
8. **Android 主题跟随系统** — `Theme.MaterialComponents.DayNight.NoActionBar`，亮色/深色自动切换。
9. **品牌拼写规约** — 品牌名一律 `PureVox`；`purevox` 全小写仅限平台/协议强制标识（见命名规范），改大小写视为破坏行为。
10. **许可证头** — 每个源码文件顶部必须带 GPL-3.0 版权头 + 模型声明 + `SPDX-License-Identifier: GPL-3.0-or-later`（照抄 `audio_processor.py` 顶部，按 `#`/`//` 注释风格替换）；新增文件也必须带。
11. **README 双语约定** — 默认中文 `README.md`，英文单独 `README_EN.md`；改文件名/平台结构/打包命令时两处必须同步，不得改名或删除。

---

## 注意事项

- **AEC SpeakerCapture**: Linux 端 AEC far 走 pvpipe `set_far(sink_name, True)`（`stream.capture.sink` tap 扬声器 sink 输出，恒 48k 单声道免重采样，会话内创建/销毁）。Windows 用 `GetMixFormat` 获取设备原生格式（WASAPI loopback 共享模式**必须用引擎 MixFormat**）；音频引擎 `audio_processor_set_aec_far_sample_rate()` 将 far-end 重采样到 48kHz。
- **网络模式缓冲**:
  - `_output_buffer`: `RingBuffer(48000)` + 预填充 `1024*3` (64ms)
  - `_network_loop TARGET_ACC`: `1024*5` (107ms), `MAX_ACC`: `1024*8` (171ms)
  - 速率补偿: 输出缓冲 >128ms 时主动丢弃多余帧
- **旧配置兼容**: `WASAPI_input_device` → `input_device` 等迁移在 `ConfigManager.load_config()` 中。

---

## 许可证

- 源码 **GPL-3.0**（SPDX: `GPL-3.0-or-later`），见 `LICENSE`
- 内置 AI 模型（`*.onnx`）**不随 GPL 授权**，归 a2heng 所有，禁止提取用于其他项目，仅随 PureVox 经授权使用 → 见 `MODEL-LICENSE.md`
- 作者另有 MIT 模型仓库可自由使用：`lightweight-denoise-48k` / `lightweight-aec-48k`（README 已写）
