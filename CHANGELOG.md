# 更新日志

## 2026-08-08 — 修复 Python 3.14→3.8 兼容问题（PySide6 6.6 / 依赖 / 面板进程）

- 修复 `SegmentedControl` 模式按钮在 PySide6 6.6（内嵌 Python 3.8）下**点击无响应**：
  3.14 系统 Python 配的是 PySide6 6.11，而内嵌 3.8 装到 6.6；6.6 对
  `clicked.connect(lambda checked, v=val: ...)`（必选参数+默认参数）的参数个数推断
  错误，回调收不到 `checked` 抛 `TypeError`，四个模式按钮全部失效；改为
  `lambda *_args, v=val: ...`，兼容 6.6 与 6.11（主题菜单 `triggered` 同改）
- 修复 Linux 打开系统声音面板「打开一秒就退」：`open_sound_panel_posix` 用
  `subprocess.run(..., timeout=2.0)` 等待 GUI 进程退出，超时即杀掉面板；改用
  `subprocess.Popen` 异步启动不等待不杀进程
- cryptography 与 py3.8 兼容：47.x 起弃用 Python 3.8、48 移除；`requirements.txt`
  对 py3.8 上限 `<47`（自动解析 46.x），3.9+ 不受限（环境标记 `python_version < '3.9'`）

---

## 2026-08-08 — CI 对齐纯 C + 内嵌 Python 3.8 + 捆绑 onnxruntime 1.11.1

- `linux.yml` 重写：build job 用仓库内捆绑的 1.11.1 SDK（`LD_LIBRARY_PATH` 指向
  `packages/onnxruntime-linux-x64-1.11.1/lib`），**不 pip 装 onnxruntime**；三发行版
  （Ubuntu 22.04/24.04 + Fedora）gcc 编纯 C 库 + import 冒烟，Ubuntu 额外出 deb 并上传
- 新增 `python38_smoke` job（官方 `python:3.8-bullseye` 容器）验证最低 3.8 环境
  可 gcc 编译纯 C 库、可 ctypes 装载
- `windows.yml` 重写：Windows 侧 mingw C 构建仍待接入，CI 改跑纯 Python 语法/导入
  冒烟检查；EXE 打包 job 限 `workflow_dispatch` 手动触发
- `android.yml` 保持（JDK 17 + SDK 34 + NDK r27 + opus 源码下载出 debug APK）
- AGENTS.md / README（中英同步）更新 CI 说明

---

## 2026-08-08 — AEC 模式 UI：监听禁用改为手动 far 端选择

- AEC 模式下原「监听（耳返）」行变为静态「AEC」状态标签：复选框勾选且禁用
  （仅作状态展示，不再控制耳返），监听功能在 AEC 模式期间被禁用
- 该行下拉框改为 **手动 far 端设备选择**：可指定实际在出声的扬声器 sink
  作为 AEC 回声参考，而非默认第一个物理 sink；默认仍未配置时用
  `speaker_sink_name()` 物理扬声器兜底（排除 `purevox_out`）
- 新增配置 `aec_far_sink`（node.name，门卫模式白名单）；`AudioThread` 新增
  `set_aec_far_sink()` 运行时切换（先停后开 re-tap），`_handle_monitor_changed`
  与 `start_processing` 按 AEC 模式分流；切走 AEC 后恢复原有监听行
- 说明：监听行在 AEC 下退化为状态展示，符合"一个功能一条实现路径"（AEC far 只走
  原声 PipeWire `PureVox-far` 流），不新增平行实现

---

## 2026-08-08 — 全部 C++ 移除：pybind11 绑定 → 纯 C 共享库 + ctypes（Linux/gcc）

- 删除 `aimic_bind.cpp`（pybind11 薄绑定）与 `pipewire_client.cpp`（C++ PwBridge），
  项目从此没有任何 C++ 与 pybind11
- 新增 `pipewire_client.c` → `libpvpipe.so`（纯 C，gcc）：SPSCRing 换用 GCC `__atomic`
  内置实现无锁 SPSC 环，`std::thread`/`std::function`/`condition_variable` 全部改
  pthread（`pthread_create` + 条件变量同步 `_run_on_loop`），行为与旧 C++ 完全一致
