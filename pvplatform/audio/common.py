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
平台音频后端共享公共件。

放置各平台后端与上层模块共同依赖的常量、RingBuffer 与日志钩子，
避免 platform.audio 与 audio_processor 之间产生循环导入。
"""

import threading
from typing import List, Optional

SAMPLE_RATE = 48000
HOP_LENGTH = 1024

# 模块级日志钩子（由 UI 经 audio_processor.set_module_log 注入，同时转发到此）
_module_log = print


def set_module_log(func):
    """设置全局日志钩子，同步到 common 与 audio_processor。"""
    global _module_log
    _module_log = func
    try:
        import audio_processor
        audio_processor._module_log = func
    except Exception:
        pass


class RingBuffer:
    """线程安全环形缓冲区，满时自动丢弃旧数据。"""

    def __init__(self, capacity_samples: int) -> None:
        self._capacity: int = capacity_samples
        self._buffer: List[float] = [0.0] * capacity_samples
        self._write_pos: int = 0
        self._read_pos: int = 0
        self._count: int = 0
        self._lock: threading.Lock = threading.Lock()

    def write(self, data: List[float]) -> None:
        """线程安全地写入数据。"""
        with self._lock:
            data_len = len(data)
            if data_len >= self._capacity:
                start = data_len - self._capacity
                self._buffer[:] = data[start:]
                self._write_pos = 0
                self._read_pos = 0
                self._count = self._capacity
                return

            discard = max(0, self._count + data_len - self._capacity)
            if discard > 0:
                self._read_pos = (self._read_pos + discard) % self._capacity
                self._count -= discard

            first_part = min(data_len, self._capacity - self._write_pos)
            self._buffer[self._write_pos:self._write_pos + first_part] = data[:first_part]

            if first_part < data_len:
                self._buffer[:data_len - first_part] = data[first_part:]

            self._write_pos = (self._write_pos + data_len) % self._capacity
            self._count = min(self._count + data_len, self._capacity)

    def read(self, n_samples: int) -> Optional[List[float]]:
        """线程安全地读取 n_samples 个采样。"""
        with self._lock:
            if self._count < n_samples:
                return None

            first_part = min(n_samples, self._capacity - self._read_pos)
            result = self._buffer[self._read_pos:self._read_pos + first_part]

            if first_part < n_samples:
                result = result + self._buffer[:n_samples - first_part]

            self._read_pos = (self._read_pos + n_samples) % self._capacity
            self._count -= n_samples
            return result

    def available(self) -> int:
        """线程安全地获取可用采样数。"""
        with self._lock:
            return self._count

    def read_latest(self, n_samples: int) -> Optional[List[float]]:
        """读取最新 n_samples 个采样，丢弃更旧数据；无数据时返回 None。"""
        with self._lock:
            if self._count == 0:
                return None
            skip = max(0, self._count - n_samples)
            if skip > 0:
                self._read_pos = (self._read_pos + skip) % self._capacity
                self._count -= skip
            to_read = min(n_samples, self._count)
            if to_read == 0:
                return None
            first_part = min(to_read, self._capacity - self._read_pos)
            result = self._buffer[self._read_pos:self._read_pos + first_part]
            if first_part < to_read:
                result = result + self._buffer[:to_read - first_part]
            self._read_pos = (self._read_pos + to_read) % self._capacity
            self._count -= to_read
            return result
