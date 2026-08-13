# PureVox

实时 AI 音频降噪工具 —— 降噪 / 目标说话人提取 / 回声消除，支持本地麦克风与远程网络推流。

中文 | [English](README_EN.md)

## 文档

- 📖 使用说明与更新日志均内置于软件，菜单「关于」查看（软件介绍 / Windows / Linux 使用说明 / 更新日志 / 许可证）

## 功能特性

- 🎤 实时 AI 降噪（48kHz，模型按需加载）
- 🗣️ TSE 目标说话人提取（录制参考语音后从背景中分离目标人声）
- 🔊 AEC 回声消除
- 🎛️ 31 段均衡器（EQ）
- 📊 AGC 自动增益控制 / VAD 静音检测
- 📱 远程麦克风：手机浏览器 / Android APK 经局域网推流到 PC 处理
- 🖥️ Windows（WASAPI 默认 / MME 备选）与 Linux（原生 PipeWire）双平台

## 环境要求

| 平台 | 要求 |
|---|---|
| Windows | Windows 7/10/11，Python 3.8+（Win7 详见下方说明） |
| Linux | Python 3.8+，PipeWire（音频走原生 libpipewire，虚拟麦克风为 null-sink） |

> **Windows 7**：Python 3.8 是最后一个支持 Win7 的 Python，本项目源码与其依赖均保持
> Python 3.8 兼容（模型 opset 13/14/15，均 ≤16，onnxruntime 定为 1.11.1）。GUI 栈
> **锁版本 `PySide6==6.1.3`** —— 这是最后一个支持 Win7 的 PySide6（Qt 6.2+ 官方仅
> Windows 10+）；`build_win.ps1` 打包时会从仓库固化的 x64 转发 stub 补入 onnxruntime
> 依赖的 Win10 专属 API-Set DLL（`packages/onnxruntime-win-x64-1.11.1/lib/`），并携带
> MSVC 运行库，使产物在 Win7 开箱即用（勿回退，实测见 AGENTS.md）。

## 快速开始

### 内嵌 Python 3.8（推荐，独立于系统环境）

项目可自带一份独立的 Python 3.8，与系统 Python（可能为 3.14）完全隔离，不会互相影响。
**Windows** 直接下载预编译包（NuGet）；**Linux** 因无预编译 3.8 可下载，以 **git 子模块**
`packages/cpython`（CPython@v3.8.20，浅克隆）锁定源码，由引导脚本一次性编译
（out-of-tree，不污染子模块）。产物都放在 `packages/` 下。

```bash
# 克隆后先拉子模块（含 CPython 源码）：
git submodule update --init --depth 1 packages/cpython

# Linux（运行引导脚本即可，自动编译）
./bootstrap_python38.sh          # -> packages/python38（自包含），并安装依赖
./py38 run_pyside6.py            # 启动
./py38 setup.py build_ext --inplace --force   # 编译 libaimic.so + libpvpipe.so（纯 C，gcc）

# Windows（PowerShell，NuGet 下载预编译）
powershell -ExecutionPolicy Bypass -File bootstrap_python38.ps1   # -> packages\python38w
# 之后 build_win.ps1 打包会自动使用 packages\python38w\python.exe，独立于系统 Python
```

若不想用内嵌解释器，也可直接使用系统 Python 3.8+：

```bash
# Windows 需追加 -r requirements-win.txt
pip install -r requirements.txt

python run_pyside6.py
```

### Linux（AOSC / 其它发行版）

```bash
# 系统级依赖（例：AOSC）
sudo oma install -y gcc pkgconf pipewire libpipewire-0.3-devel

# 内嵌 3.8 方式（推荐，见上）：
./bootstrap_python38.sh
./py38 setup.py build_ext --inplace --force   # 编译纯 C 共享库（libaimic.so + libpvpipe.so）
./py38 run_pyside6.py

# 或用系统 python3 直接运行：
pip install --user -r requirements.txt
python3 run_pyside6.py
```