- 新增 `aimic.py` / `pvpipe.py` ctypes 绑定层：加载 `libaimic.so` / `libpvpipe.so`，
  类名/方法名/返回语义与旧 pybind11 绑定完全一致（`AudioProcessor`/`TseProcessor`/
  `AecProcessor`/`Resampler`/`RingBuffer`、`PwBridge`、`compute_spectrum` 等）；
  `aimic.py` 加载前先按捆绑 1.11.1 SDK 路径预加载 `libonnxruntime.so`（满足 DT_NEEDED）
- `setup.py` 重写为纯 gcc 构建：`build_ext --inplace` 产出固定名 `libaimic.so`
  （aimic.c + pffft + libsamplerate，链接 onnxruntime）+ `libpvpipe.so`
  （pipewire_client.c，pkg-config 链 libpipewire-0.3）；保留 `ORT_INCLUDE_DIR`/`ORT_LIB_DIR`
  覆盖（CI/pip）；Python 3.8+ 均可用
- 说明（为何不保留 pybind）：ctypes 对暴露 C API 的纯 C 库是唯一的绑定路径，接口面与
  旧绑定一致（`List[float]` ↔ `float*`），音频热路径仅做数组搬运；这是功能最小化模型的
  强制性迁移，非新增平行实现
- `pack_deb.sh` / `.github/workflows/linux.yml` / `requirements.txt` / AGENTS.md /
  README（中英同步）更新：移除 pybind11，.so 用固定名 `libaimic.so`/`libpvpipe.so`
  定位（不再用 `sysconfig.EXT_SUFFIX`）
- 注：Windows 侧 `aimic.dll` 的 mingw 构建改造与 `build_win.ps1`/`windows.yml` 待接入

---

## 2026-08-08 — Linux AEC far-end 迁到原生 PipeWire（移除 Linux 端 PyAudio 全部路径）

- `pvpipe` PwBridge 新开第 4 条流 `PureVox-far`（`set_far`/`read_far`）：以 PipeWire
  `stream.capture.sink` 语义 tap 扬声器 sink 输出作为 AEC far-end，不依赖预先存在的
  `.monitor` 源节点与 PulseAudio/PyAudio 设备枚举
- `speaker_capture_linux.py` 重写为原生 PipeWire 实现（复用已有 PwBridge，恒 F32 单声道
  48kHz 免重采样环境）；`create_speaker_capture` 工厂新增 `pw_bridge`/`far_sink` 参数
- `audio_processor._create_stream` Linux 强制只走 PipeWire：删掉 Linux 端 PortAudio/PyAudio
  回退（网络输入模式输出早已走 PwBridge）；`pyaudio`（ui/engine）改为可选导入
- `pack_deb.sh`：Depends/Recommends 移除 `portaudio`/`python3-pyaudio`（Linux 不再需要）
- 说明：TSE 参考音频「播放」按钮仍走 PyAudio（Linux 无 portaudio 时优雅降级为播放失败），
  待后续专项迁移

---

## 2026-08-08 — 音频核心迁移（aimic.c + aimic_bind.cpp）并统一捆绑 ONNX Runtime 1.11.1

- ONNX Runtime 由捆绑 `onnxruntime-win-x64-1.24.4` 统一换成预编译 **1.11.1**（AGENTS 钉 ==1.11.1）：
  Windows `packages/onnxruntime-win-x64-1.11.1` + Linux/macOS `packages/onnxruntime-linux-x64-1.11.1`
  （恰好 `include/`+`lib/` 两平台同构）；不再依赖系统 onnxruntime，`aimic` 链接 `libonnxruntime.so.1.11.1`，
  运行时由 `py38` 启动器注入 `LD_LIBRARY_PATH`，setup.py 仍保留 `ORT_INCLUDE_DIR`/`ORT_LIB_DIR` 覆盖（CI/pip 场景）
- 旧 pybind11 绑定 `aimic.cpp` 依赖 C++ 扩展 API `Session::GetOutputNames()`（仅新 ORT ≥1.20），
  编不过 ORT 1.11.1 头文件 → 删除，改为 `aimic.c`（C 音频核心，ONNX Runtime C API）+ `aimic_bind.cpp`
  （pybind11 薄绑定，RAII 封装）——这是"功能最小化"的解释性迁移，非新增平行实现
