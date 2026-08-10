# 更新日志

## 2026-08-10 — 开源重构定稿（08-07 起的最终状态，中间过程已压缩）

> 08-07 开始的 CI/构建/兼容/音频架构重构均已定稿；被后续推翻的中间实现
> （pybind11 绑定、旧 onnxruntime 版本、`module-null-sink` 虚拟麦克风、VB-CABLE
> 自动安装等）不再重复记录，这里只保留最终生效的状态。

### 版本号与发版（2026-08-10 追加）

- **版本号由构建时间驱动改为 tag 驱动**：deb/rpm 包内 `Version` 与 `setup.py` version
  不再写死 `1.0.x`。tag 触发 CI 时直接从 tag 名推导 `yyyy.MM.dd.HHmm`
  （`v2026.08.10.1517` → `2026.08.10.1517`），文件名字段转 `yyyy-MM-dd-HHmm`（含
  `pack_appimage.sh` 与 APK 改名）；
  各 job 并发跑不再各自 `date`，杜绝产物文件名/版本号互相漂移不一致。本地/手动跑
  （无 `GITHUB_REF_NAME` tag）才回退到构建时刻。
- **push tag 自动发 release**：`ci.yml` 新增 `release` job——推送 tag
  `v<yyyy.MM.dd.HHmm>`（如 `v2026.08.10.1517`）时，三个构建 job 针对该 ref 重跑，
  随后自动 `gh release create` 并把全部产物 attach 到 Release（Linux deb/rpm/AppImage、
  Windows 目录重打成 zip、Android APK）。tag 命名与包内版本号对齐，二者来自同一来源。
- **CI 触发收紧**：只有 push tag（`v*`）才跑 CI，分支 push 不再触发；
  需验证分支用 `workflow_dispatch` 手动跑。日常快速提交零 CI 成本。

### 构建与运行时

- **内嵌 Python 3.8（独立于系统环境）**：Linux `bootstrap_python38.sh`（git 子模块
  `packages/cpython`@v3.8.20 out-of-tree 编译，自带 `py38` 启动器）；Windows
  `bootstrap_python38.ps1`（NuGet 预编译包 → `packages\python38w\`）
- **纯 C 共享库 + ctypes 取代 pybind11**：`aimic.c` → `libaimic.so`/`aimic.dll`
  （pffft + libsamplerate + ONNX Runtime C API）、`pipewire_client.c` → `libpvpipe.so`
  （无锁 SPSC + pthread）；绑定层 `aimic.py`/`pvpipe.py`。项目不再含任何 C++/pybind11
- **ONNX Runtime 统一捆绑 1.11.1**（win/linux 预编译 SDK，不 pip 安装）；setup.py
  保留 `ORT_INCLUDE_DIR`/`ORT_LIB_DIR` 覆盖；运行时 `LD_LIBRARY_PATH` 注入
- **Windows `aimic.dll` mingw 构建打通**：`windows.h` 后按平台 `#undef far/near`
  （win 头 16 位空宏吞 `far`/`near` 参数名导致编译失败）；setup.py Windows 分支链接
  捆绑 ORT import lib；`.so`/`.dll` 用固定名定位（不再用 `sysconfig.EXT_SUFFIX`）
- 打包：`build_win.ps1`（PyInstaller one-folder → 产物目录 `dist/PureVox/`）、
  `pack_deb.sh`、`pack_rpm.sh`、`pack_appimage.sh`
- **CI 精简为单一 `.github/workflows/ci.yml`**：linux（ubuntu → deb + AppImage
  best-effort / fedora → rpm / python3.8 最低运行时冒烟）、windows（mingw 编
  `aimic.dll` + 打包上传）、android（debug APK，JDK17+SDK34+NDK27）；actions 全部
  升级 node24
