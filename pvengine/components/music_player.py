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

"""音乐播放器插件——链上注入式长曲播放（与音效板相互独立）。

放歌进麦克风：选曲目文件（mp3/flac/ogg/wav 等，miniaudio 解码，
解码失败时 WAV 走标准库兜底），▶ 播放 / ⏸ 暂停 / ■ 停止 / 循环开关，
音量滑杆实时生效。位置语义与音效板一致：挂链尾=后级直通随全部输出。

解码整曲入内存（一首 4 分钟 48k 单声道 float32 ≈ 45MB，可接受）；
位置 pos 仅音频线程推进，控制面经锁操作状态位。
"""

import io
import threading
import wave

import numpy as np

from pvengine.components.effect_base import Effect

_TARGET_SR = 48000


def _decode_any(path):
    """任意音频文件 → float32 单声道 48kHz；失败返回 None。

    优先 miniaudio（mp3/ogg/flac/wav/m4a…）；不可用或解码失败时
    WAV 以标准库 wave 兜底。
    """
    try:
        import miniaudio
        dec = miniaudio.decode_file(path)
        x = np.asarray(dec.samples, dtype=np.float32)
        if dec.nchannels > 1:
            x = x.reshape(-1, dec.nchannels).mean(axis=1).astype(np.float32)
        if dec.sample_rate != _TARGET_SR and len(x) > 1:
            t = np.linspace(0.0, 1.0, max(1, int(len(x) * _TARGET_SR / dec.sample_rate)))
            x = np.interp(t, np.linspace(0.0, 1.0, len(x)), x).astype(np.float32)
        if len(x):
            return x
    except Exception:
        pass
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


class MusicPlayerPlugin(Effect):
    NAME = "music_player"
    LABEL = "音乐播放器"
    PARAMS = {"volume_db": ("音量 dB", -30.0, 6.0, 0.0, 1.0)}

    def __init__(self, params=None, stage_cache=None):
        self._lock = threading.Lock()
        self._data = None      # np.float32 mono 48k
        self._path = ""
        self._pos = 0          # 仅音频线程推进
        self._playing = False
        self._paused = False
        self._loop = False
        self._volume = 1.0
        super().__init__(params)
        self.on_struct_param("path", (params or {}).get("path", ""))

    # ── 结构化参数（UI/配置，非滑杆键经 processor 钩子到达）──
    def on_struct_param(self, key, value):
        if key == "path":
            path = str(value or "")
            with self._lock:
                if path != self._path:
                    self._path = path
                    self._data = None
                    self._pos = 0
                    self._playing = False
                    self._paused = False
        elif key == "loop":
            with self._lock:
                self._loop = bool(value)

    # ── 控制面 ──
    def play(self):
        with self._lock:
            if not self._path:
                return
            if self._data is None:
                self._data = _decode_any(self._path)
            if self._data is None or not len(self._data):
                return
            self._playing = True
            self._paused = False
            if self._pos >= len(self._data):
                self._pos = 0

    def pause(self):
        with self._lock:
            if self._playing:
                self._paused = True

    def resume(self):
        with self._lock:
            if self._playing and self._paused:
                self._paused = False

    def stop(self):
        with self._lock:
            self._playing = False
            self._paused = False
            self._pos = 0

    def set_loop(self, on):
        self.on_struct_param("loop", on)

    def is_playing(self) -> bool:
        with self._lock:
            return self._playing and not self._paused

    def on_params_changed(self):
        self._volume = 10.0 ** (self.params["volume_db"] / 20.0)

    # ── 音频面 ──
    def process(self, frame, ctx):
        with self._lock:
            if not self._playing or self._paused or self._data is None:
                return frame
            data, vol, loop = self._data, self._volume, self._loop
        out = frame.astype(np.float32, copy=True)
        n = len(out)
        pos = self._pos
        take = min(n, len(data) - pos)
        if take <= 0:
            if loop and len(data):
                self._pos = 0
                take = min(n, len(data))
                pos = 0
            else:
                with self._lock:
                    self._playing = False
                self._pos = 0
                return out
        out[:take] += data[pos:pos + take] * np.float32(vol)
        self._pos = pos + take
        np.clip(out, -1.0, 1.0, out=out)
        return out

    def reset(self):
        self.stop()
