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

"""音频文件解码工具——媒体类插件（音效板/音乐播放器）共用的叶子函数。

decode_to_mono_48k(path) → float32 单声道 48kHz（-1..1），失败返回 None。
三段回退链，覆盖 Soundpad 同级格式面（wav/mp3/flac/ogg/m4a/aac/opus/wma…）：
1. miniaudio：wav/mp3/flac/ogg-vorbis（自包含 C，首选最快）；
2. PyAV（可选依赖）：长尾容器/编码（m4a/aac/wma/opus/…）；
3. wave 标准库：16/24/32bit 整数与 8bit 无符号 PCM 兜底。
本模块零状态、零相互依赖，仅函数。
"""

import wave

import numpy as np

_TARGET_SR = 48000


def _resample_48k(x, sr):
    if sr == _TARGET_SR or len(x) <= 1:
        return np.ascontiguousarray(x, dtype=np.float32)
    t = np.linspace(0.0, 1.0, max(1, int(len(x) * _TARGET_SR / sr)))
    return np.interp(t, np.linspace(0.0, 1.0, len(x)), x).astype(np.float32)


def _to_mono(x, nch):
    if nch > 1:
        x = x.reshape(-1, nch).mean(axis=1)
    return np.ascontiguousarray(x, dtype=np.float32)


def _decode_wave(path):
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
        if not len(b):
            return None
        b = b.reshape(-1, 3)
        v = (b[:, 0].astype(np.int32)
             | (b[:, 1].astype(np.int32) << 8)
             | (b[:, 2].astype(np.int32) << 16))
        v = np.where(v >= 1 << 23, v - (1 << 24), v)
        x = v.astype(np.float32) / 2147483648.0
    else:
        return None
    return _resample_48k(_to_mono(x, nch), sr)


def _decode_miniaudio(path):
    """miniaudio：输出直接指定 float32/单声道/48k（默认 s16 整数域，
    幅度是 32767 量级的原始整数，直接用会削波失真）。"""
    import miniaudio
    dec = miniaudio.decode_file(
        path, output_format=miniaudio.SampleFormat.FLOAT32,
        nchannels=1, sample_rate=_TARGET_SR)
    x = np.asarray(dec.samples, dtype=np.float32)
    if x.ndim > 1:
        x = x.reshape(-1)
    return np.ascontiguousarray(x, dtype=np.float32)


def _decode_av(path):
    import av
    with av.open(path) as c:
        streams = [s for s in c.streams if s.type == "audio"]
        if not streams:
            return None
        resampler = av.AudioResampler(format="fltp", layout="mono",
                                      rate=_TARGET_SR)
        chunks = []
        for frame in c.decode(streams[0]):
            for f in (resampler.resample(frame) or []):
                arr = f.to_ndarray().reshape(-1).astype(np.float32)
                if len(arr):
                    chunks.append(arr)
    if not chunks:
        return None
    return np.concatenate(chunks)


def decode_to_mono_48k(path):
    """三段回退解码；全部失败返回 None。"""
    for fn in (_decode_miniaudio, _decode_av, _decode_wave):
        try:
            x = fn(path)
        except Exception:
            x = None
        if x is not None and len(x):
            return x
    return None
