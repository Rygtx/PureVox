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
Linux 平台扬声器 loopback 采集（AEC far-end 数据源）。

通过 PulseAudio 的 "Monitor of <sink>" 源捕获系统播放音频。
PulseAudio 的 monitor source 会以输入设备形式出现在 PortAudio/PyAudio
枚举中（设备名通常含 "monitor" 关键字，如
"Monitor of Built-in Audio Analog Stereo"），因此可直接用 PyAudio
打开该输入流，无需 pactl 子进程。

探测回退顺序：
    1. 枚举 PyAudio 输入设备，找名字含 "monitor" 的源（Pulse/PipeWire 通用）。
    2. 找到后用设备原生采样率打开（48k 优先），C++ 侧已按 dev_sr 重采样。
    3. 无 monitor 时 start() 返回 False，上层 AEC 降级（不阻塞主流程）。

接口契约（与 Windows 后端一致）：
    start() -> bool / stop() / read(n) / flush()
    dev_sr (int) / active (bool) / on_device_changed 回调
"""

import threading
import time
from typing import List, Optional, Callable

from .common import RingBuffer, HOP_LENGTH, _module_log

try:
    import pyaudio
except Exception:  # 系统缺 PortAudio 时延后到 start() 再报
    pyaudio = None


class SpeakerCaptureLinux:
    """半双工扬声器采集 — PulseAudio "Monitor of <sink>"（Linux 专用）。"""

    DEVICE_CHECK_INTERVAL = 2.0
    AEC_FAR_SR = 48000

    def __init__(self, on_device_changed: Optional[Callable[[int], None]] = None):
        self._buffer = RingBuffer(HOP_LENGTH * 2)
        self._active = False
        self._lock = threading.Lock()
        self._capture_thread: Optional[threading.Thread] = None
        self._device_check_thread: Optional[threading.Thread] = None
        self._p: Optional[pyaudio.PyAudio] = None
        self._stream = None
        self._dev_sr: int = 48000
        self._dev_ch: int = 1
        self._dev_name: str = "Unknown"
        self._current_monitor: Optional[str] = None
        self._on_device_changed = on_device_changed

    @property
    def active(self) -> bool:
        return self._active

    @property
    def dev_sr(self) -> int:
        return self._dev_sr

    def _find_monitor_device(self) -> Optional[int]:
        """枚举 PyAudio 输入设备，返回扬声器 monitor（loopback）源索引。

        兼容两种命名风格：
        1. PulseAudio: 名字含 "monitor"（如 "Monitor of Built-in Audio Analog Stereo"）；
        2. PipeWire:   sink 同时以输出+输入设备出现（同名），即自带 loopback 输入。
        优先级：默认输出设备的同名 monitor > 名字含 "monitor" > 其它同名 sink loopback。
        """
        if pyaudio is None:
            return None
        input_devs = []
        input_names = set()
        output_names = set()
        for i in range(self._p.get_device_count()):
            dev = self._p.get_device_info_by_index(i)
            name = dev.get('name') or ''
            if dev.get('maxInputChannels', 0) > 0:
                input_devs.append((i, name))
                input_names.add(name)
            if dev.get('maxOutputChannels', 0) > 0:
                output_names.add(name)

        # 0) 默认输出设备的同名 monitor（最贴近系统"正在播放"的源）
        try:
            default_out = self._p.get_default_output_device_info()
            default_out_name = default_out.get('name') or ''
            if default_out_name in input_names:
                for i, name in input_devs:
                    if name == default_out_name:
                        return i
        except Exception:
            pass

        # 1) 同名列里优先含 "Speaker"/"Speaker"/"Analog"/"Output" 语义的真实播放设备
        for i, name in input_devs:
            if name in output_names and any(k in name.lower() for k in ("speaker", "output", "analog", "default")):
                return i

        # 2) 名字含 "monitor" 的输入源
        for i, name in input_devs:
            if 'monitor' in name.lower():
                return i

        # 3) 任意输入设备名与某输出设备名相同（PipeWire loopback）
        for i, name in input_devs:
            if name in output_names:
                return i
        return None

    def _open_monitor(self) -> bool:
        """打开默认 monitor 源。返回 True 表示成功。"""
        if pyaudio is None:
            _module_log("[AEC] Linux: 未安装 PyAudio/PortAudio，无法采集扬声器")
            return False
        try:
            self._p = pyaudio.PyAudio()
            monitor = self._find_monitor_device()
            if monitor is None:
                _module_log("[AEC] Linux: 未找到 PulseAudio monitor 源（无 loopback 设备）")
                self._p.terminate()
                self._p = None
                return False

            dev = self._p.get_device_info_by_index(monitor)
            self._dev_name = dev.get('name') or "Monitor"
            self._dev_sr = int(dev.get('defaultSampleRate') or 48000)
            self._dev_ch = int(dev.get('maxInputChannels') or 1)
            self._buffer = RingBuffer(HOP_LENGTH * 2)

            def _callback(in_data, frame_count, time_info, status):
                if not self._active or not in_data:
                    return (in_data, pyaudio.paContinue)
                try:
                    import struct
                    n = frame_count * self._dev_ch
                    raw = struct.unpack(f'{n}f', in_data)
                    if self._dev_ch == 1:
                        samples = list(raw)
                    else:
                        ch = self._dev_ch
                        samples = [sum(raw[i*ch:(i+1)*ch]) / ch for i in range(frame_count)]
                    self._buffer.write(samples)
                except Exception:
                    pass
                return (in_data, pyaudio.paContinue)

            self._stream = self._p.open(
                format=pyaudio.paFloat32,
                channels=self._dev_ch,
                rate=self._dev_sr,
                input=True,
                frames_per_buffer=HOP_LENGTH,
                stream_callback=_callback,
                input_device_index=monitor,
            )
            self._stream.start_stream()
            self._current_monitor = self._dev_name
            _module_log(f"[AEC] Linux 扬声器采集: {self._dev_name} ({self._dev_sr}Hz, ch={self._dev_ch})")
            return True
        except Exception as e:
            _module_log(f"[AEC] Linux 打开 monitor 失败: {e}")
            try:
                if self._p:
                    self._p.terminate()
            except Exception:
                pass
            self._p = None
            return False

    def start(self) -> bool:
        ok = self._open_monitor()
        if not ok:
            return False
        self._active = True
        self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._capture_thread.start()
        self._device_check_thread = threading.Thread(target=self._device_check_loop, daemon=True)
        self._device_check_thread.start()
        return True

    def _capture_loop(self) -> None:
        """保持流活性（PyAudio 回调已持续写入 buffer）。"""
        while self._active:
            time.sleep(0.5)

    def _device_check_loop(self) -> None:
        """定期检测默认 monitor 是否变化（Linux 版暂为稳定占位）。"""
        while self._active:
            time.sleep(self.DEVICE_CHECK_INTERVAL)

    def stop(self) -> None:
        self._active = False
        if self._device_check_thread:
            self._device_check_thread.join(timeout=1.0)
            self._device_check_thread = None
        if self._capture_thread:
            self._capture_thread.join(timeout=1.0)
            self._capture_thread = None
        try:
            if self._stream:
                self._stream.stop_stream()
                self._stream.close()
                self._stream = None
        except Exception:
            pass
        if self._p:
            try:
                self._p.terminate()
            except Exception:
                pass
            self._p = None

    def read(self, n_samples: int) -> Optional[list]:
        return self._buffer.read(n_samples)

    def flush(self) -> None:
        self._buffer = RingBuffer(HOP_LENGTH * 2)