- 修复 `aimic.c` `onnx_model_open` 崩溃：对 `CastTypeInfoToTensorInfo` 返回的 tensor info 重复
  `ReleaseTensorTypeAndShapeInfo` 会在 ORT 1.11.1 下段错误（gdb/coredumpctl 定位到模型加载处），
  删除该释放、只保留 `ReleaseTypeInfo`（tensor info 归 type info 所有）
- 功能回归通过：3 个模型（denoise/TSE/AEC）构造+推理、频谱(128)、均衡器(61)、RingBuffer、
  Resampler(48k→16k)、`process_with_far`(AEC)、TSE 参考提取（需对齐模型固定 94 参考帧）均正常；
  降噪 ~0.76ms/帧、AEC ~2.21ms/帧（真实推理）
- `.gitignore`、`build_win.ps1`、`setup.py`、`py38`、AGENTS.md / README（中英）同步
- `pack_deb.sh` 同步：deb 随包带上捆绑的 `libonnxruntime.so*`，启动脚本先注入
  `LD_LIBRARY_PATH=/opt/purevox`，Depends 去掉系统 onnxruntime；`.so` 定位改用
  `sysconfig.EXT_SUFFIX`（此前硬编码 cpython-314，CI Ubuntu 3.10/3.12 会失败）

---

## 2026-08-07 — 修复 Linux 设备枚举：USB 麦克风被误判为幻影路由

- `pwpipe_client.list_sources()` 之前把 `api.alsa.path = hw:N`（无 `,设备`）一律当幻影路由排除，
  导致真实 USB 麦克风（如 `hw:1`/`hw:2`，`device.bus=usb`）在设备列表缺失、默认麦克风为空
- 修正：`_is_phantom_route()` 对 `device.bus=usb` 的节点直接放行；幻影路由排除仅作用于
  内部/板载卡（platform/sof 类，如 `hw:sofhdadsp`），保持原有"打开也是静音"的处理
- 节点解析新增 `device_bus` 字段；回归用例覆盖 7 种情形全部通过

---

## 2026-08-07 — 内嵌 Python 3.8（独立于系统环境）

- 新增 Linux `bootstrap_python38.sh`：从 git 子模块 `packages/cpython`（CPython@v3.8.20）
  out-of-tree 编译自包含 `packages/python38/`（内嵌 CPython 3.8，自带 libpython3.8.so），
  与系统 Python（如 3.14）完全隔离；新增 `py38` 启动器（自动带 `LD_LIBRARY_PATH`）
- 新增 Windows `bootstrap_python38.ps1`：经 NuGet 下载预编译包生成 `packages\python38w\`
  （NuGet 完整版，含头文件/链接库）
- `build_win.ps1` 改为优先调用内嵌 `packages\python38w\python.exe`，构建不再依赖系统默认 Python
- 新增 git 子模块 `packages/cpython`（锁定 v3.8.20 源码，供 Linux 编译）
- `.gitignore` 忽略 `packages/python38*`、`.py38-src/`；AGENTS.md / README（中英）同步更新

---

## 2026-08-07 — 开源筹备：模型单独授权

- 确定开源方案：源码 **GPL-3.0** 开源；内置 AI 模型**不随 GPL 授权**，单独授权使用
- 新增 `MODEL-LICENSE.md`（模型授权声明）：模型禁止提取用于其他项目，仅随 PureVox 使用
- 移除商业许可（`LICENSE-COMMERCIAL.md` 已删除）
- 全部源码头注释更新为 GPL-3.0 + 模型声明
- README 迁移为 `README.md`（中文）+ `README_EN.md`（英文）

---

## 2026-08-06 — 双许可证授权

- 曾采用 **GPL-3.0 开源 + 商业许可** 双许可证模式（2026-08-07 已改为模型单独授权）
- 新增 `LICENSE-COMMERCIAL.md`（商业许可条款，后移除）
- 全部源码文件添加双许可证头注释（SPDX: GPL-3.0-or-later）
- 内置 AI 模型所有权声明：模型归作者 a2heng 所有，随软件一并授权

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
