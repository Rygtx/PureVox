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

"""Linux 音频桥（纯 Python，pipewire-pulse 兼容层）。

历史：曾用自编 C 库 libpvpipe.so（原生 pw_stream）。迁移为纯 Python 后改用
`pulsectl`（ctypes 到系统 libpulse，走 pipewire-pulse 协议兼容层），
不再有任何自编译二进制。格式仍统一 F32 单声道 48000Hz，
重采样与声道转换由 PipeWire 完成。

设备列表 = `pw-dump` 标准 introspection 解析的节点名（node.name 稳定）：
  - 输入：media.class=Audio/Source（物理麦克风 + 虚拟麦克风 monitor）
  - 输出：media.class=Audio/Sink（扬声器 + purevox_out）
  排除 PureVox 自身流节点与真源 purevox_mic（对外虚拟麦克风，不参与自身输入）。

结构：
  - list_sources() / list_destinations()   节点名列表（去重/净化）
  - PwBridge                               input/output(/monitor/far) 多流桥
"""

import json
import subprocess
import sys
import threading
import time
from typing import Dict, List, Optional

import numpy as np

IS_LINUX = sys.platform.startswith("linux")

# 桥接数据面粒度：10ms @48kHz = 480（与 pvengine.context.HOP_LENGTH 一致）。
# _Ring 按此长度分块入队，消费方 read(引擎 hop) 必须与之对齐——
# 粒度错位会整块弹出后丢弃尾部样本（勿改回 1024）。
HOP = 480

try:
    import pulsectl  # 纯 Python（ctypes 系统 libpulse）
    PULSE_AVAILABLE = True
except Exception:
    PULSE_AVAILABLE = False

# libpulse pa_sample_spec.format 枚举值：FLOAT32LE = 3
_FORMAT_FLOAT32LE = 3


def pw_available() -> bool:
    """音频桥是否可用（Linux + pulsectl 就绪）。"""
    return IS_LINUX and PULSE_AVAILABLE


def _list_nodes() -> List[Dict[str, str]]:
    """解析 `pw-dump`（PipeWire 标准全量 introspection），返回节点列表。

    每个节点含：id / name / description / media_class / api_alsa_path / state。
    """
    nodes: List[Dict[str, str]] = []
    try:
        out = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=5).stdout
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


def list_sources() -> List[str]:
    """枚举输入节点名（PureVox 自身的麦克风选项）。

    PureVox 的输入 = 真实物理麦克风（media.class=Audio/Source）。排除：
      - PureVox 自身流（PureVox-*）
      - PureVox 虚拟麦克风（purevox_out.monitor / purevox_mic）——那是 PureVox
        降噪后的**输出**（给别人软件当麦克风），拿它当输入会形成回授
      - error 状态的死节点

    注意：**不按 api.alsa.path 排除板载卡接口**（数字麦 Mic1 与模拟麦 Mic2 是
    同一块 sof-hda 声卡的两个接口、各对应一个真实物理麦克风），宽松枚举与
    旧实现一致。
    """
    nodes = _list_nodes()
    out = []
    for n in nodes:
        if n["media_class"] != "Audio/Source":
            continue
        name = n["name"]
        if not name or name.startswith("PureVox-"):
            continue
        if name.startswith("purevox"):
            continue
        if n.get("state") == "error":
            continue
        if name not in out:
            out.append(name)
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
        return "PureVox out"
    for n in _list_nodes():
        if n["name"] == name:
            return n["description"] or name
    return name


def source_label(name: str) -> str:
    """输入节点显示名（标记职责）。"""
    if name == "purevox_mic":
        return "PureVox mic（虚拟麦克风）"
    if name.startswith("purevox"):
        return "PureVox out"
    return "麦克风 · " + (node_description(name) or name)


