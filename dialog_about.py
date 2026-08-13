# PureVox — AI 麦克风降噪工具
# Copyright (C) 2024-2026 a2heng <752848283@qq.com>
#
# PureVox is licensed under the GNU General Public License v3.0 or
# later (GPL-3.0-or-later).  See LICENSE for details.
# 
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# The built-in AI models are NOT covered by the GPL; they are the
# property of a2heng and may only be used with PureVox under
# authorization.  See MODEL-LICENSE.md for details.
# 
# SPDX-License-Identifier: GPL-3.0-or-later

"""
关于对话框：软件介绍 / Windows 使用说明 / Linux 使用说明 / 更新日志 / 许可证。
内容全部内嵌本 py（中文，无 emoji），以整页标签形式展示；菜单单一入口「关于」打开。
更新日志是唯一维护位置：无独立 CHANGELOG.md 文件，发版时直接在 CHANGELOG_TEXT 顶部追加。
"""

import sys

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QPushButton,
    QTextBrowser, QWidget, QTabWidget, QApplication
)
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices

try:
    from _build_version import BUILD_DATE
except ImportError:
    BUILD_DATE = "开发版"

APP_NAME = "PureVox"
APP_VERSION = BUILD_DATE
APP_DESC = "AI 麦克风降噪工具"
APP_AUTHOR = "a2heng"
URLS = {
    "GitHub": "https://a2heng.github.io/",
    "哔哩哔哩": "https://space.bilibili.com/10850943",
    "声音测试工具": "https://a2heng.github.io/mic-test.html",
}
LIBS = [
    {"name": "PySide6", "ver": "6.x", "license": "LGPL v3", "url": "https://wiki.qt.io/Qt_for_Python", "desc": "GUI 框架"},
    {"name": "PyAudio", "ver": "0.2.x", "license": "MIT", "url": "https://people.csail.mit.edu/hubert/pyaudio/", "desc": "设备枚举 / 虚拟声卡检测"},
    {"name": "ONNX Runtime", "ver": "1.11.x", "license": "MIT", "url": "https://onnxruntime.ai/", "desc": "AI 模型推理"},
]


# ── Windows 使用说明（中文，无 emoji）──

