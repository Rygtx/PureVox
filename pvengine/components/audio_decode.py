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

"""音频文件格式工具——媒体类插件（音效板/音乐播放器）共用。

运行时解码唯一实现：miniaudio（wav/mp3/flac/ogg-vorbis；自包含 C，
跨平台 wheel），直接输出 float32 单声道 48kHz。

选文件时刻经 ensure_playable 归一：miniaudio 不支持的容器/编码
（m4a/mp4/aac/wma/opus…）用 PyAV 一次性转码为 <原名>.purevox.wav
（48kHz 单声道 16bit WAV），调用方自动改用转码后的路径——
运行时永远只走 miniaudio，无静默回退。

本模块零状态、零相互依赖，仅函数。
"""

import os
import wave

import numpy as np

_TARGET_SR = 48000
_TRANSCODE_TAG = ".purevox.wav"


def decode_to_mono_48k(path):
    """miniaudio 解码整文件；不支持/失败返回 None。

    经内存解码（decode）：decode_file 的 char* 路径在 Windows 上按 ANSI
    fopen，中文/非 ASCII 文件名必挂；先自读字节则全平台一致。
    """
    try:
        import miniaudio
        with open(path, "rb") as f:
            data = f.read()
        dec = miniaudio.decode(
            data, output_format=miniaudio.SampleFormat.FLOAT32,
            nchannels=1, sample_rate=_TARGET_SR)
    except Exception:
        return None
    x = np.asarray(dec.samples, dtype=np.float32)
    if x.ndim > 1:
        x = x.reshape(-1)
    return np.ascontiguousarray(x, dtype=np.float32)


def _probe_ok(path) -> bool:
    """miniaudio 能否直接解码（只读文件头，零成本）。"""
    try:
        import miniaudio
        miniaudio.get_file_info(path)
        return True
    except Exception:
        return False


def _transcode_wav48k(path) -> str:
    """PyAV 解码首个音频流 → 48kHz 单声道 16bit WAV（同名 + .purevox.wav）。

    转完临时文件再原子改名；目标已存在且不旧于源文件时直接复用。
    """
    import av
    root, _ext = os.path.splitext(path)
    out = root + _TRANSCODE_TAG
    if (os.path.exists(out)
            and os.path.getmtime(out) >= os.path.getmtime(path)):
        return out
    tmp = out + ".part"
    try:
        with av.open(path) as c:
            streams = [s for s in c.streams if s.type == "audio"]
            if not streams:
                raise RuntimeError("文件中没有音频流")
            res = av.AudioResampler(format="s16", layout="mono",
                                    rate=_TARGET_SR)
            with wave.open(tmp, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(_TARGET_SR)
                for frame in c.decode(streams[0]):
                    for o in (res.resample(frame) or []):
                        w.writeframes(
                            o.to_ndarray().reshape(-1).tobytes())
        os.replace(tmp, out)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return out


def ensure_playable(path) -> str:
    """选文件时刻的格式归一：可解码 → 原路径；否则转码并返回新路径。"""
    if _probe_ok(path):
        return path
    return _transcode_wav48k(path)