- **产物命名统一**：`PureVox-<平台>-<架构>-<yyyy-MM-dd-HHmm>-<release|debug>.<ext>`
  （`pack_rpm.sh` 产物名与 CI 上传 glob 由同一 `$PKG_FILE` 变量驱动，杜绝上次 glob
  不匹配导致 fedora job 挂）
- **AppImage 修复**：ubuntu job 装 `libssl-dev`（内嵌 CPython 编译 ssl 模块否则 pip
  无网络）与 `file`；desktop/icon 副本补 `$APPDIR` 根目录；`PyAudio` 移入
  `requirements-win.txt`（Linux 原生 PipeWire 不需要，此前 Linux 编译缺
  `portaudio.h` 让 AppImage 打包静默失败）
- **CI 踩坑沉淀**（多轮 CI/容器实测累积，勿白踩）：
  - 容器 checkout：先在 checkout 前装系统依赖（含 `git`），REST API 下载不支持
    submodules；仅 AppImage job 拉 `packages/cpython` 子模块（fedora/python3.8 不拉）
  - AppImage：appimagetool 容器无 FUSE → 用 `--appimage-extract-and-run`；`.desktop`
    须同时放一份到 AppDir 根目录；图标由 `audio_icon_base_on_1024.png` 直接生成
    256/512 png（勿走 ico 转换，避免 `Icon not found`）
  - `pipewire_client.c`：勿 `#include <spa/param/audio/raw-utils.h>`（老 spa /
    bullseye 没有该头）；`PW_KEY_TARGET_OBJECT` 是 0.3.64 才引入，老版本需回退
    `node.target`
  - `pack_deb.sh` 末尾 `dpkg-deb --info | head` 触发 SIGPIPE(141) 让 `sh -e` 退出
    → 补 `|| true`
  - Ubuntu 容器 pip 装 pillow 遇到匹配版本时用 `--break-system-packages` 兜底
    （`||` 回退普通安装），不再 `pip install --upgrade`；`python3-setuptools`
    由 apt/dnf 装机（sysdeps 里）供 setup.py
  - Android：JNI `CMakeLists.txt` 版权头必须用 `#` 注释（CMake 不认 `//`，否则
    Parse error）；`gradlew` 仓库中无执行位，构建前先 `chmod +x`
  - Windows：pwsh 没有 `\` 行继续符（compileall 多行命令被拆行执行）→ 写单行；
    `aimic.dll` 编译统一走 `build_win.ps1` 一条路（独立 Build 步骤已删，不再维护
    `-SkipC` 双路径）

### Windows 7 兼容（实测结论，勿回退）

- **PySide6 锁 `6.1.3`**（最后一个支持 Win7；Qt 6.2+ 官方仅 Win10+，6.6.x import
  即报 `DLL load failed`）；四件套 wheel 含 `abi3`
- 打包时补 Win7 缺失 DLL：两个 **API-Set 转发 stub**（仓库固化 x64，导出符号转发到
  KERNEL32，mingw 可复现）+ **MSVC 运行库**（MSVCP140/VCRUNTIME140 等），Win7 无需
  单独装 VC++ redist
- **瘦身禁删 `Qt6Qml.dll`/`Qt6Quick.dll`**：`pyside6.abi3.dll`（所有 Qt*.pyd 的链接
  目标）硬依赖 `Qt6Qml.dll`，删除则 Win7 启动即报模块缺失；只允许删 import 闭包外的
  `Qt6Pdf.dll`/`Qt6DataVisualization.dll`
- **深色主题判定一律用调色板亮度**（`theme_colors.is_dark_current()`），禁
  `styleHints().colorScheme()`（Qt 6.5+ API，6.1.3 没有）

### Linux 音频架构（原生 PipeWire）

- Linux 输入/输出/设备枚举/AEC 全部原生 PipeWire，移除 Linux 端 PyAudio 路径
  （TSE 参考音频播放除外）；格式一律 **F32 单声道 48kHz**
- **AEC far-end**：独立 `PureVox-far` 流（`stream.capture.sink` tap 扬声器 sink，
  恒 48k 单声道免重采样，会话内创建/销毁）
- **虚拟麦克风定稿（单一生产者 + 双出口，全健康）**：
  生产者 = 单声道 null-sink `purevox_out`（`pw-cli create-node`，唯一写入口）；
  出口 1 = 内置 monitor `purevox_out.monitor`（宽口径，普通软件直接选用）；
  出口 2 = `module-remap-source` 重映射真源 `purevox_mic`（`media.class=Audio/Source`、
  无 monitor_of，供 OBS 等"只列真源"软件）。生命周期 `virtual_mic_ready()` /
  `ensure_virtual_mic()` / `remove_virtual_mic()` 全幂等；**启动不再自动创建**，
  菜单「虚拟声卡」→ `dialog_virtual_mic_linux.py` 手动创建/清理
- **本地输出流低延迟**（唯一优化过的延迟路径，网络模式不受益）：
  `create_stream` 显式 `SPA_PARAM_Buffers`(4096B=1024 样本) → 输出流缓冲 12288→1024
  样本（256ms→21ms）；`RING_CAPACITY` 收窄到 4 hop(4096/85ms) 封顶，输入环稳态 ~0
- 设备枚举修正 USB 麦克风误判幻影路由（`device.bus=usb` 直接放行）；VU 电平显示
  降噪输出峰值
- **禁用/踩坑（违反即弄坏系统托盘/协议）**：禁 `module-null-sink
  media.class=Audio/Source/Virtual`（弄坏 pipewire-pulse）、禁 `pw-loopback`（旧架构，
  仅防御性 pkill）、禁重启 pipewire-pulse 修托盘（KDE 不自动重连）、禁 PortAudio
  直开 null-sink（ALSA 插件堆崩溃）

### Windows 虚拟声卡

- **VB-CABLE 改为手动安装**：`dialog_vbcable_check.py` 纯检测弹框（不再自动安装，
  给官方驱动包下载链接 + B站教程，状态指示灯）；复选框「检测虚拟麦克风」默认开启，
  取消即跳过不再提示（config `vbcable_check_enabled`）

### UI / 配置 / 规约

- 修 `SegmentedControl` 模式按钮在 PySide6 6.6 点击无响应（参数推断错误）→
  `lambda *_args, v=val`；系统声音面板改 `subprocess.Popen` 异步打开（不再超时杀进程）；
  cryptography 锁 `==42.0.8`（py<3.9 线，46.x 起拆出 `cryptography_rust.dll` Win7 缺件崩）；
  `open_sound_panel`/`list_sources` 等平台修正
- AGENTS.md 新增工程约定：C 源码禁止中文（第 12 条）、弹框文件统一 `dialog_` 前缀
  （第 13 条）；全部源码头注释更新 GPL-3.0 + 模型声明（`MODEL-LICENSE.md`）

---

## 2026-07-31 — TSE模型 tse15_stream_ep_0673

- 更新 TSE 模型文件，替换为新版本 tse15_stream_ep_0673.onnx

---

## 2026-07-30 — TSE模型 tse15 & 网络串流更新

- 更新 TSE 模型文件，替换为新版本 tse15_stream_ep_0350.onnx
- 更新安卓APK和web串流UI和效果
- 新增压缩效果(测试)

---

## 2026-07-29 — 降噪模型 purevox9 & TSEv15 & 网络串流更新

- 更新 purevox9 模型文件，替换为新版本 v9_fft2048_band256_epoch_261.onnx
- 更新 TSE 模型文件，替换为新版本 tse15_stream_ep_0081.onnx(测试版)

---
## 2026-07-21 — 降噪模型 purevox9

- 重写v9降噪模型，测试版初版发布，仅供测试不代表最终品质，修复purevox9低频噪音问题，提升任意频段的说话时噪音消除能力
- “菜单栏”新增手动主题设置，默认为“系统”，可手动指定为“白天/黑夜”

- **WASAPI 全双工采样率修复**：非 48K 设备回退半双工 + libsamplerate 重采样，修复异常声音（详见 docs/wasapi-full-duplex-samplerate.md）

---

## 2026-07-20 — 录音倒数 & AEC v9

- 录音按钮点击后显示 3→2→1 倒数，倒数结束才开始录音
- 录音/播放按钮加宽，视觉更平衡
- 播放中按钮变"停止"，可点击中断
- 录音完成后自动加载最新参考音频，无需手动切模式
- 前增益数值标签留更多空间，避免挤在一起
- VU 电平表刻度字号缩小，右侧留白增加
- 优化内存占用，模型按需加载
- AEC 升级为独立模型 v9-ep483，不再依赖降噪模块，回声消除效果大幅提升，能更好去除音响声、保留人声
- 降噪升级至 v9，去除噪音能力略有减弱，后续可考虑在 AEC 后串联降噪进一步优化

---

## 2026-07-19 — 架构精简 & 按需加载

- 删除 post_gain（后增益），处理链路统一为 `前增益(AGC替代) → EQ → clip → [AEC] → 降噪 → [TSE] → clip → VAD → 输出`
- 四种模式：直通 / 降噪 / AEC / TSE（录音改为 TSE 子功能）
- 模型按需加载/释放：降噪、TSE、AEC 仅在对应模式下加载，切换时自动释放
- EQ 按需：全 0 时跳过处理
- 配置白名单：移除 model_name、aec_enabled、DirectSound 设备等废弃项
- UI 清理：删除 qtawesome 依赖，全局样式精简
- 关于对话框：使用说明更新为四模式，Qt 页改为 LGPL v3 标准免责声明

---

## 2026-07-17 — MME 驱动修复

- MME 模式下消除机器音，自动适配设备真实采样率
- 启动日志显示设备索引，方便排查

---

## 2026-07-16 — V8 模型 + 频谱图 & 均衡器

- V8 降噪模型
- 频谱直方图：128段 Mel 实时降噪前后对比
- 61段均衡器：7组预设，可视化曲线拖拽
- 修复模式切换闪退、EQ 拖拽崩溃

---

## 2026-07-03 — V6 模型 + VU 优化

- V6 降噪模型：人声保留提升，突发噪音识别减弱
- VU 电平表：Peak 峰值，-20/-9dB 分界，16ms 刷新
- 托盘暂停 UI 定时器，窗口恢复自动重启
- HTML/Android 客户端 VU 同步更新

---

## 2026-07-02 — 网络串流 + 高质量重采样

- 网页端远程麦克风（HTTPS+WSS）
- Android App 局域网连接，Opus 编解码
- mDNS 自动发现，一键热点
- 防火墙自动放行
- 高质量重采样（44.1k/48k 等）
- 设备热插拔自动刷新
- 修复网络音频变调、半双工卡顿、端口冲突等

---

## 2026-06-28 — 局域网远程麦克风

- 手机 App / 浏览器作为远程麦克风
- 局域网自动发现，断网重连

---

## 2026-06-27 — VU 电平表 + 文件处理

- VU 实时电平表
- 文件降噪导出

---

## 2026-06-13 — PySide6 重构

- 直通 / 降噪 / AEC / TSE / 录音 五种模式
- tkinter → Qt (PySide6)
- AEC 回声消除，AGC 自动增益
- 睡眠唤醒自动重连

---

## 2025-10-09 — Windows 原生音频

- WASAPI/MME，即插即用

---

## 2025-08-13 — 正式版

- 降噪效果提升，延迟更低

---

## 2025-07-23 — 2.0 测试版

- 48kHz 采样率，降噪提升

---

## 2025-04-19

- 增益优化，静默启动，多 DPI 适配

## 2025-04-08

- 设备按 ID 识别，不再因顺序变化选错

## 2025-04-02

- 开机自启

## 2025-03-29

- 增益 -20~+30dB，配置自动保存