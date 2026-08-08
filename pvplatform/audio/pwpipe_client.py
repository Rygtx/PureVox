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
Linux 原生 PipeWire 输入/输出（ctypes 绑定 pvpipe 的薄封装）。

为什么用原生 PipeWire（取代旧 GStreamer / JACK）：
  - 格式协商声明 F32 单声道 48000Hz，PipeWire 内置重采样 + 声道转换，
    模型永远拿 48k 单声道，输出自动上混到目标设备声道数。
  - 无 JACK 依赖，更现代；虚拟麦克风生命周期可控（退出即清理）。

设备列表 = `pw-cli ls Node` 解析的节点名（node.name 稳定）：
  - 输入：media.class=Audio/Source（物理麦克风 + PureVox 虚拟麦克风 purevox_mic）
  - 输出：media.class=Audio/Sink（扬声器 + PureVox 虚拟麦克风 sink purevox_out）
  排除 PureVox 自身流节点与 purevox_out 的 monitor 源（避免"两个虚拟麦克风"）。

结构：
  - list_sources() / list_destinations()  节点名列表（去重/净化）
  - PwBridge                              input/output/monitor 三流 + 48k 单声道协商
"""

import subprocess
import sys
from typing import Dict, List, Optional

try:
    import pvpipe
    PW_AVAILABLE = True
except Exception:
    PW_AVAILABLE = False

IS_LINUX = sys.platform.startswith("linux")


def pw_available() -> bool:
    """pvpipe 扩展是否可用。"""
    return IS_LINUX and PW_AVAILABLE


def _list_nodes() -> List[Dict[str, str]]:
    """解析 `pw-dump`（PipeWire 标准全量 introspection），返回节点列表。

    每个节点含：id / name / description / media_class / api_alsa_path / state。
    """
    nodes: List[Dict[str, str]] = []
    try:
        out = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=5).stdout
        import json
        objs = json.loads(out)
    except Exception:
        return nodes
    for o in objs:
        if o.get("type") != "PipeWire:Interface:Node":
            continue
        info = o.get("info", {}) or {}
        p = info.get("props", {}) or {}
        nodes.append({
            "id": str(o.get("id", "")),
            "name": p.get("node.name", ""),
            "description": p.get("node.description", ""),
            "media_class": p.get("media.class", ""),
            "api_alsa_path": p.get("api.alsa.path", ""),
            "device_bus": p.get("device.bus", ""),
            "state": info.get("state", ""),
        })
    return nodes


def _is_phantom_route(node: Dict[str, str]) -> bool:
    """ALSA 捕获路径未指定具体设备（hw:<card> 无 ,<dev>）→ 幻影/默认路由。

    例：hw:sofhdadsp（卡默认，常解析到非真实捕获）vs hw:sofhdadsp,6（具体 PCM）。
    这类源即使能"打开"，采集到的也是纯静音。另：error 状态的节点同样排除。
    """
    if node.get("state") == "error":
        return True
    # USB 设备上的 hw:card（无 ,设备）是真实采集（如 hw:1/hw:2），不是幻影路由。
    # 幻影路由只出现在内部/板载（platform/sof 类）卡，打开也是静音。
    if (node.get("device_bus") or "").strip().lower() == "usb":
        return False
    path = (node.get("api_alsa_path") or "").strip().lower()
    if not path:
        return False
    if path.startswith("hw:") and "," not in path:
        return True
    if path in ("default", "plughw", "hw"):
        return True
    return False


def list_sources() -> List[str]:
    """枚举输入节点名（麦克风选项）。

    物理麦克风 = media.class=Audio/Source；排除：
      - PureVox 自身流（PureVox-*）
      - 幻影路由（api.alsa.path 未指定具体设备，如 hw:sofhdadsp）与 error 节点
        —— 这类源是 UCM 幻影/死路由，打开也是静音（如本机 Stereo Microphone）
    PureVox 虚拟麦克风 = 单声道 null-sink purevox_out 的 monitor
    （purevox_out.monitor，唯一 PureVox 源）。
    """
    nodes = _list_nodes()
    out = []
    for n in nodes:
        if n["media_class"] != "Audio/Source":
            continue
        name = n["name"]
        if not name or name.startswith("PureVox-"):
            continue
        if _is_phantom_route(n):
            continue
        if name not in out:
            out.append(name)
    # 虚拟麦克风源 = purevox_out 的 monitor（sink 存在时）
    if any(n["name"] == "purevox_out" for n in nodes):
        if "purevox_out.monitor" not in out:
            out.append("purevox_out.monitor")
    return out


def list_destinations() -> List[str]:
    """枚举输出节点名（media.class=Audio/Sink）。

    保留扬声器与 PureVox 虚拟麦克风 sink（purevox_out）。
    """
    out = []
    for n in _list_nodes():
        if n["media_class"] != "Audio/Sink":
            continue
        name = n["name"]
        if not name or name.startswith("PureVox-"):
            continue
        if name not in out:
            out.append(name)
    return out


def node_description(name: str) -> str:
    """节点名 → node.description（无则返回节点名）。"""
    if name == "purevox_out.monitor":
        return "PureVox 虚拟麦克风"
    for n in _list_nodes():
        if n["name"] == name:
            return n["description"] or name
    return name


def source_label(name: str) -> str:
    """输入节点显示名（标记职责）。"""
    if name == "purevox_out.monitor":
        return "PureVox 虚拟麦克风（降噪后）"
    if name.startswith("purevox"):
        return "PureVox 虚拟麦克风（降噪后）"
    return "麦克风 · " + (node_description(name) or name)


def dest_label(name: str) -> str:
    """输出节点显示名（标记职责）。"""
    if name == "purevox_out":
        return "PureVox 虚拟麦克风（输出目标，默认）"
    return "播放 · " + (node_description(name) or name)


def default_mic_name() -> str:
    """默认输入：第一个物理麦克风节点名。"""
    for s in list_sources():
        if "source" in s and "purevox" not in s:
            return s
    for s in list_sources():
        if not s.startswith("purevox"):
            return s
    return ""


def default_sink_name() -> str:
    """默认输出：PureVox 虚拟麦克风 sink（无则第一个输出）。"""
    for d in list_destinations():
        if d == "purevox_out":
            return d
    for d in list_destinations():
        return d
    return ""


def speaker_sink_name() -> str:
    """物理扬声器 sink 名（AEC far 兜底目标）。

    优先非 PureVox 的真实输出；只有虚拟麦克风时返回 ""（无实梅西扬，AEC 静默降级）。
    """
    dsts = list_destinations()
    for d in dsts:
        if d != "purevox_out" and not d.startswith("purevox"):
            return d
    return ""


class PwBridge:
    """PureVox 原生 PipeWire 桥：input 采集 + output 播放 + 可选 monitor 监听。

    所有流以 F32 单声道 48000Hz 协商，PipeWire 负责重采样与声道转换。
    Python 线程 read()/write() 搬运，进程回调只动无锁环形缓冲。
    """

    def __init__(self):
        self._bridge = pvpipe.PwBridge() if PW_AVAILABLE else None
        self._monitor_name: str = ""

    @property
    def available(self) -> bool:
        return self._bridge is not None

    def open(self, input_name: str, output_name: str, monitor_name: str = "") -> bool:
        if not self.available:
            return False
        self._monitor_name = monitor_name or ""
        return self._bridge.open(input_name or "", output_name or "", self._monitor_name)

    def close(self) -> None:
        if self.available:
            self._bridge.close()

    def active(self) -> bool:
        return bool(self.available and self._bridge.active())

    def last_error(self) -> str:
        return self._bridge.last_error() if self.available else "pvpipe 扩展不可用"

    def sample_rate(self) -> int:
        return int(self._bridge.sample_rate()) if self.available else 0

    def read(self, n: int) -> Optional[List[float]]:
        if not self.available:
            return None
        return self._bridge.read(n)

    def write(self, samples: List[float]) -> None:
        if self.available and samples:
            self._bridge.write(samples)

    def set_monitor(self, monitor_name: str, enabled: bool) -> None:
        """运行时开关监听流（与输出同一路降噪音频）。"""
        if not self.available:
            return
        self._monitor_name = monitor_name or "" if enabled else ""
        if enabled and monitor_name:
            self._bridge.set_monitor(monitor_name, True)
        elif not enabled:
            self._bridge.set_monitor(self._monitor_name, False)

    def set_far(self, sink_name: str, enabled: bool) -> bool:
        """运行时开关 AEC far 采集流（capture.sink 监听 sink 输出）。

        sink_name = 扬声器 sink 节点名（如 alsa_output.…analog-stereo）。
        """
        if not self.available:
            return False
        return self._bridge.set_far(sink_name or "", enabled)

    def read_far(self, n: int) -> Optional[List[float]]:
        if not self.available:
            return None
        return self._bridge.read_far(n)
