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

# -*- coding: utf-8 -*-
"""纯 Python WAV 读写 — 零依赖，支持 int16 与 float32。
替代 soundfile 用于本项目的 WAV I/O。"""

import struct


def read_wav(path):
    """读取 WAV 文件，返回 (samples_list, sample_rate)。

    采样始终为 [-1.0, 1.0] 的单声道浮点列表。
    支持 int16 PCM（format=1）与 float32（format=3）。
    多声道音频按均值混音为单声道。
    """
    with open(path, 'rb') as f:
        # RIFF header: "RIFF" + file_size + "WAVE"
        header = f.read(12)
        if len(header) < 12:
            raise ValueError(f"Not a WAV file (too short): {path}")
        riff, _size, wave = struct.unpack('<4sI4s', header)
        if riff != b'RIFF' or wave != b'WAVE':
            raise ValueError(f"Not a WAV file: {path}")

        fmt_tag = 0
        nchannels = 1
        sample_rate = 0
        bits_per_sample = 0
        data_bytes = b''

        while True:
            chunk_id = f.read(4)
            if len(chunk_id) < 4:
                break
            chunk_size = struct.unpack('<I', f.read(4))[0]

            if chunk_id == b'fmt ':
                fmt_data = f.read(chunk_size)
                fmt_tag = struct.unpack('<H', fmt_data[0:2])[0]
                nchannels = struct.unpack('<H', fmt_data[2:4])[0]
                sample_rate = struct.unpack('<I', fmt_data[4:8])[0]
                # byte_rate  = fmt_data[8:12]
                # block_align = fmt_data[12:14]
                bits_per_sample = struct.unpack('<H', fmt_data[14:16])[0]
            elif chunk_id == b'data':
                data_bytes = f.read(chunk_size)
            else:
                f.seek(chunk_size, 1)  # skip unknown chunks

        if fmt_tag == 1:  # int16 PCM
            n_samples = len(data_bytes) // 2
            ints = struct.unpack(f'<{n_samples}h', data_bytes)
            samples = [s / 32767.0 for s in ints]
        elif fmt_tag == 3:  # float32 IEEE
            n_samples = len(data_bytes) // 4
            samples = list(struct.unpack(f'<{n_samples}f', data_bytes))
        else:
            raise ValueError(f"Unsupported WAV format tag {fmt_tag} in {path}")

        # Downmix to mono
        if nchannels > 1:
            mono = []
            for i in range(0, len(samples), nchannels):
                chunk = samples[i:i + nchannels]
                mono.append(sum(chunk) / len(chunk))
            samples = mono

        return samples, sample_rate


def write_wav(path, samples, sample_rate, bits=16):
    """写入单声道 WAV 文件。

    参数:
        path: 输出文件路径。
        samples: [-1.0, 1.0] 的浮点采样列表。
        sample_rate: 采样率（Hz）。
        bits: 16（int16 PCM）或 32（float32 IEEE）。
    """
    n = len(samples)
    # Clamp to [-1, 1]
    clamped = [max(-1.0, min(1.0, s)) for s in samples]

    if bits == 16:
        ints = [int(round(s * 32767.0)) for s in clamped]
        # Clamp int16 range
        ints = [max(-32768, min(32767, v)) for v in ints]
        data_bytes = struct.pack(f'<{n}h', *ints)
        byte_rate = sample_rate * 2
        block_align = 2
        fmt_tag = 1
        bits_per_sample = 16
    else:  # float32
        data_bytes = struct.pack(f'<{n}f', *clamped)
        byte_rate = sample_rate * 4
        block_align = 4
        fmt_tag = 3      # IEEE float
        bits_per_sample = 32

    data_size = len(data_bytes)
    fmt_chunk_size = 16

    with open(path, 'wb') as f:
        # RIFF header
        f.write(struct.pack('<4sI4s', b'RIFF', 36 + data_size, b'WAVE'))
        # fmt  chunk
        f.write(struct.pack('<4sIHHIIHH',
            b'fmt ', fmt_chunk_size,
            fmt_tag, 1,                # format=PCM, channels=1
            sample_rate, byte_rate,
            block_align, bits_per_sample))
        # data chunk
        f.write(struct.pack('<4sI', b'data', data_size))
        f.write(data_bytes)
