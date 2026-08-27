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

"""音乐播放器插件 v2——流式解码（长视频/长音频不占内存）。

后台解码线程：PyAV 逐包解码 → 重采样(单声道/48k/float32) → 3 秒
环形缓冲；音频线程 process() 只从环形缓冲拉取（实时预算内零文件 IO）。
内存占用恒定（环形缓冲 ~576KB，与文件时长无关）。

▶/⏸ 单键可变；seek（秒）；循环；**断点续播**：位置经 params.resume_sec
由 UI 在暂停/拖动/退出等事件触发持久化（粗粒度即可），重建链/重启后
首次 play 自动从该秒附近继续（容器 seek 对齐关键帧，不追求精确）。
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
    """PyAV 容器会话（仅解码线程触碰）。seek = 容器重开：
    复用旧解复用器状态在长文件大跨度 seek 时可能挂死/产出坏帧。"""

    def __init__(self, path, seek_seconds=None):
        import av
        self._av = av
        self.path = path
        self.container = av.open(path)
        streams = [s for s in self.container.streams if s.type == "audio"]
        if not streams:
            self.container.close()
            raise ValueError("no audio stream")
        self.stream = streams[0]
        self._iter = None
        self.duration = 0.0
        try:
            if self.container.duration:
                self.duration = float(self.container.duration) / 1e6
        except Exception:
            self.duration = 0.0
        if self.stream.duration and not self.duration:
            tb = self.stream.time_base or 1 / 48000
            self.duration = float(self.stream.duration) * float(tb)
        self.resampler = self._make_resampler()
        if seek_seconds is not None:
            self._raw_seek(seek_seconds)

    def _make_resampler(self):
        return self._av.AudioResampler(format="fltp", layout="mono",
                                       rate=_TARGET_SR)

    def seek(self, seconds):
        """重开容器并定位（绝对秒）。"""
        self.container.close()
        self.container = self._av.open(self.path)
        self._raw_seek(seconds)

    def _raw_seek(self, seconds):
        # 不传 stream：容器级 seek 的 offset 单位 = AV_TIME_BASE（微秒）。
        # 若传 stream，offset 单位变成该流 time_base，微秒数值会被
        # 放大数万倍 → seek 被钳到文件头/尾（表现为永远从头/直接 EOF）。
        target = int(max(0.0, seconds) * 1e6)
        try:
            self.container.seek(target, backward=True)
        except Exception:
            self.container.seek(0)
        self._iter = None                  # seek 后必须换新迭代器
        self.resampler = self._make_resampler()

    def read_chunk(self, min_samples=1024, loop=False):
        """解码攒够约 min_samples；非循环 EOF 抛 EOFError；无产出返回 None。

        AAC 起始包可能不产出帧（priming delay），必须跨包继续，
        不能把「单包零输出」当文件结束。loop=True 时容器尾→头无缝续读，
        永不 EOF（微短音频也能持续灌满缓冲）。"""
        total = []
        while sum(len(a) for a in total) < min_samples:
            if self._iter is None:
                self._iter = self.container.decode(self.stream)
            got = False
            try:
                for f in self._iter:
                    got = True
                    for out in (self.resampler.resample(f) or []):
                        a = out.to_ndarray().reshape(-1).astype(np.float32)
                        if len(a):
                            total.append(a)
                    break                 # 每次消费一帧，跨调用续读
            except StopIteration:
                if loop:
                    self.seek(0.0)        # 头尾相接（容器重开定位）
                    continue
                if not got and not total:
                    raise EOFError
                break                     # 先回吐已攒样本，下次调用再报 EOF
        if total:
            return np.concatenate(total)
        return None


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
        while True:
            # 换文件：旧容器立即释放（位置语义已随 _stop_locked 清零）
            if stream is not None and stream.path != path:
                stream.container.close()
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
                    # seek = 容器重开定位（无陈旧解复用状态，杜绝挂死）
                    if stream is not None:
                        stream.container.close()
                    stream = _Stream(path, seek_seconds=seek_req)
                    with self._lock:
                        self._duration = max(self._duration, stream.duration)
                        self._dec_done = False
                    with self._ring_lock:
                        self._r = self._w = self._count = 0
                    continue
                if not playing or dec_done:
                    # 不关容器：暂停/未开播必须保留解码位置，
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
                    chunk = stream.read_chunk(min_samples=_RING_SAFE,
                                              loop=loop)
                except EOFError:
                    with self._lock:
                        self._dec_done = True
                    continue
                if chunk is None or not len(chunk):
                    continue
                with self._ring_lock:
                    self._ring_write(chunk)
            except Exception:
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