_WINDOWS_BODY = """
<body>
<h1>PureVox 使用说明（Windows）</h1>

<h2>软件简介</h2>
<p>PureVox 是一款 AI 麦克风降噪工具，能实时消除键盘声、鼠标声、电流声、风扇声、风声等背景噪音，只保留纯净人声。</p>

<h2>快速开始（本地降噪）</h2>
<h3>第 1 步：启动软件</h3>
<p>双击 <code>PureVox.exe</code> 打开软件。</p>
<p>首次启动时，如果检测到未安装 VB-Cable，会弹出检测面板，内含下载地址与安装教程。</p>
<blockquote>
<p><strong>也可以手动安装：</strong></p>
<ol>
<li>到 VB-Audio 官网 <code>https://vb-audio.com/Cable/</code> 下载 VB-CABLE 驱动</li>
<li>右键 <code>VBCABLE_Setup_x64.exe</code> → <strong>以管理员身份运行</strong></li>
<li>点击安装按钮，安装完成后重启电脑</li>
</ol>
<p>PureVox 不随安装包内置第三方工具，软件检测到未安装时会提示下载地址。</p>
</blockquote>

<h3>第 2 步：设置系统声音</h3>
<p>打开软件菜单栏 <strong>系统声音</strong> 打开声音控制面板。</p>
<p><strong>播放选项卡（CABLE Input）</strong></p>
<ol>
<li>右键你的实际扬声器/耳机 → <strong>设置为默认设备</strong>
   <span style="color:red">注意：不设置的话系统音频可能无法正常从扬声器播放。</span></li>
<li>右键 <strong>CABLE Input</strong> → <strong>属性</strong> → <strong>高级</strong> → 格式设为 <code>1 通道，16 位，48000 Hz</code></li>
</ol>
<p><strong>录制选项卡（虚拟麦克风）</strong></p>
<ol>
<li>右键 <strong>CABLE Output</strong> → <strong>设置为默认设备</strong>
   <span style="color:red">注意：必须设置，否则降噪后的麦克风在其他软件中无法使用。</span></li>
<li>右键 <strong>CABLE Output</strong> → <strong>属性</strong> → <strong>高级</strong> → 格式设为 <code>1 通道，16 位，48000 Hz</code></li>
</ol>
<blockquote>
<p><strong>48kHz 强制检测：</strong>PureVox 启动前会逐设备检测采样率。如果弹出
<strong>「以下设备不支持 48kHz，无法启动」</strong>的弹框，说明有设备当前采样率不是
48kHz——点击弹框上的 <strong>打开播放选项卡 / 打开录制选项卡</strong> 按钮进入 Windows
声音控制面板，或手动右键托盘喇叭 → 声音设置 → 声音控制面板，把弹框列出的设备在
<strong>属性 → 高级 → 默认格式</strong> 里设为 <code>48000 Hz</code>，然后重新启动 PureVox。</p>
</blockquote>

<h3>第 3 步：选择设备</h3>
<ol>
<li><strong>音频接口</strong>：默认 <code>本地接口 WASAPI（默认）</code>（Windows 原生低延迟音频接口）。
   若 WASAPI 无法正常使用，可改选 <code>本地接口 MME</code>（Windows 旧版接口，延迟较高，仅作备选）</li>
<li><strong>输入设备</strong>：选择你的物理麦克风（耳机麦克风、桌面麦克风等）</li>
<li><strong>输出设备</strong>：选择 <strong>CABLE Input</strong>。这个对应的是系统声音里的播放选项卡，降噪后的音频会输出到这里，再从录制选项卡的 CABLE Output 作为虚拟麦克风供其他软件使用。<span style="color:red">注意：不要选你的扬声器/耳机，否则声音会直接外放，虚拟麦克风（CABLE Output）也收不到信号。</span></li>
<li><strong>监听</strong>（可选）：勾选后选择你的耳机或扬声器，实时听到降噪效果</li>
</ol>

<h3>第 4 步：开始降噪</h3>
<p>点击 <strong>启动音频处理</strong> 按钮，按钮变红即表示降噪已开启。</p>
<p>这时候打开微信、Discord、游戏等软件，把麦克风设为 <strong>CABLE Output</strong> 或 <strong>默认麦克风</strong>，对方听到的就是降噪后的声音了。</p>

<h2>模式说明</h2>
<p>顶部有 5 种模式，点击切换：</p>
<table>
<thead>
<tr>
<th>模式</th>
<th>说明</th>
</tr>
</thead>
<tbody>
<tr>
<td><strong>直通</strong></td>
<td>不降噪，只处理前增益、EQ、AGC、VAD，适合只想调音量的场景</td>
</tr>
<tr>
<td><strong>降噪</strong></td>
<td>AI 深度学习降噪，消除键盘、风扇、空调等噪声，日常使用选这个</td>
</tr>
<tr>
<td><strong>AEC</strong></td>
<td>回声消除 + 降噪，适合使用音响的用户。开启后扬声器外放的声音不会传回麦克风，对方听不到回声</td>
</tr>
<tr>
<td><strong>TSE</strong></td>
<td>目标说话人提取。先用录音模式录制一段参考语音，开启后会只保留该人的声音，屏蔽其他人声和背景噪声</td>
</tr>
<tr>
<td><strong>录音</strong></td>
<td>录制 10 秒参考语音，作为 TSE 模式的参照样本</td>
</tr>
</tbody>
</table>
<blockquote>
<p><strong>日常使用选降噪模式即可。</strong></p>
</blockquote>
<p style="font-size: 9pt;">处理链路：前增益(AGC) → EQ → clip → [AEC] → 降噪 → [TSE] → clip → VAD → 输出（方括号表示该模式才启用的步骤）。</p>

<h2>增益设置</h2>
<table>
<thead>
<tr>
<th>选项</th>
<th>说明</th>
</tr>
</thead>
<tbody>
<tr>
<td><strong>前增益</strong></td>
<td>麦克风输入音量。正常说话时 VU 表峰值在 <strong>-12 ~ -6 dB</strong> 为最佳</td>
</tr>
<tr>
<td><strong>AGC</strong></td>
<td>自动调节增益，声音始终稳定。<strong>建议开启</strong>，开启后前增益滑块会自动被接管</td>
</tr>
<tr>
<td><strong>VAD</strong></td>
<td>不说话时自动静音，消除残留底噪。<strong>建议开启</strong></td>
</tr>
</tbody>
</table>

<h2>VU 电平表</h2>
<p>实时显示当前音量：</p>
<table>
<thead>
<tr>
<th>颜色区域</th>
<th>范围</th>
<th>说明</th>
</tr>
</thead>
<tbody>
<tr>
<td>绿色</td>
<td>-60 ~ -18 dB</td>
<td>音量正常</td>
</tr>
<tr>
<td>黄色</td>
<td>-18 ~ -6 dB</td>
<td>声音够大</td>
</tr>
<tr>
<td>红色</td>
<td>-6 ~ 0 dB</td>
<td>接近过载，建议降低增益</td>
</tr>
</tbody>
</table>
<p>红色小点表示最近 10 秒内的峰值。</p>

<h2>均衡器（EQ）</h2>
<p>菜单栏 <strong>设置 → 均衡器</strong>，打开 31 段均衡器。</p>
<p>可以直接拖动频点调节，也可以使用预设：</p>
<blockquote>
<p>清晰透亮 · 温暖饱满 · 低沉有力 · 减少齿音 · 减少鼻音 · 消除沉闷 · 增强临场</p>
</blockquote>

<h2>远程麦克风（手机无线麦克风）</h2>
<p>用手机当无线麦克风，音频通过局域网推流到电脑进行降噪，降噪后仍然通过 VB-Cable 输出给其他软件。</p>
<h3>准备工作</h3>
<p>确保电脑和手机在 <strong>同一个局域网</strong>（连同一个 WiFi）。</p>
<h3>操作步骤</h3>
<ol>
<li>打开 PureVox，<strong>音频接口</strong> 选择 <strong>网络(API)</strong></li>
<li>输入设备下方会出现一个网址框，格式如 <code>https://192.168.1.100:59123</code>（自动生成，一般不用改）</li>
<li>点击 <strong>启动音频处理</strong>，电脑开始等待手机连接<blockquote>
<p>注意：首次使用时会弹出防火墙授权提示，点击 <strong>是</strong> 即可，后续启动不会再弹出</p>
</blockquote>
</li>
</ol>
<p><strong>浏览器访问（推荐）</strong></p>
<ol>
<li>手机浏览器打开电脑上显示的网址（如 <code>https://192.168.1.100:59123</code>）</li>
<li>首次访问浏览器会提示"不安全"，<span style="color:red">注意：点击 <strong>高级 → 继续访问</strong> 即可（自签名证书，连接加密，安全）</span></li>
<li>点击页面上的麦克风按钮，开始说话，电脑端就会收到并降噪</li>
</ol>
<p><strong>Android App</strong></p>
<ol>
<li>安装 <code>PureVoxMic-debug.apk</code> 到手机</li>
<li>打开 App，会自动搜索局域网内的 PureVox 服务器</li>
<li>搜到后自动连接，自动开始推流，无需任何操作</li>
</ol>
<h3>输出设备怎么选</h3>
<p>远程麦克风模式下，降噪后的音频仍然会输出到你选的输出设备：</p>
<ul>
<li>选 <strong>CABLE Input</strong> → 输出到虚拟麦克风（CABLE Output），供电脑上的软件使用（微信、游戏等）</li>
<li>选你的 <strong>耳机/音箱</strong> → 直接从扬声器听到手机端的声音</li>
</ul>
<blockquote>
<p>跟本地模式一样，输出设备继续选 <strong>CABLE Input</strong> 即可。</p>
</blockquote>

<h2>全局快捷键</h2>
<table>
<thead>
<tr>
<th>快捷键</th>
<th>功能</th>
</tr>
</thead>
<tbody>
<tr>
<td><strong>右 Alt + &gt;</strong>（右 Alt + 句号键）</td>
<td>启动 / 停止降噪</td>
</tr>
</tbody>
</table>
<p>可在菜单栏 <strong>设置</strong> 中关闭快捷键。</p>

<h2>数据目录</h2>
<p>配置、日志、录音文件均保存在 <code>~/.purevox/</code>（如 <code>C:\\Users\\用户名\\.purevox\\</code>）。</p>

<h2>常见问题</h2>
<p><strong>Q：别人听不到我的声音？</strong></p>
<p>检查：① 软件输出设备是否选了 <strong>CABLE Input</strong> ② 系统默认麦克风是否设为 <strong>CABLE Output</strong> ③ 软件是否点击了 <strong>启动</strong></p>
<p><strong>Q：启动后没声音？</strong></p>
<p>检查：① 麦克风是否被系统独占 ② 输出设备是否选了 <strong>CABLE Input</strong> ③ 系统默认麦克风是否设为 <strong>CABLE Output</strong> ④ 是否弹出过 48kHz 弹框（见第 2 步说明）</p>
<p><strong>Q：降噪后人声变怪？</strong></p>
<p>先重置 <strong>EQ</strong>，然后调整前增益，确保说话时 VU 表不超过黄色区。</p>
<p><strong>Q：如何开机自启？</strong></p>
<p>菜单栏 <strong>设置</strong> → <strong>开机自启</strong> 打钩，下次开机 PureVox 会自动启动。</p>
<p><strong>Q：如何让软件启动后自动开始降噪？</strong></p>
<p>菜单栏 <strong>设置</strong> → <strong>启动时自动运行</strong> 打钩，软件打开后会自动开始降噪。可以配合开机自启一起使用。</p>
<p><strong>Q：手机连不上电脑？</strong></p>
<p>检查：① 手机和电脑是否 <strong>同一 WiFi</strong> ② 电脑防火墙是否拦截了 <strong>59123 端口</strong>（软件会自动添加规则，需要管理员权限）③ 网址是否输入正确</p>
</body>
"""


