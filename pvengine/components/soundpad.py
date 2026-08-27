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

"""音效板插件——链上注入式媒体输入（Soundpad 类功能）。

process(frame) 在自身链位置把当前播放中的音效帧与信号相加：
- 挂链尾（默认追加位置）= 后级直通：音效不经降噪/变声，随全部输出扇出；
- 用户可拖到降噪之前参与处理（位置语义与可视化/输出抽头一致）。

音源仅支持 WAV（wave 标准库解码：8/16/24/32bit 整数与浮点，多声道下混，
非 48kHz 线性重采样到 48k）；懒加载：首次 play 才读文件，垫子列表变更
即清理失效缓存。控制面（UI/热键线程）与音频面（处理线程）经锁分离。
"""

import threading
import wave

import numpy as np

from pvengine.components.effect_base import Effect

_TARGET_SR = 48000


def _load_wav_mono_48k(path):
    """WAV → float32 单声道 48kHz（-1..1）；失败返回 None。"""
    try:
        with wave.open(path, "rb") as w:
            sr = w.getframerate()
            nch = w.getnchannels()
            width = w.getsampwidth()
            raw = w.readframes(w.getnframes())
        if width == 2:
            x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        elif width == 4:
            x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
        elif width == 1:
            x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        elif width == 3:
            b = np.frombuffer(raw, dtype=np.uint8)
            if len(b) % 3:
                b = b[: len(b) // 3 * 3]
            b = b.reshape(-1, 3)
            v = (b[:, 0].astype(np.int32)
                 | (b[:, 1].astype(np.int32) << 8)
                 | (b[:, 2].astype(np.int32) << 16))
            v = np.where(v >= 1 << 23, v - (1 << 24), v)
            x = v.astype(np.float32) / 2147483648.0
        else:
            return None
        if nch > 1:
            x = x.reshape(-1, nch).mean(axis=1)
        if sr != _TARGET_SR and len(x) > 1:
            t = np.linspace(0.0, 1.0, max(1, int(len(x) * _TARGET_SR / sr)))
            x = np.interp(t, np.linspace(0.0, 1.0, len(x)), x).astype(np.float32)
        return np.ascontiguousarray(x, dtype=np.float32)
    except Exception:
        return None


class _Voice:
    """单次播放状态（data 只读共享；pos 仅音频线程推进）。"""

    __slots__ = ("data", "pos", "vol")

    def __init__(self, data, vol):
        self.data = data
        self.pos = 0
        self.vol = vol


class SoundPadPlugin(Effect):
    NAME = "soundpad"
    LABEL = "音效板"
    PARAMS = {"volume_db": ("音量 dB", -30.0, 6.0, 0.0, 1.0)}

    def __init__(self, params=None, stage_cache=None):
        self._lock = threading.Lock()
        self._voices = {}   # pad_index -> _Voice
        self._cache = {}    # path -> np.ndarray | None
        self._pads = []
        self._volume = 1.0
        super().__init__(params)
        self.set_pads((params or {}).get("pads") or [])

    # ── 控制面（UI / 热键线程调用）──
    def set_pads(self, pads):
        with self._lock:
            self._pads = [dict(p) for p in (pads or [])]
            keep = {p.get("path") for p in self._pads}
            self._cache = {k: v for k, v in self._cache.items() if k in keep}
            for i in [i for i in self._voices if i >= len(self._pads)]:
                self._voices.pop(i, None)

    def play(self, index):
        with self._lock:
            if not (0 <= index < len(self._pads)):
                return
            path = self._pads[index].get("path") or ""
            if path not in self._cache:
                self._cache[path] = _load_wav_mono_48k(path)
            data = self._cache.get(path)
            if data is None or not len(data):
                return
            self._voices[index] = _Voice(data, self._volume)

    def stop(self, index):
        with self._lock:
            self._voices.pop(index, None)

    def stop_all(self):
        with self._lock:
            self._voices.clear()

    def pads_count(self) -> int:
        with self._lock:
            return len(self._pads)

    def on_params_changed(self):
        self._volume = 10.0 ** (self.params["volume_db"] / 20.0)

    # ── 音频面（处理线程）──
    def process(self, frame, ctx):
        with self._lock:
            voices = [(i, v) for i, v in self._voices.items()]
        if not voices:
            return frame
        out = frame.astype(np.float32, copy=True)
        n = len(out)
        finished = []
        for idx, v in voices:
            take = min(n, len(v.data) - v.pos)
            if take <= 0:
                finished.append(idx)
                continue
            out[:take] += v.data[v.pos:v.pos + take] * np.float32(v.vol)
            v.pos += take
            if v.pos >= len(v.data):
                finished.append(idx)
        if finished:
            with self._lock:
                for idx in finished:
                    cur = self._voices.get(idx)
                    if cur is not None and cur.pos >= len(cur.data):
                        self._voices.pop(idx, None)
        np.clip(out, -1.0, 1.0, out=out)
        return out

    def reset(self):
        self.stop_all()
