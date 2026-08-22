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

"""VAD / 压缩器 / 限幅 / 录制抽头 / 可视化抽头 组件。"""

import math
import numpy as np

from pvengine.context import FrameContext, MODE_TSE
from pvengine.dsp.core import clip_buffer
from pvengine.stages.base import Stage


class ClipStage(Stage):
    """±1 限幅 + NaN/Inf 清零。"""

    name = "clip"

    def process(self, frame, ctx: FrameContext):
        return clip_buffer(frame)


class VadStage(Stage):
    """RMS 硬门：默认 -45 dBFS 门限，连续 20ms 开启、静默 250ms 后关闭，
    关闭期间整帧清零（对齐原 C VadGate(-45, 20, 250, 48000, 480)）。"""

    name = "vad"

    def __init__(self, threshold_dbfs: float = -45.0, onset_ms: float = 20.0,
                 hang_ms: float = 250.0, fs: float = 48000.0, hop: int = 480):
        super().__init__()
        self.threshold_linear = 10.0 ** (threshold_dbfs / 20.0)
        self.onset_frames = max(1, int(onset_ms / 1000.0 * fs / hop))
        self.hang_frames = max(1, int(hang_ms / 1000.0 * fs / hop))
        self.active = False
        self.voice_cnt = 0
        self.silence_cnt = 0

    def set_enabled(self, enabled: bool):
        if enabled and not self.enabled:
            self.reset()
        self.enabled = enabled

    def reset(self):
        self.active = False
        self.voice_cnt = 0
        self.silence_cnt = 0

    def process(self, frame, ctx: FrameContext):
        rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64)))) if len(frame) else 0.0
        if rms > self.threshold_linear:
            self.voice_cnt += 1
            self.silence_cnt = 0
        else:
            self.silence_cnt += 1
            self.voice_cnt = 0
        if not self.active and self.voice_cnt >= self.onset_frames:
            self.active = True
        elif self.active and self.silence_cnt >= self.hang_frames:
            self.active = False
        return frame if self.active else np.zeros_like(frame)


class CompressorStage(Stage):
    """前馈压缩器（原 C Compressor 移植）：
    默认 threshold -20dB / ratio 3 / 检测 attack 15ms release 180ms /
    knee 8dB / makeup +4dB；增益平滑 attack 25ms release 220ms；输出 tanh 软限幅。"""

    name = "compressor"

    def __init__(self, fs: float = 48000.0, threshold_db: float = -20.0,
                 ratio: float = 3.0, attack_ms: float = 15.0, release_ms: float = 180.0,
                 knee_db: float = 8.0, makeup_db: float = 4.0):
        super().__init__()
        self.fs = fs
        self.threshold_db = threshold_db
        self.ratio = max(ratio, 1.0)
        self.knee_db = knee_db
        self.makeup_db = makeup_db
        self.det_attack_alpha = 1.0 - math.exp(-1.0 / (attack_ms * 0.001 * fs))
        self.det_release_alpha = 1.0 - math.exp(-1.0 / (release_ms * 0.001 * fs))
        self.gain_attack_alpha = 1.0 - math.exp(-1.0 / (25.0 * 0.001 * fs))
        self.gain_release_alpha = 1.0 - math.exp(-1.0 / (220.0 * 0.001 * fs))
        self.envelope = 0.0
        self.gain_smooth = 1.0

    def reset(self):
        self.envelope = 0.0
        self.gain_smooth = 1.0

    def process(self, frame, ctx: FrameContext):
        out = frame.astype(np.float32, copy=True)
        env = self.envelope
        gsm = self.gain_smooth
        for i in range(len(out)):
            x2 = float(out[i]) * float(out[i])
            alpha = self.det_attack_alpha if x2 > env else self.det_release_alpha
            env += alpha * (x2 - env)
            env_db = 10.0 * math.log10(env) if env > 1e-12 else -120.0
            over = env_db - self.threshold_db
            gr_db = 0.0
            if over > 0.0:
                if self.knee_db > 0.0 and over < self.knee_db:
                    t = over / self.knee_db
                    gr_db = (1.0 / self.ratio - 1.0) * over * t * 0.5
                else:
                    gr_db = (1.0 / self.ratio - 1.0) * over
            gain_target = 10.0 ** ((gr_db + self.makeup_db) / 20.0)
            if gain_target < gsm:
                gsm = self.gain_attack_alpha * gain_target + (1.0 - self.gain_attack_alpha) * gsm
            else:
                gsm = self.gain_release_alpha * gain_target + (1.0 - self.gain_release_alpha) * gsm
            out[i] = np.float32(math.tanh(float(out[i]) * gsm))
        self.envelope = env
        self.gain_smooth = gsm
        return out


class RecorderTapStage(Stage):
    """录制抽头：保存最新一帧到内部缓冲（TSE 模式不记录）。"""

    name = "recorder_tap"
    active_modes = frozenset({0, 1, 2})

    def __init__(self):
        super().__init__()
        self.recording_enabled = False
        self.frame = []

    def process(self, frame, ctx: FrameContext):
        if self.recording_enabled:
            self.frame = frame.tolist()
        else:
            self.frame = []
        return frame


class BufferTapStage(Stage):
    """可视化/调试缓冲抽头：有界累积，take 时清空取走。"""

    name = "buffer_tap"

    def __init__(self, max_samples: int = 48000 * 5):
        super().__init__()
        self._max = max_samples
        self._acc: list[float] = []
        # 注意：恒生效（不受模式影响），由调用方决定是否排空；
        # 有界上限从根上修复旧 C 版 viz 只增不减的内存隐患
        self.active_modes = None

    def process(self, frame, ctx: FrameContext):
        self._acc.extend(frame.tolist())
        overflow = len(self._acc) - self._max
        if overflow > 0:
            del self._acc[:overflow]
        return frame

    def take(self, cap: int = 1 << 16):
        got = self._acc[:cap]
        del self._acc[:len(got)]
        return got