# ── Linux 使用说明（中文，无 emoji）──

_LINUX_BODY = """
<body>
<h1>PureVox 使用说明（Linux）</h1>

<h2>安装与启动</h2>
<p>Linux 版音频采集/输出/虚拟麦克风全部使用系统原生 <b>PipeWire</b>。请先用发行版包管理器安装
<b>pipewire</b> 与 <b>libpipewire</b> 开发包（各发行版包名略有不同，如 AOSC 的
<code>pipewire libpipewire-0.3-devel</code>，见 README），并保证 PipeWire 正在运行
（<code>systemctl --user status pipewire</code>）。</p>
<p>运行方式（任选其一）：</p>
<ol>
<li>安装包：按发布页下载 deb（Debian/Ubuntu 系）、rpm（Fedora 系）或 AppImage，
   安装后从桌面/应用菜单启动，或命令行执行 <code>purevox</code>；</li>
<li>源码运行：<code>./bootstrap_python38.sh</code> 准备内嵌 Python 3.8 后，
   <code>./py38 run_pyside6.py</code> 启动。</li>
</ol>

<h2>第 1 步：选择设备</h2>
<ol>
<li><strong>输入设备</strong>：选择物理麦克风（PipeWire 直接枚举设备名）；</li>
<li><strong>输出设备</strong>：选扬声器/耳机即可；想让其它软件使用降噪后的声音时，
   选 <strong>PureVox 虚拟麦克风</strong>（见下一步）；</li>
<li><strong>采样率</strong>：PipeWire 统一重采样为 48kHz 单声道，无需手动设置。</li>
</ol>

<h2>第 2 步：虚拟麦克风（可选）</h2>
<p>Linux 虚拟麦克风由 PureVox 经 PipeWire 原生创建，<b>启动时不会自动创建</b>：
菜单「<strong>虚拟声卡</strong>」→ 状态面板点「创建」。创建后提供两个出口：</p>
<ul>
<li><strong>PureVox 虚拟麦克风</strong>（purevox_out.monitor）—— 宽口径源，供绝大多数软件选用；</li>
<li><strong>PureVox mic</strong>（purevox_mic）—— 供 OBS 等只列"真源"的软件使用（由前者重映射而来）。</li>
</ul>
<p>其它软件把输入设备设为「<strong>PureVox 虚拟麦克风</strong>」即可收到降噪后的声音；
不用时回菜单「虚拟声卡」点「清理」，创建/清理均安全幂等。</p>

<h2>第 3 步：开始降噪</h2>
<p>点击 <strong>启动音频处理</strong> 按钮，按钮变红即表示降噪已开启。</p>

<h2>模式说明</h2>
<p>五种模式与 Windows 版一致：</p>
<ul>
<li><strong>直通</strong>：不降噪，仅增益 + EQ（含 AGC / VAD / 压缩）；</li>
<li><strong>降噪</strong>：AI 实时去噪，日常推荐；</li>
<li><strong>AEC</strong>：回声消除 + 降噪，适合用音响的用户，AEC 远端自动监听扬声器输出；</li>
<li><strong>TSE</strong>：目标说话人提取，先录音录制参考语音，再切 TSE 只保留该人声音；</li>
<li><strong>录音</strong>：录制 10 秒参考语音（TSE 的参照样本）。</li>
</ul>

<h2>增益 / 均衡器 / VU</h2>
<p>与 Windows 版完全一致：前增益（说话时 VU 峰值 <strong>-12 ~ -6 dB</strong> 最佳）、
AGC、VAD，31 段均衡器在菜单「设置 → 均衡器」打开。</p>

<h2>远程麦克风（手机无线麦克风）</h2>
<p>与 Windows 版步骤一致：菜单「音频接口」选「<strong>网络(API)</strong>」→ 启动处理 →
手机浏览器打开电脑显示的网址，或手机装 App 自动发现并推流；降噪后的音频输出到你选定的设备，
想交给其它软件继续选「<strong>PureVox 虚拟麦克风</strong>」即可。</p>

<h2>数据目录</h2>
<p>配置、日志、录音文件均保存在 <code>~/.purevox/</code>。</p>

<h2>常见问题</h2>
<p><strong>Q：完全没有声音？</strong></p>
<p>确认 PipeWire 正在运行（<code>systemctl --user status pipewire</code>），再确认输入/输出设备下拉是否选对。</p>
<p><strong>Q：OBS 里选不到虚拟麦克风？</strong></p>
<p>OBS 只列"真源"：请选 <strong>PureVox mic</strong>，而不是 PureVox 虚拟麦克风（monitor 源）。</p>
<p><strong>Q：AEC 没有效果？</strong></p>
<p>AEC 远端会自动监听扬声器 sink 输出，使用音响/扬声器外放时才需要它。</p>
<p><strong>Q：怎么卸载虚拟麦克风？</strong></p>
<p>菜单「虚拟声卡」→「清理」即可，删除安全幂等。</p>
</body>
"""


