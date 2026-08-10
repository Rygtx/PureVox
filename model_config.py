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
集中管理模型文件名和降噪 STFT 参数。
修改模型时只需改这个文件，所有引用处自动同步。
"""

# ── ONNX 模型文件名 ──
DENOISE_MODEL = "v9_fft2048_band256_epoch_261.onnx"
AEC_MODEL = "aec9_ep0544.onnx"
TSE_MODEL = "tse15_stream_ep_0673.onnx"