Linux 音频基于原生 PipeWire：格式协商 F32 单声道 48000Hz，重采样与声道转换由
PipeWire 负责。虚拟麦克风是单声道 null-sink `purevox_out` 的 monitor，
其它应用可选 **"PureVox 虚拟麦克风"** 作为输入设备。AEC 远端采集（回声参考）
同样是原生 PipeWire（`stream.capture.sink` 监听扬声器输出）。

### Windows 远程麦克风附加组件

远程麦克风功能需要 Opus 解码与 VB-CABLE 虚拟声卡，二者不内置：

1. `opus.dll` —— 从 [DSharpPlus VoiceNext Natives](https://github.com/DSharpPlus/DSharpPlus/raw/master/docs/natives/vnext_natives_win32_x64.zip) 获取，解压重命名 `libopus.dll` → `opus.dll` 放入 `server/`（或系统 PATH）
2. **VB-CABLE** —— 需自行下载并安装：
   从 [vb-audio.com/Cable](https://vb-audio.com/Cable/) 下载 `VBCABLE_Setup_x64.exe`
   （或直接下载[官方驱动包](https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip)），
   双击运行安装程序，按提示完成即可。首次使用 PureVox 检测到未安装时会弹出指引（含[安装视频教程](https://www.bilibili.com/video/BV1i2bazGEKe/)）。

## 打包

### Windows（产物目录，CI 打包上传）

```powershell
powershell -ExecutionPolicy Bypass -File build_win.ps1   # 产出 dist/PureVox/（PyInstaller one-folder 产物）
```

脚本包含完整流程：编译 `aimic.dll`（mingw gcc）→ PyInstaller 打包 → tcl/tk 与无用 PySide6 模块清理 → 拷贝文档。
Windows CI 会执行同样流程，上传 `dist/PureVox/` 目录（`actions/upload-artifact` 自动压缩为 zip）。

### Linux（deb / rpm / AppImage）

```bash
bash pack_deb.sh        # 产出 dist/PureVox-Linux-x64-<yyyy-MM-dd-HHmm>-release.deb，内含源码+.so+模型+html
bash pack_rpm.sh        # 产出 dist/PureVox-Linux-x64-<yyyy-MM-dd-HHmm>-release.rpm（Fedora/RHEL）
bash pack_appimage.sh   # 产出 dist/PureVox-Linux-x64-<yyyy-MM-dd-HHmm>-release.AppImage（捆绑内嵌 Python3.8）
```

| 产物 | 命名规则 |
|---|---|
| Windows（CI 上传目录自动压缩） | `PureVox-Windows-x64-<date>-release` |
| Linux deb/rpm/AppImage | `PureVox-Linux-x64-<date>-release.<deb\|rpm\|AppImage>` |
| Android APK | `PureVox-Android-arm64-<date>-debug.apk` |

时间戳 `<date>` = `yyyy-MM-dd-HHmm`（文件名）；产物体内版本字段 = `yyyy.MM.dd.HHmm`
（如 `2026.08.10.1517`），**由 tag 名推导**（`v2026.08.10.1517` → `2026.08.10.1517`），
deb/rpm/setup.py 版本号同源一致，不随 job 运行时刻漂移。

发版：推送 tag `v<yyyy.MM.dd.HHmm>`（如 `v2026.08.10.1517`）即自动触发 CI，release job
会创建 GitHub Release 并把全部产物（deb / rpm / AppImage / Windows zip / APK）attach 上去。

### Android APK

```bash
cd android
./gradlew assembleDebug    # 输出 android/app/build/outputs/apk/debug/app-debug.apk
```

环境要求：JDK 17、Android SDK platform 34、NDK r27。首次编译需将 Opus 源码放到
`android/opus-src/`（[xiph/opus v1.5.2](https://github.com/xiph/opus/archive/refs/tags/v1.5.2.zip)）。

## 远程麦克风

手机 / 浏览器 → WSS(Opus) → PC 服务器 → AI 处理链路 → 扬声器 / 虚拟麦克风

```
手机 → https://<PC的IP>:59123（mDNS 广播 _purevox._tcp.local.）→ 降噪 → 输出
```

- 浏览器：手机与 PC 同局域网，访问 `https://<PC的IP>:59123`，信任自签名证书后点麦克风推流
- APK：打开自动搜索局域网服务器，发现即自动连接推流
- 客户端消息：`{"type":"audio","data":"<base64 opus>","seq":N}`，服务器回 `{"type":"ack","seq":N}`
- 帧大小 960 samples（20ms @48kHz），与 Opus 编码器对齐

## 项目结构

```
run_pyside6.py            # 启动入口（单实例锁）
ui_pyside6.py             # 主 UI（PySide6）：面板、设备选择、模式切换
audio_processor.py        # 核心音频引擎 + TSE 参考录音工具
aimic.c + aimic.py         # C 音频核心 → aimic.dll / libaimic.so（mingw gcc）+ ctypes 绑定
pipewire_client.c + pvpipe.py  # 原生 PipeWire 桥 → libpvpipe.so（Linux，纯 C + ctypes）
pvplatform/               # 平台抽象：audio/（设备枚举、SpeakerCapture）、system/（单实例/虚拟麦克风）
config_manager.py         # JSON 配置（旧 key 自动迁移）
model_config.py           # ONNX 模型文件名常量
dialog_about.py           # 关于对话框
dialog_eq.py              # 均衡器对话框
dialog_tse_reference.py   # TSE 参考音频录音对话框
server/                   # 远程麦克风 HTTPS/WSS 服务端（aiohttp + Opus + mDNS + TLS）
html/                     # 浏览器推流前端（AudioWorklet + Opus WASM）
android/                  # Android 客户端（Kotlin + OkHttp + Opus JNI）
pack_deb.sh               # Linux deb 打包
pack_rpm.sh               # Linux rpm 打包（Fedora/RHEL）
pack_appimage.sh          # Linux AppImage 打包（捆绑内嵌 Python 3.8）
build_win.ps1             # Windows 打包（aimic.dll + PyInstaller 产物目录）
bootstrap_python38.sh / .ps1  # 内嵌 Python 3.8 引导（Linux 编译自子模块，Windows 拉 NuGet）
setup.py                  # 纯 C 共享库构建（gcc，产出 libaimic.so + libpvpipe.so / aimic.dll）
```

## 技术栈

| 组件 | 技术 |
|---|---|
| 桌面 GUI | Python + PySide6 |
| 音频处理 | 纯 C 共享库（gcc）+ ONNX Runtime C API（频谱/FFT 全在 C 端，Python 仅 ctypes 数据搬运） |
| Linux 音频 | 原生 PipeWire（libpipewire） |
| Windows 音频 | WASAPI 全双工（默认）/ MME 备选 |
| 服务端 | Python aiohttp + zeroconf + cryptography |
| 音频编码 | Opus（PC: opuslib，APK: NDK 编译，Web: WASM） |
| Android | Kotlin + OkHttp + NsdManager + AudioRecord |

## 许可证

- **源代码**：[GPL-3.0](LICENSE)（GNU General Public License v3.0 or later）
- **内置 AI 模型**：不随 GPL 授权，归作者 a2heng 所有，禁止提取用于其他项目，
  仅可在 PureVox 内经授权使用 —— 详见 [MODEL-LICENSE.md](MODEL-LICENSE.md)

作者的 MIT 开源模型仓库（较早版本，可自由使用）：

- <https://github.com/a2heng/lightweight-denoise-48k>
- <https://github.com/a2heng/lightweight-aec-48k>

第三方组件（PySide6、ONNX Runtime、Opus 等）使用各自许可证，
见 [LICENSE-THIRD-PARTY.txt](LICENSE-THIRD-PARTY.txt)。

## 联系方式

- GitHub: <https://a2heng.github.io/>
- 哔哩哔哩: <https://space.bilibili.com/10850943>
