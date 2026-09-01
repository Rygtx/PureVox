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

"""AEC 组件——purevox_aec_202609_cpx 模型（回声消除）。

模型契约（202609 三件套统一，STFT 在模型图内，引擎零 DSP）：
  输入  mic_hop [1,480] + far_hop [1,480] + cache_in [1,215504]
  输出  enh_hop [1,480]（滞后 1 hop）+ cache_out [1,215504]
far-end 非恒 48k 时组件内部用 Resampler 重采样（沿用原行为：
ratio = 48000/far_sr，静音预热，取不满一帧则本帧直通）。
"""

import numpy as np

from pvengine.context import HOP_LENGTH
from pvengine.dsp.core import make_session, state_dim
from pvengine.dsp.resampler import Resampler
from pvengine.stages.base import Stage

_CACHE_FALLBACK = 215504


class AecEngine:
    def __init__(self, model_path: str):
        self.sess = make_session(model_path)
        self.in_names = [i.name for i in self.sess.get_inputs()]
        self.out_names = [o.name for o in self.sess.get_outputs()]
        dim = state_dim(self.sess, "cache_in", _CACHE_FALLBACK)
        self.cache = np.zeros((1, dim), dtype=np.float32)

    def process_frame(self, mic: np.ndarray, far: np.ndarray) -> np.ndarray:
        """mic/far 各 480 样本（48kHz，10ms）→ 输出 480。"""
        outs = self.sess.run(self.out_names, {
            "mic_hop": np.asarray(mic, dtype=np.float32).reshape(1, -1),
            "far_hop": np.asarray(far, dtype=np.float32).reshape(1, -1),
            "cache_in": self.cache,
        })
        od = dict(zip(self.out_names, outs))
        self.cache = np.asarray(od["cache_out"], dtype=np.float32).reshape(1, -1)
        return np.asarray(od["enh_hop"], dtype=np.float32).reshape(-1)

    def reset(self):
        self.cache[:] = 0.0

    def release(self):
        self.sess = None


class AecStage(Stage):
    name = "aec"
    active_modes = frozenset({2})

    def __init__(self, model_path: str):
        super().__init__()
        self.engine = AecEngine(model_path)
        self._far_resampler: Resampler | None = None
        self.far_sample_rate = 48000

    def set_far_sample_rate(self, sr: int):
        sr = int(sr) if sr and sr > 0 else 48000
        self.far_sample_rate = sr
        if sr != 48000:
            self._far_resampler = Resampler()
            # 静音预热（沿用原行为：让插值历史先建立）
            self._far_resampler.process(np.zeros(HOP_LENGTH, dtype=np.float32),
                                        48000.0 / sr)
        else:
            self._far_resampler = None

    def process(self, frame, ctx):
        far = ctx.far
        far_sr = ctx.far_sample_rate
        if far is None or len(far) == 0:
            return frame
        if self._far_resampler is not None and far_sr != 48000:
            got = self._far_resampler.process(far, 48000.0 / float(far_sr))
            if len(got) < HOP_LENGTH:
                return frame
            far_ref = np.asarray(got[:HOP_LENGTH], dtype=np.float32)
        elif len(far) >= HOP_LENGTH:
            far_ref = np.asarray(far[:HOP_LENGTH], dtype=np.float32)
        else:
            return frame
        return self.engine.process_frame(frame, far_ref)

    def reset(self):
        self.engine.reset()

    def release(self):
        self.engine.release()
