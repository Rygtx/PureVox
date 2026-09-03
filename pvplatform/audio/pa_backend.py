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

"""Windows PortAudio 传输后端（哑传输，与 PwBridge 同形契约）。

拓扑（与 Linux 桥同一模型，取代旧的"全双工单流内联处理 + 三套回调"）：
- 输入：单路 input-only 回调流（mono 48k）→ 环形缓冲（200ms）；
- 输出：每个设备一条 output-only 回调流，回调（设备时钟）→ `out_pull[i](n)`
  拉帧（PlaybackSink，跨时钟域变速消化）→ 上混声道 → 设备。

本后端零缓冲策略、零时钟逻辑——正确性全部在 pvengine.PlaybackSink，
"一个功能只有一条规范实现路径"。输入与输出分属独立流，主输出与额外
输出地位对等（各自 sink 各自时钟域）。
"""

import struct
import threading
from typing import Callable, List, Optional

from pvplatform.audio.common import RingBuffer, _module_log

SAMPLE_RATE = 48000
HOP_LENGTH = SAMPLE_RATE // 100       # 10ms @48kHz = 480

_RING_CAP = SAMPLE_RATE // 5          # 输入环 200ms（吸收调度抖动）


class PaBridge:
    """PortAudio（WASAPI/MME）后端：1 路输入采集 + N 路输出播放。

    open(in_id, out_ids, out_pull)：out_pull[i] = 输出 i 的帧供给
    （PlaybackSink.pull，设备回调线程调用）。
    """

    def __init__(self):
        self._p = None                 # PyAudio 实例（open 传入则不拥有）
        self._owns_p = False
        self._in_stream = None
        self._in_ring = RingBuffer(_RING_CAP)
        self._out_streams: List = []
        self._out_pull: List[Callable] = []
        self._error: str = ""
        self._lock = threading.Lock()
        self._stopped = False

    # ── 连接管理 ──

    def open(self, in_id: Optional[int], out_ids: List[Optional[int]],
             out_pull: List[Callable], p=None) -> bool:
        """打开输入 + 多路输出。p 传入已验证的 PyAudio 实例则不接管其生命周期。

        out_ids 首路允许 None（= 系统默认输出）；其余须为有效设备索引。
        """
        import pyaudio
        self._p = p if p is not None else pyaudio.PyAudio()
        self._owns_p = p is None
        outs: List[Optional[int]] = []
        for dev in (out_ids or []):
            if dev is None:
                if not outs:
                    outs.append(None)       # 首路 None = 系统默认输出
            elif isinstance(dev, int) and dev >= 0 and dev not in outs:
                outs.append(dev)
        self._out_pull = list(out_pull or [])

        try:
            if in_id is not None:
                self._open_input(in_id)
            for i, dev in enumerate(outs):
                self._open_output(i, dev)
        except (OSError, ValueError) as e:
            self._error = str(e)
            self.close()
            return False
        if self._in_stream is None and not self._out_streams:
            self._error = "未指定任何输入/输出设备"
            self.close()
            return False
        # 缓冲/回调就绪后统一启动（回调开流即触发，避免竞态）
        try:
            if self._in_stream is not None:
                self._in_stream.start_stream()
            for s in self._out_streams:
                s.start_stream()
        except (OSError, ValueError) as e:
            self._error = str(e)
            self.close()
            return False
        return True

    def _open_input(self, dev: int) -> None:
        import pyaudio
        self._in_stream = self._p.open(
            format=pyaudio.paFloat32, channels=1,
            rate=SAMPLE_RATE, input=True,
            input_device_index=dev,
            frames_per_buffer=HOP_LENGTH,
            stream_callback=self._input_callback)
        _module_log(f"[PaBridge] 输入设备 #{dev} (mono 48kHz)")

    def _open_output(self, idx: int, dev: Optional[int]) -> None:
        import pyaudio
        ch = 2
        if dev is not None:
            try:
                info = self._p.get_device_info_by_index(dev)
                ch = max(1, int(info.get('maxOutputChannels', 2)))
            except Exception:
                ch = 2
        s = self._p.open(
            format=pyaudio.paFloat32, channels=ch,
            rate=SAMPLE_RATE, output=True,
            output_device_index=dev,
            frames_per_buffer=HOP_LENGTH,
            stream_callback=self._make_output_callback(idx, ch))
        self._out_streams.append(s)
        _module_log(f"[PaBridge] 输出设备 #{dev if dev is not None else '(系统默认)'} ({ch}ch)")

    def close(self) -> None:
        self._stopped = True
        streams = []
        with self._lock:
            if self._in_stream is not None:
                streams.append(self._in_stream)
            streams.extend(self._out_streams)
            self._in_stream = None
            self._out_streams = []
            self._out_pull = []
        for s in streams:
            try:
                s.stop_stream()
            except Exception:
                pass
            try:
                s.close()
            except Exception:
                pass
        if self._p is not None and self._owns_p:
            try:
                self._p.terminate()
            except Exception:
                pass
        self._p = None

    def active(self) -> bool:
        with self._lock:
            streams = ([self._in_stream] if self._in_stream is not None else []) \
                + list(self._out_streams)
        if not streams:
            return False
        for s in streams:
            try:
                if not s.is_active():
                    return False
            except OSError:
                return False
        return True

    def last_error(self) -> str:
        return self._error or "未知错误"

    def sample_rate(self) -> int:
        return SAMPLE_RATE if self.active() else 0

    def output_count(self) -> int:
        return len(self._out_streams)

    # ── 数据面 ──

    def read_each(self, n: int) -> Optional[List[Optional[List[float]]]]:
        """逐路读取输入环（单路后端恒返回一路；无数据返回 None）。
        与 PwBridge.read_each 同形（AEC 行按路取本路 mic 用）。"""
        got = self._in_ring.read(n)
        return [got] if got is not None else None

    def read(self, n: int) -> Optional[List[float]]:
        """读取输入（单路，等权混合退化为直读；无数据返回 None）。"""
        return self._in_ring.read(n)

    # ── 回调（PortAudio 设备线程）──

    def _input_callback(self, in_data, frame_count, time_info, status):
        if self._stopped:
            return (None, pyaudio_paComplete())
        try:
            samples = list(struct.unpack(f'{frame_count}f', in_data))
            self._in_ring.write(samples)
        except Exception as e:
            _module_log(f"[PaBridge] 输入回调异常: {e}")
        return (None, pyaudio_paContinue())

    def _make_output_callback(self, idx: int, ch: int):
        pull = self._out_pull[idx] if idx < len(self._out_pull) else None

        def callback(in_data, frame_count, time_info, status):
            if self._stopped:
                return (None, pyaudio_paComplete())
            try:
                mono = pull(frame_count) if pull is not None else None
                if mono is None or len(mono) < frame_count:
                    mono = list(mono or []) + \
                        [0.0] * (frame_count - len(mono or []))
                if ch > 1:
                    out = [0.0] * (frame_count * ch)
                    pos = 0
                    for v in mono:
                        for _c in range(ch):
                            out[pos] = v
                            pos += 1
                else:
                    out = mono
                return (struct.pack(f'{len(out)}f', *out),
                        pyaudio_paContinue())
            except Exception as e:
                _module_log(f"[PaBridge] 输出回调异常: {e}")
                return (struct.pack(f'{frame_count * ch}f',
                                    *([0.0] * frame_count * ch)),
                        pyaudio_paContinue())
        return callback


def pyaudio_paContinue():
    import pyaudio
    return pyaudio.paContinue


def pyaudio_paComplete():
    import pyaudio
    return pyaudio.paComplete
