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

"""音乐播放器插件——流式解码（长视频/长音频不占内存）。

后台解码线程 → 3 秒环形缓冲 → 音频线程逐帧消费；内存占用恒定
（环形缓冲 ~576KB，与文件时长无关）。

音源唯一实现：miniaudio 流式生成器（自包含 C，库负责解码/重采样/
跨包细节，float32/mono/48k 直出；格式面 wav/mp3/flac/ogg）。
seek = 重开生成器传 seek_frame（C 级 ma_decoder 定位），无任何回退链。

▶/⏸ 单键可变；seek（秒）；循环；断点续播经 params.resume_sec
由 UI 在暂停/拖动/退出等事件触发持久化，重启后自动续播。
"""

import threading
import time

import numpy as np

from pvengine.components.effect_base import Effect

_TARGET_SR = 48000
_RING_N = _TARGET_SR * 3          # 3 秒环形缓冲
_RING_SAFE = 2048                  # 解码水线（剩余空间低于此即等待）
_PRIME_N = _TARGET_SR // 4         # 预填充 0.25s（启停/seek 防爆音门）


class _Stream:
    """miniaudio 流式解码会话（唯一音源路径，仅解码线程触碰）。

    stream_file 产出 float32/mono/48k 块；seek = 重开生成器并传
    seek_frame（C 级 ma_decoder 定位）；duration 取自 get_file_info。
    """

    def __init__(self, path, seek_seconds=None):
        self.path = path
        self.duration = 0.0
        try:
            import miniaudio
            self.duration = float(
                miniaudio.get_file_info(path).duration)
        except Exception:
            pass
        self._open(max(0.0, float(seek_seconds or 0.0)))

    def _open(self, seconds):
        import miniaudio
        self._gen = miniaudio.stream_file(
            self.path,
            output_format=miniaudio.SampleFormat.FLOAT32,
            nchannels=1, sample_rate=_TARGET_SR,
            frames_to_read=2048,
            seek_frame=int(seconds * _TARGET_SR))

    def close(self):
        self._gen = None

    def read_chunk(self):
        """下一块 float32 mono 48k；EOF 抛 EOFError（loop 由外层 seek(0) 续）。"""
        try:
            return np.asarray(next(self._gen), dtype=np.float32).reshape(-1)
        except StopIteration:
            raise EOFError

    def seek(self, seconds):
        self._open(max(0.0, float(seconds)))


