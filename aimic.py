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
#
# aimic — 音频引擎兼容垫片。
#
# 历史演进：aimic.c（纯 C 共享库）的 ctypes 绑定 → 纯 Python 组件化引擎
# （pvengine 包，numpy + onnxruntime）。本文件仅保留旧模块名与公共 API，
# 全部实现转发到 pvengine；新代码请直接使用 pvengine。

from pvengine import (
    SAMPLE_RATE,
    HOP_LENGTH,
    NFFT,
    FREQ,
    MODE_PASSTHROUGH,
    MODE_DENOISE,
    MODE_AEC,
    MODE_TSE,
    FrameContext,
    Pipeline,
    AudioProcessor,
    RingBuffer,
    Resampler,
    SPECTRUM_NUM_BANDS,
    compute_spectrum,
    spectrum_warmup,
)

# 旧 libsamplerate 枚举占位（Resampler 已忽略该参数）
SRC_SINC_FASTEST = 0

# 推理后端报告常量：纯 py 后由 onnxruntime 内部调度，恒报 AVX/OK
BACKEND_AVX = 0
BACKEND_SSE = 1
BACKEND_NPU = 2

BACKEND_REASON_OK = 0
BACKEND_REASON_NPU_UNAVAILABLE = 1
BACKEND_REASON_NPU_NO_ENTRY = 2

__all__ = [
    "SAMPLE_RATE", "HOP_LENGTH", "NFFT", "FREQ",
    "MODE_PASSTHROUGH", "MODE_DENOISE", "MODE_AEC", "MODE_TSE",
    "FrameContext", "Pipeline", "AudioProcessor", "RingBuffer", "Resampler",
    "SPECTRUM_NUM_BANDS", "compute_spectrum", "spectrum_warmup",
    "SRC_SINC_FASTEST",
    "BACKEND_AVX", "BACKEND_SSE", "BACKEND_NPU",
    "BACKEND_REASON_OK", "BACKEND_REASON_NPU_UNAVAILABLE", "BACKEND_REASON_NPU_NO_ENTRY",
]