# ── 更新日志（中文，唯一维护位置：发版时在顶部追加）──

CHANGELOG_TEXT = """# 更新日志

## 2026-08-13 — Windows 新增本地接口 MME + 设备配置按接口隔离

- **音频接口下拉框改为「本地接口 WASAPI（默认）」+「本地接口 MME」+「网络(API)」三项**：
  Windows 设备枚举与打开流都按所选接口过滤（`get_device_id` / `get_device_names` /
  48k 检测走 `get_host_api_indices` 分级匹配 host API），不再只有一个本地接口
- **WASAPI 仍为默认**：默认值、老配置（api_type=13）均不受影响；MME（PortAudio
  paMME=2）为旧版低延迟备选，仅当 WASAPI 不可用时使用
- **设备配置按接口隔离，显式写全**：通用键 `input_device` / `output_device` /
  `monitor_device` / `aec_far_sink` 废弃，改为 `<方向>_device_<接口后缀>` 与
  `aec_far_sink_<接口后缀>`（如 `input_device_wasapi` / `input_device_mme` /
  `input_device_pulse`）。`config_manager.py` 的 `ConfigDefaults` 与 `_KEY_ORDER`
  把全部接口（WASAPI/MME/PulseAudio/ALSA/DirectSound/ASIO/Core Audio/OSS/JACK/
  Sndio）的键显式写全，不做动态生成，阅读直观
- **monitor 与 AEC far 分开维护**：监听设备存 `monitor_device_<接口>`，AEC 回声
  参考 sink 存 `aec_far_sink_<接口>`（Linux 手动选物理扬声器），同一下拉框按模式
  写不同键，互不覆盖
- **默认不置空、不特选 CABLE Input**：配置设备值为空或不在枚举列表时，强制回退
  枚举列表**第一个**并写回配置（用户自行选择 VB 用法）；删除了 Windows 输出默认
  CABLE Input 与 UI 自动偏好
- **强配置，不做旧配置迁移**：`ConfigManager.load_config` 只保留已知键，
  旧 `WASAPI_*` / 通用设备键等未知键一律丢弃回退默认；网络模式输出/监听/AEC far
  仍用平台默认接口的键
- **host API 匹配分级化**：`get_host_api_indices` 改为分级匹配——先按配置的 API 名
  （选 MME 就只列 MME，不再混入 WASAPI），匹配不到再回退平台默认，最后全枚举兜底

## 2026-08-12 — 移除编译期 `-mavx2`，全 CPU 兼容

- **`setup.py` 编 `aimic.dll`/`libaimic.so` 去掉 `-mavx2`**：一律用 `-O2`
  默认基线（x86-64 的 SSE2）。原因：`-mavx2` 让 gcc 对整个 aimic.c+pffft+
  libsamplerate 无差别生成 AVX 指令且不做运行时检测，在无 AVX 的老 CPU
  （酷睿4代以前）上，pffft FFT 热身即 `0xc000001d` 非法指令崩溃。
  实测：带 `-mavx2` 时 DLL 含 3888 条 AVX 指令、推理 4.010ms/帧、老 CPU
  崩溃；去掉后 0 条、5.629ms/帧、全 CPU 兼容（远低于 20.8ms 实时预算）。
- **模型推理性能不受影响**：onnxruntime 的 MLAS 内核在运行时按 CPUID
  dispatch（SSE2→AVX→AVX2 自动选最优），与本库编译参数无关。
- **「推理后端」日志改为纯报告**：`cpu_supports_avx()` 探测 CPU 能力并打印
  AVX/SSE，仅反映 MLAS 会跑哪档内核，不参与行为决策；NPU 分支保留为**死代码
  示例**（捆绑 onnxruntime 1.11.1 为纯 CPU 构建，DirectML/OpenVINO EP 必然
  失败），作为日后加入 NPU 执行提供程序的参考路径，见 `aimic.c`
  `onnx_apply_backend` 的 TODO(NPU) 注释。

## 2026-08-12 — 48kHz 启动检测补诊断日志 + 修复中文设备名乱码

- **48k 检测失败时输出诊断日志**：`_try_open_48k` 失败打 `[warn]`，包含设备索引/
  名字/默认采样率/输入输出通道数/宿主 API/异常原因；每次启动输出一行汇总
  （`[48k检测] 输入=OK 输出=FAIL ...`）；弹框阻止启动时打 `[err]` 列出设备名。
  用于区分「设备真不支持 48k」与「设备被占用/独占」等资源性问题。
- **修复中文设备名乱码**：PortAudio 返回 UTF-8 设备名，PyAudio 在中文系统
  （locale=cp936/GBK）先按 GBK 解码、不抛异常就不退回 UTF-8，导致部分中文名
  （如「线路输入」）变成「绾胯矾杈撳叆」式乱码，且同批设备有乱有正常。
  新增 `device_api.fix_device_name()`：对乱码串按 GBK 重编码再按 UTF-8 解码还原，
  正常名字不受影响。`get_device_names` / `get_device_id` 与 48k 诊断日志统一走它。

## 2026-08-12 — 移除 VB-CABLE 检测的 PnP 退化回退

- `dialog_vbcable_check.py` 虚拟声卡检测不再用 PowerShell `Get-PnpDevice` 兜底：
  只走 PyAudio 枚举双端点（CABLE Input 有输出通道 + CABLE Output 有输入通道，
  限时 5s）。原退化路径会误判「驱动已装但被禁用 / 半卸载残留」的设备为已安装，
  且 Get-PnpDevice 需额外拉起 PowerShell（~1s、无窗口）。
- 检测单一实现路径：不做任何驱动层/PnP 兜底，已安装判定唯一依据是
  PortAudio 能枚举到可用的 CABLE 双端点

## 2026-08-11 — 推理后端改为自动选择（NPU → AVX → SSE）

- **移除「推理后端」下拉框**：后端不再需要手动选择，创建模型时自动按
  NPU → AVX → SSE 顺序选取：优先尝试 NPU 执行提供程序（Linux OpenVINO /
  Windows DirectML），不可用则回退 CPU——CPU 支持 AVX 用 AVX，不支持则 SSE
  （ORT 内核本就按 CPUID 自动选择最佳指令集）
- **实际生效后端仍可见**：启动日志打印 `[启动] 推理后端: ...`，若 NPU 不可用会
  注明原因；捆绑的 onnxruntime 1.11.1 是纯 CPU 构建（已移除 SSE 限制配置项、
  CPU EP 不读任何会话配置，实测确认），故当前实际生效为 AVX
- 移除 `inference_backend` 配置键（不再需要）

## 2026-08-11 — 降低 PipeWire 状态日志频率

- `[PipeWire] 处理 N 帧 (rms=...)` 状态日志由每 100 帧（约 2 秒）改为每 1000 帧
  （约 21 秒）打印一次，减少刷屏

## 2026-08-11 — 窗口标题版本号随 tag 打包

- **Linux 不再显示「开发版」**：deb / rpm / AppImage 打包脚本按 tag
  （`GITHUB_REF_NAME`）生成 `_build_version.py`，窗口标题与包版本/文件名同源；
  本地手动打包回退当前时间
- **Windows 标题日期取 tag**：`build_win.ps1` 生成版本戳时优先用
  `GITHUB_REF_NAME`（v<yyyy.MM.dd.HHmm> → yyyy-MM-dd-HHmm），不再用构建机本地时间

## 2026-08-11 — 移除窗口隐藏时的频谱调试日志刷屏

- `_feed_visualizer` 中遗留的 `[调试] _feed: ...` dev 日志在窗口最小化到托盘时每帧
  打印一条，已删除该调试分支（频谱仅在可见时更新，行为不变）

## 2026-08-11 — Windows 设备枚举对齐 WASAPI

- **Windows 设备枚举只留 WASAPI host API**：`get_device_names` / `get_device_id`
  不再枚举全部 host API，改为先经 `device_api.get_host_api_indices` 过滤
  `dev['hostApi']`，避免混入 DirectSound / MME / WDM-KS 等重复或无关端点
- **修 host API 名字匹配**：`device_api.get_host_api_indices` 原用精确相等
  （`info['name'] in names`），但 PyAudio 的 host API 名带厂商前缀
  （WASAPI 实为 `Windows WASAPI`），匹配不到误触「全枚举兜底」→ 等于没过滤。
  改为大小写不敏感的子串匹配；名不见时仍回退全枚举（虚拟 sink 等跨 API 设备）
- Linux 不受影响：仍是原生 PipeWire pw-dump 节点枚举

## 2026-08-11 — 用户手册与更新日志内化到「关于」对话框

- **删除两个独立文档文件**：`用户手册.html` 与 `CHANGELOG.md` 删除，内容全部内嵌
  `dialog_about.py`；菜单改为单一「关于」入口，打开即整页标签：软件介绍 /
  Windows 使用说明 / Linux 使用说明 / 更新日志 / 许可证
- **更新日志改内嵌维护**：从本版起不再有独立 CHANGELOG.md 文件，发版时直接在本
  `CHANGELOG_TEXT` 顶部追加（见 AGENTS.md）
- **新增 Linux 使用说明页**：原生 PipeWire、虚拟麦克风双出口、AEC / 远程麦克风、
  常见问题；Windows 页为原手册内容
- **Windows 说明按现状修正**：音频接口只保留 WASAPI（不再提 MME）、删除后增益
  （post_gain 已移除）；新增 48kHz 强制检测说明——启动弹框提示设备不支持 48kHz 时，
  去 Windows 声音控制面板把设备默认格式设为 48000 Hz 后重试
- **去掉 emoji**：手册等用户可见文本不再使用 emoji（不引入 awesome 风格）
- 引用处同步：README（中/英）链接、AGENTS.md、build_win.ps1（不再拷贝 CHANGELOG.md）

## 2026-08-11 — VB-CABLE 检测面板重设计

- **检测只弹未安装**：`_check_vbcable` 改为先检测，VB-CABLE 已安装则无事发生，
  未安装才弹面板；检测开关（`vbcable_check_enabled`）默认开启，取消勾选即跳过
- **面板统一结构**（`dialog_vbcable_check.py`）：已安装/未安装共用同一布局——
  状态灯 + 双端点说明（CABLE Input 接收 PureVox 输出 / CABLE Output 作虚拟麦克风，
  均 48kHz，含数据流向示意）+ 驱动卡片；未安装时卡片额外显示红色安装提示
- **VB 控制面板按钮移入面板**：菜单栏删除「VB设置」入口（及 `open_vb_panel`），
  面板驱动卡片内新增「打开控制面板」按钮（未安装时置灰）
- **面板自动刷新状态**：2s 定时器重查安装状态，装好驱动后指示灯无需重开即变绿
- 面板新增公开的 `vbcable_installed()` 供启动检测复用

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
  `bootstrap_python38.ps1`（NuGet 预编译包 → `packages\\python38w\\`）
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
  - Windows：pwsh 没有 `\\` 行继续符（compileall 多行命令被拆行执行）→ 写单行；
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

- 增益 -20~+30dB，配置自动保存"""
def _docs_css(pal) -> str:
    """按当前调色板生成文档样式（亮/暗自适应）。"""
    from PySide6.QtGui import QColor as _QColor
    text_color = pal.text().color().name()
    link_color = pal.highlight().color().name()
    border_color = pal.mid().color().name()
    base = _QColor(pal.base().color())
    dark = base.lightness() < 128
    if dark:
        th_bg = base.lighter(122).name()
        code_bg = base.lighter(116).name()
        quote_bg = base.lighter(106).name()
        h1_line = "#888888"
        h2_line = "#555555"
    else:
        th_bg = base.lighter(103).name()
        code_bg = base.lighter(103).name()
        quote_bg = base.lighter(102).name()
        h1_line = "#333333"
        h2_line = "#cccccc"
    return (
        "body{font-family:'Microsoft YaHei',sans-serif;max-width:820px;"
        "margin:8px auto;padding:0 12px;font-size:14px;line-height:1.7;color:%s}"
        "h1{font-size:20pt;border-bottom:2px solid %s;padding-bottom:8px}"
        "h2{font-size:15pt;margin-top:24px;border-bottom:1px solid %s;padding-bottom:4px}"
        "h3{font-size:12pt;margin-top:16px}"
        "table{border-collapse:collapse;width:100%%;margin:10px 0}"
        "th,td{border:1px solid %s;padding:6px 10px;text-align:left}"
        "th{background:%s}"
        "code{background:%s;padding:2px 6px;border-radius:3px;font-size:13px}"
        "blockquote{border-left:4px solid %s;margin:10px 0;padding:6px 14px;background:%s}"
        "hr{border:none;border-top:1px solid %s;margin:20px 0}"
        "a{color:%s}"
    ) % (text_color, h1_line, h2_line, border_color, th_bg, code_bg,
         border_color, quote_bg, h2_line, link_color)