def dest_label(name: str) -> str:
    """输出节点显示名（标记职责）。"""
    if name == "purevox_out":
        return "PureVox out（默认）"
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

    优先非 PureVox 的真实输出；只有虚拟麦克风时返回 ""（无物理扬声器，AEC 静默降级）。
    """
    dsts = list_destinations()
    for d in dsts:
        if d != "purevox_out" and not d.startswith("purevox"):
            return d
    return ""


# ---------------------------------------------------------------------------
#  流线程：每条流独占一个线程 + 一个 pulsectl.Pulse 连接
#  （libpulse 主循环有线程亲和性，连接必须在创建它的线程内使用）
# ---------------------------------------------------------------------------

class _RecordThread(threading.Thread):
    """从 source 录制 F32 单声道 48kHz，推入环形缓冲。"""

    def __init__(self, tag: str, source_name: str, ring, block: int = HOP):
        super().__init__(daemon=True, name=f"pv-rec-{tag}")
        self._client = f"PureVox-{tag}"
        self._source = source_name
        self.ring = ring
        self._block = block
        self._stop_evt = threading.Event()
        self.ready = threading.Event()
        self.error: str = ""

    def run(self):
        import pulsectl
        try:
            with pulsectl.Pulse(self._client) as pulse:
                self.ready.set()
                with pulse.connect_recording(
                        source_name=self._source,
                        stream_name=self._client,
                        rate=48000, channels=1,
                        format=getattr(pulsectl, "PA_SAMPLE_FLOAT32LE", _FORMAT_FLOAT32LE),
                ) as rec:
                    while not self._stop_evt.is_set():
                        data = rec.read(self._block)
                        if data:
                            self.ring.write(np.frombuffer(bytes(data), dtype=np.float32))
        except Exception as e:
            self.error = str(e)
            self.ready.set()

    def stop(self):
        self._stop_evt.set()


class _PlayThread(threading.Thread):
    """向 sink 播放 F32 单声道 48kHz；从缓冲拉取数据，欠载补静音。"""

    def __init__(self, tag: str, sink_name: str, ring, block: int = HOP):
        super().__init__(daemon=True, name=f"pv-play-{tag}")
        self._client = f"PureVox-{tag}"
        self._sink = sink_name
        self.ring = ring
        self._block = block
        self._stop_evt = threading.Event()
        self.ready = threading.Event()
        self.error: str = ""

    def run(self):
        import pulsectl
        try:
            with pulsectl.Pulse(self._client) as pulse:
                self.ready.set()
                with pulse.connect_playback(
                        sink_name=self._sink,
                        stream_name=self._client,
                        rate=48000, channels=1,
                        format=getattr(pulsectl, "PA_SAMPLE_FLOAT32LE", _FORMAT_FLOAT32LE),
                ) as play:
                    silence = bytes(self._block * 4)
                    while not self._stop_evt.is_set():
                        got = self.ring.read(self._block)
                        if got:
                            play.write(np.asarray(got, dtype=np.float32).tobytes())
                        else:
                            play.write(silence)
        except Exception as e:
            self.error = str(e)
            self.ready.set()

    def stop(self):
        self._stop_evt.set()


class _Ring:
    """轻量 FIFO（满丢最旧 / 读不足返回 None）。"""

    def __init__(self, capacity: int):
        from collections import deque
        self._dq = deque(maxlen=max(capacity // HOP, 4))  # 以 hop 为单位存块
        self._lock = threading.Lock()

    def write(self, samples):
        x = np.asarray(samples, dtype=np.float32).reshape(-1)
        with self._lock:
            for i in range(0, len(x), HOP):
                self._dq.append(x[i:i + HOP])

    def read(self, n: int):
        need = max(1, min(int(n), HOP))
        with self._lock:
            if not self._dq:
                return None
            chunk = self._dq.popleft()
        return chunk[:need].tolist()


class PwBridge:
    """PureVox 纯 Python 音频桥：N 路输入采集（自动混音）+ M 路输出播放（扇出）+ 可选 AEC far。

    所有流以 F32 单声道 48000Hz 协商，PipeWire 负责重采样与声道转换。
    read() 对全部输入环取平均（缺席的路跳过）；write() 把同一份降噪音频
    推进每一路输出环。Python 线程 read()/write() 经内部缓冲搬运。
    """

    def __init__(self):
        self._in_threads: List[_RecordThread] = []
        self._in_rings: List[_Ring] = []
        self._out_threads: List[_PlayThread] = []
        self._out_rings: List[_Ring] = []
        self._far_thread: Optional[_RecordThread] = None
        self._far_ring = _Ring(HOP * 8)
        self._error: str = ""

    @property
    def available(self) -> bool:
        return pw_available()

    # ── 连接管理 ──

    def open(self, inputs: List[str], outputs: List[str]) -> bool:
        """打开 N 路采集 + M 路播放（node.name 列表，空串项忽略）。"""
        if not self.available:
            self._error = "pulsectl 不可用（pip install pulsectl）"
            return False
        inputs = [s for s in (inputs or []) if s]
        outputs = [s for s in (outputs or []) if s]
        for i, name in enumerate(inputs):
            ring = _Ring(HOP * 8)
            t = _RecordThread(f"in{i}", name, ring)
            self._in_threads.append(t)
            self._in_rings.append(ring)
            t.start()
        for i, name in enumerate(outputs):
            tag = "out" if i == 0 else f"out{i}"
            ring = _Ring(48000)          # 每路独立 1s 缓冲
            t = _PlayThread(tag, name, ring)
            self._out_threads.append(t)
            self._out_rings.append(ring)
            t.start()
        if not (self._in_threads or self._out_threads):
            self._error = "未指定任何输入/输出节点"
            return False
        # 等各流就绪或报错
        deadline = time.time() + 3.0
        for t in (*self._in_threads, *self._out_threads):
            while not t.ready.is_set() and time.time() < deadline:
                time.sleep(0.02)
            if t.error:
                self._error = t.error
                return False
        return True

    def close(self) -> None:
        threads = [*self._in_threads, *self._out_threads]
        if self._far_thread is not None:
            threads.append(self._far_thread)
        for t in threads:
            t.stop()
        for t in threads:
            t.join(timeout=1.0)
        self._in_threads = []
        self._in_rings = []
        self._out_threads = []
        self._out_rings = []
        self._far_thread = None

    def active(self) -> bool:
        started = [t for t in (*self._in_threads, *self._out_threads)]
        if not started:
            return False
        return all(t.is_alive() for t in started)

    def last_error(self) -> str:
        return self._error or "未知错误"

    def sample_rate(self) -> int:
        return 48000 if self.active() else 0

    # ── 数据面 ──

    def read(self, n: int) -> Optional[List[float]]:
        """读取并混合全部输入路（等权平均；无数据返回 None）。"""
        if not self.available:
            return None
        chunks = []
        for ring in self._in_rings:
            got = ring.read(n)
            if got is not None:
                chunks.append(got)
        if not chunks:
            return None
        if len(chunks) == 1:
            return chunks[0]
        acc = [0.0] * len(chunks[0])
        for c in chunks:
            for i, v in enumerate(c):
                acc[i] += v
        k = 1.0 / len(chunks)
        return [v * k for v in acc]

    def write(self, samples) -> None:
        """把降噪后的音频扇出到全部输出路。"""
        if not (self.available and samples):
            return
        for ring in self._out_rings:
            ring.write(samples)

    def write_per_output(self, frames: List[Optional[List[float]]]) -> None:
        """按输出路分别写入（线性多出：每路拿自己链位置上的信号）。

        frames[i] 对应第 i 路输出；None/空 表示该路本帧静音跳过；
        列表短于路数时，多余的路复用最后一个非空帧（单出兼容）。
        """
        if not (self.available and self._out_rings):
            return
        last = None
        for i, ring in enumerate(self._out_rings):
            f = frames[i] if i < len(frames) else None
            if f:
                last = f
                ring.write(f)
            elif last:
                ring.write(last)

    def set_far(self, sink_name: str, enabled: bool) -> bool:
        """运行时开关 AEC far 采集流（监听 <sink>.monitor 源）。"""
        if not self.available:
            return False
        if enabled and sink_name:
            if self._far_thread is not None:
                self._far_thread.stop()
            src = sink_name if sink_name.endswith(".monitor") else f"{sink_name}.monitor"
            self._far_thread = _RecordThread("far", src, self._far_ring)
            self._far_thread.start()
            return True
        if not enabled:
            if self._far_thread is not None:
                self._far_thread.stop()
            self._far_thread = None
        return True

    def read_far(self, n: int) -> Optional[List[float]]:
        if not self.available:
            return None
        return self._far_ring.read(n)