class MusicPlayerPlugin(Effect):
    NAME = "music_player"
    LABEL = "音乐播放器"
    PARAMS = {"volume_db": ("音量 dB", -30.0, 6.0, 0.0, 1.0)}

    def __init__(self, params=None, stage_cache=None):
        self._lock = threading.Lock()
        self._ring = np.zeros(_RING_N, dtype=np.float32)
        # RLock：dec loop 持锁调 _ring_write，其内部再次获取（可重入），
        # 普通 Lock 会自死锁（解码线程冻结、环形缓冲永空）
        self._ring_lock = threading.RLock()
        self._r = self._w = self._count = 0
        self._pos = 0          # 已消费样本（播放位置，音频线程推进）
        self._duration = 0.0   # 秒
        self._path = ""
        self._playing = False
        self._paused = False
        self._loop = False
        self._seek_req = None  # 秒
        self._dec_done = False
        self._volume = 1.0
        self._thread = None
        super().__init__(params)
        self.on_struct_param("path", (params or {}).get("path", ""))
        resume = (params or {}).get("resume_sec", 0.0)
        if resume:
            self.on_struct_param("resume_sec", resume)

    # ── 结构化参数 ──
    def on_struct_param(self, key, value):
        if key == "path":
            path = str(value or "")
            with self._lock:
                if path != self._path:
                    self._path = path
                    self._stop_locked()
                    self._duration = 0.0
                    self._seek_req = None
                    self._dec_done = False
                    self._ensure_thread()
        elif key == "loop":
            with self._lock:
                self._loop = bool(value)
        elif key == "resume_sec":
            try:
                sec = max(0.0, float(value))
            except (TypeError, ValueError):
                return
            with self._lock:
                # 仅在尚未开播时生效（链重建/重启的恢复场景）
                if not self._playing and sec > 0.0:
                    self._seek_req = sec
                    self._pos = int(sec * _TARGET_SR)
                    self._ensure_thread()
        elif key == "seek_sec":
            try:
                self.seek(float(value))
            except (TypeError, ValueError):
                pass

    def _stop_locked(self):
        self._playing = False
        self._paused = False
        self._pos = 0
        with self._ring_lock:
            self._r = self._w = self._count = 0

    def _ensure_thread(self):
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._dec_loop,
                                            daemon=True)
            self._thread.start()

    # ── 控制面 ──
    def play(self):
        with self._lock:
            if not self._path:
                return
            self._ensure_thread()
            if self._duration and self._pos >= self._dur_samples():
                self._seek_req = 0.0
                self._pos = 0
            self._playing = True
            self._paused = False
            self._dec_done = False

    def pause(self):
        with self._lock:
            if self._playing:
                self._paused = True

    def toggle(self):
        with self._lock:
            paused = self._paused
            playing = self._playing
        if playing and not paused:
            self.pause()
        else:
            self.play()

    def stop(self):
        with self._lock:
            self._stop_locked()
            self._seek_req = 0.0
            self._dec_done = False

    def seek(self, seconds):
        with self._lock:
            self._seek_req = float(seconds)
            self._pos = int(max(0.0, seconds) * _TARGET_SR)
            self._dec_done = False
            if self._path:
                self._ensure_thread()

    def set_loop(self, on):
        self.on_struct_param("loop", on)

    def is_playing(self) -> bool:
        with self._lock:
            return self._playing and not self._paused

    def status(self):
        with self._lock:
            return {"playing": self._playing and not self._paused,
                    "pos": self._pos / _TARGET_SR,
                    "dur": self._duration}

    def _dur_samples(self):
        return int(self._duration * _TARGET_SR)

    def on_params_changed(self):
        self._volume = 10.0 ** (self.params["volume_db"] / 20.0)

    # ── 解码线程 ──
    def _dec_loop(self):
        stream = None
        path = ""
        while True:
            # 换文件：旧音源立即释放（位置语义已随 _stop_locked 清零）
            if stream is not None and stream.path != path:
                stream.close()
                stream = None
            with self._lock:
                seek_req = self._seek_req
                self._seek_req = None
                path = self._path
                playing = self._playing and not self._paused
                loop = self._loop
                dec_done = self._dec_done
            try:
                if seek_req is not None:
                    # seek = 音源重开定位（miniaudio 回头 / av 容器重开）
                    if stream is not None:
                        stream.close()
                    stream = _Stream(path, seek_seconds=seek_req)
                    with self._lock:
                        self._duration = max(self._duration, stream.duration)
                        self._dec_done = False
                    with self._ring_lock:
                        self._r = self._w = self._count = 0
                    continue
                if not playing or dec_done:
                    # 不关音源：暂停/未开播必须保留解码位置，
                    # 否则 resume/断点续播会被「重开从头」吞掉
                    time.sleep(0.05)
                    continue
                if stream is None:
                    stream = _Stream(path)
                    with self._lock:
                        self._duration = max(self._duration, stream.duration)
                with self._ring_lock:
                    room = _RING_N - self._count
                if room < _RING_SAFE:
                    time.sleep(0.004)
                    continue
                try:
                    chunk = stream.read_chunk()
                except EOFError:
                    if loop:
                        stream.seek(0.0)   # 头尾相接（miniaudio 重开@0）
                        with self._lock:
                            self._pos = 0
                            self._dec_done = False
                    else:
                        with self._lock:
                            self._dec_done = True
                    continue
                if chunk is None or not len(chunk):
                    continue
                with self._ring_lock:
                    self._ring_write(chunk)
            except Exception:
                with self._ring_lock:
                    self._r = self._w = self._count = 0
                with self._lock:
                    self._dec_done = True
                time.sleep(0.05)

    def _ring_write(self, chunk):
        n = len(chunk)
        if n > _RING_N - self._count:      # 水线兜底：截断防覆写未读数据
            n = _RING_N - self._count
            if n <= 0:
                return
            chunk = chunk[:n]
        end = (self._w + n) % _RING_N
        if end > self._w:
            self._ring[self._w:end] = chunk
        elif end < self._w:
            first = _RING_N - self._w
            self._ring[self._w:] = chunk[:first]
            self._ring[:end] = chunk[first:]
        else:
            self._ring[self._w:] = chunk[:first]
        self._w = end
        with self._ring_lock:
            self._count += n

    def _ring_read(self, out):
        need = len(out)
        got = 0
        while got < need and self._count > 0:
            with self._ring_lock:
                if self._count <= 0:
                    break
                n = min(need - got, _RING_N - self._r, self._count)
                out[got:got + n] = self._ring[self._r:self._r + n]
                self._r = (self._r + n) % _RING_N
                self._count -= n
            got += n
        return got

    # ── 音频面 ──
    def process(self, frame, ctx):
        with self._lock:
            if not self._playing or self._paused:
                return frame
            vol = self._volume
            with self._ring_lock:
                primed = self._count >= _PRIME_N or self._dec_done
        if not primed:
            # 预填充门：启动/seek/链重建后的空环直灌零帧=爆音瑕疵，
            # 先让解码线程灌到水线再开始消费（0.25s 内完成，无感）
            return frame
        out = frame.astype(np.float32, copy=True)
        n = len(out)
        buf = np.zeros(n, dtype=np.float32)
        got = self._ring_read(buf)
        self._pos += got
        with self._lock:
            if self._loop and self._duration:
                dur_s = self._dur_samples()
                if self._pos >= dur_s:
                    self._pos -= dur_s      # 循环回卷，进度 UI 正确
        if got < n:
            with self._lock:
                if self._dec_done and not self._loop:
                    self._playing = False
        if got:
            out[:got] += buf[:got] * np.float32(vol)
            np.clip(out, -1.0, 1.0, out=out)
        return out

    def reset(self):
        self.stop()