def _docs_html(body: str, pal) -> str:
    return "<html><head><style>%s</style></head>%s</html>" % (_docs_css(pal), body)


class AboutDialog(QDialog):
    """关于对话框：整页标签形式 —— 介绍 / Windows / Linux / 更新日志 / 许可证。"""

    def __init__(self, parent=None, start_tab=0):
        super().__init__(parent)
        self.setWindowTitle("关于 {APP_NAME}".format(APP_NAME=APP_NAME))
        self.resize(820, 640)
        self.setModal(True)
        self._build(start_tab)
        # DWM 标题栏跟随当前主题（仅 Windows；其它平台空转）
        try:
            import ctypes
            from theme_colors import is_dark_current
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            dark = is_dark_current()
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                int(self.winId()), DWMWA_USE_IMMERSIVE_DARK_MODE,
                ctypes.byref(ctypes.c_int(1 if dark else 0)),
                ctypes.sizeof(ctypes.c_int))
        except Exception:
            pass

    def _build(self, start_tab):
        pal = QApplication.instance().palette()
        text_color = pal.text().color().name()
        link_color = pal.highlight().color().name()
        hint_color = pal.placeholderText().color().name()
        base_color = pal.base().color().name()
        border_color = pal.mid().color().name()
        from PySide6.QtGui import QColor
        header_bg = QColor(pal.base().color()).lighter(115).name()

        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(8, 8, 8, 8)

        tabs = QTabWidget()
        layout.addWidget(tabs, 1)

        # ── 关于：介绍 PureVox ──
        about = QTextBrowser()
        about.setOpenExternalLinks(True)
        about.setStyleSheet("QTextBrowser { border: none; }")
        about.setHtml(f"""
        <div style="line-height: 1.8; color: {text_color}; font-family: 'Microsoft YaHei', sans-serif;">
            <div style="text-align: center; margin-top: 16px;">
                <div style="font-size: 26pt; font-weight: bold; color: {link_color};">PureVox</div>
                <div style="font-size: 12pt; color: {hint_color}; margin-top: 6px;">{APP_DESC}</div>
                <div style="font-size: 10pt; color: {link_color}; font-weight: bold; margin-top: 10px;">版本: {APP_VERSION}</div>
                <div style="font-size: 9pt; color: {hint_color}; margin-top: 4px;">作者: {APP_AUTHOR} · Copyright (C) 2024-2026 a2heng · GPL-3.0-or-later</div>
            </div>
            <hr>
            <p style="text-align: center;">
                <a href="{URLS['GitHub']}" style="color: {link_color}; margin-right: 12px;">GitHub</a>
                <a href="{URLS['哔哩哔哩']}" style="color: {link_color}; margin-right: 12px;">哔哩哔哩</a>
                <a href="{URLS['声音测试工具']}" style="color: {link_color};">声音测试工具</a>
            </p>
            <h3 style="color: {link_color}; margin-top: 8px;">它是什么</h3>
            <p>PureVox 是一款 AI 麦克风降噪工具，实时消除键盘声、鼠标声、电流声、
            风扇声、风声等背景噪音，只保留纯净人声；也支持目标说话人提取与回声消除。</p>
            <h3 style="color: {link_color};">功能特性</h3>
            <ul>
                <li><b>实时 AI 降噪</b>：48kHz 模型推理，本地低延迟链路</li>
                <li><b>目标说话人提取（TSE）</b>：只保留指定人的声音</li>
                <li><b>回声消除（AEC）</b>：消除扬声器外放的回声</li>
                <li><b>均衡器</b>：31 段 EQ + 预设，可视化拖拽</li>
                <li><b>远程麦克风</b>：手机 App / 浏览器经局域网推流到电脑降噪</li>
                <li><b>虚拟声卡</b>：Windows 用 VB-CABLE；Linux 用原生 PipeWire 虚拟麦克风</li>
                <li><b>便捷功能</b>：全局快捷键、开机自启、主题、VU 电平表</li>
            </ul>
            <h3 style="color: {link_color};">支持平台</h3>
            <p>Windows（7+，含 VB-CABLE 虚拟声卡）与 Linux（原生 PipeWire），
            详细操作见本对话框「Windows 使用说明」「Linux 使用说明」标签页。</p>
        </div>
        """)
        tabs.addTab(about, "关于")

        # ── Windows 使用说明 ──
        win_tab = QWidget()
        wl = QVBoxLayout(win_tab)
        wl.setContentsMargins(0, 0, 0, 0)
        win_text = QTextBrowser()
        win_text.setOpenExternalLinks(True)
        win_text.setStyleSheet("QTextBrowser { border: none; }")
        win_text.setHtml(_docs_html(_WINDOWS_BODY, pal))
        wl.addWidget(win_text)
        tabs.addTab(win_tab, "Windows 使用说明")

        # ── Linux 使用说明 ──
        linux_tab = QWidget()
        ll2 = QVBoxLayout(linux_tab)
        ll2.setContentsMargins(0, 0, 0, 0)
        linux_text = QTextBrowser()
        linux_text.setOpenExternalLinks(True)
        linux_text.setStyleSheet("QTextBrowser { border: none; }")
        linux_text.setHtml(_docs_html(_LINUX_BODY, pal))
        ll2.addWidget(linux_text)
        tabs.addTab(linux_tab, "Linux 使用说明")

        # ── 更新日志 ──
        changelog_tab = QWidget()
        cll = QVBoxLayout(changelog_tab)
        cll.setContentsMargins(0, 0, 0, 0)
        changelog_text = QTextBrowser()
        changelog_text.setOpenExternalLinks(True)
        changelog_text.setStyleSheet("QTextBrowser { border: none; }")
        changelog_text.setMarkdown(CHANGELOG_TEXT)
        cll.addWidget(changelog_text)
        tabs.addTab(changelog_tab, "更新日志")

        # ── 许可证 ──
        license_tab = QWidget()
        lll = QVBoxLayout(license_tab)
        lll.setContentsMargins(12, 12, 12, 12)
        lib_rows = ''.join(
            f'<tr><td style="padding: 4px 6px; border-bottom: 1px solid {border_color};">'
            f'<a href="{l["url"]}" style="color: {link_color}; text-decoration: none;">{l["name"]} {l["ver"]}</a></td>'
            f'<td style="padding: 4px 6px; border-bottom: 1px solid {border_color};">{l["license"]}</td>'
            f'<td style="padding: 4px 6px; border-bottom: 1px solid {border_color}; color: {hint_color};">{l["desc"]}</td></tr>'
            for l in LIBS
        )
        license_text = QTextBrowser()
        license_text.setOpenExternalLinks(True)
        license_text.setStyleSheet("QTextBrowser { border: none; }")
        license_text.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        from PySide6.QtCore import qVersion
        from PySide6 import __version__ as pyside_ver
        license_text.setHtml(f"""
        <div style="line-height: 1.6; color: {text_color}; font-family: 'Microsoft YaHei', sans-serif;">
            <h3 style="color: {link_color}; margin-top: 0;">许可条款</h3>
            <p>本软件采用<b>GPL-3.0 开源许可证</b>：</p>
            <p style="padding-left: 16px;">
                1) <b>GNU General Public License v3.0 (GPL-3.0)</b> —— 开源许可。<br>
                您有权自由使用、修改与分发，但修改版须保持 GPL-3.0 并公开源码。</p>
            <p style="padding-left: 16px;">
                2) <b>内置 AI 模型</b> —— 单独授权。<br>
                模型不随 GPL 授权，禁止用于其他项目，仅可在 PureVox 内经作者授权使用。
                <a href="https://www.gnu.org/licenses/gpl-3.0.html" style="color: {link_color};">GPL-3.0 全文</a></p>
            <hr>
            <h3 style="color: {link_color};">第三方库</h3>
            <table style="width: 100%; border-collapse: collapse;">
                <tr style="background: {header_bg};">
                    <th style="padding: 6px; text-align: left; border-bottom: 2px solid {border_color};">库</th>
                    <th style="padding: 6px; text-align: left; border-bottom: 2px solid {border_color};">许可证</th>
                    <th style="padding: 6px; text-align: left; border-bottom: 2px solid {border_color};">说明</th>
                </tr>
                {lib_rows}
            </table>
            <p style="color: {hint_color}; font-size: 9pt; margin-top: 8px;">
                VB-CABLE 为 VB-Audio 专有驱动，用户在 Windows 端自行安装，不随本软件分发。</p>
            <hr>
            <h3 style="color: {link_color};">PySide6 (Qt for Python) —— LGPL v3</h3>
            <p><b>Qt 版本:</b> {qVersion()} · <b>PySide6 版本:</b> {pyside_ver}</p>
            <p>本软件动态链接 PySide6，满足 LGPL v3 要求：
            您有权获取 PySide6 源码，并将其替换为其它兼容 LGPL 的版本。</p>
            <p>
                <a href="https://www.gnu.org/licenses/lgpl-3.0.html" style="color: {link_color};">LGPL v3 全文</a> |
                <a href="https://wiki.qt.io/Qt_for_Python" style="color: {link_color};">PySide6 源码</a>
            </p>
            <p style="color: {hint_color}; font-size: 9pt;">
                Qt is a registered trademark of The Qt Company Ltd. 本软件与 The Qt Company 无关联。</p>
        </div>
        """)
        lll.addWidget(license_text)
        tabs.addTab(license_tab, "许可证")

        tabs.setCurrentIndex(start_tab)


def show_about_dialog(parent=None, start_tab=0):
    """打开关于对话框（整页标签）。start_tab: 0=关于, 1=Windows, 2=Linux, 3=更新日志, 4=许可证。"""
    AboutDialog(parent, start_tab).exec()