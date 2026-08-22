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

"""AudioProcessor——组件化引擎的门面。

保持与旧 ctypes 版 aimic.AudioProcessor 完全一致的外部 API，
内部改为 Pipeline 组件链（可按需增删组件）。

默认链路（对齐原 C audio_processor_process 顺序）：
    增益(AGC/pre) → EQ → 限幅 → 降噪(v9) → AEC(aec9) → TSE(tse15)
    → 压缩器 → 录制抽头 → 限幅 → VAD 门 → AGC 测量
"""

import math

import numpy as np

from pvengine.context import (FrameContext, HOP_LENGTH, SAMPLE_RATE,
                              MODE_PASSTHROUGH, MODE_DENOISE, MODE_AEC, MODE_TSE)
from pvengine.pipeline import Pipeline
from pvengine.components.gain import GainStage, AgcMeterStage
from pvengine.components.eq import EQ_BANDS, EQ_FREQS, EqStage
from pvengine.components.misc import (BufferTapStage, ClipStage, CompressorStage,
                                      RecorderTapStage, VadStage)
from pvengine.components.aec import AecStage
from pvengine.components.denoise import DenoiseStage
from pvengine.components.tse import TseStage


class AudioProcessor:
    def __init__(self, pre_gain_db: float, denoise_model_path: str,
                 tse_model_path: str = "", aec_model_path: str = ""):
        self._mode = MODE_DENOISE
        self._far_sample_rate = SAMPLE_RATE
        self._far_rms_target = 0.05
        self._io_in_sr = SAMPLE_RATE
        self._io_out_sr = SAMPLE_RATE

        self._gain = GainStage(pre_gain_db)
        self._eq = EqStage()
        self._clip = ClipStage()
        self._denoise = DenoiseStage(denoise_model_path) if denoise_model_path else None
        self._aec = AecStage(aec_model_path) if aec_model_path else None
        self._stft = None
        self._tse = TseStage(tse_model_path) if tse_model_path else None
        if self._tse:
            self._stft = self._tse.stft
        self._compressor = CompressorStage()
        self._recorder = RecorderTapStage()
        self._vad = VadStage()
        self._viz_in = BufferTapStage()
        self._viz_out = BufferTapStage()
        self._viz_in.enabled = False   # 默认关闭：仅 process_pipeline 内临时开启
        self._viz_out.enabled = False

        # passthrough 模式无需组件：mode 不在其它组件 active_modes 内时全链旁路
        self.pipeline = Pipeline([
            self._viz_in,
            self._gain,
            self._eq,
            self._clip,
            self._denoise,
            self._aec,
            self._tse,
            self._compressor,
            self._recorder,
            ClipStage(),
            self._vad,
            AgcMeterStage(self._gain.agc),
            self._viz_out,
        ])

    # ── 推理后端报告（纯 py 后由 onnxruntime 内部调度，恒报 AVX/OK）──
    def backend_effective(self):
        return 0  # BACKEND_AVX

    def backend_reason(self):
        return 0  # BACKEND_REASON_OK

    def backend_info(self):
        return (self.backend_effective(), self.backend_reason())

    # ── 生命周期 ──
    def cleanup(self):
        self.pipeline.release()

    def reset(self):
        self.pipeline.reset()

    # ── 基础控制 ──
    def set_pre_gain(self, db: float):
        self._gain.set_pre_gain_db(float(db))

    def set_mode(self, mode: int):
        """切模式：只复位 STFT/TSE 流式态（对齐原 C；降噪 RNN 态跨模式保留）。"""
        self._mode = int(mode)
        if self._stft:
            self._stft.reset()
        if self._tse:
            self._tse.engine.reset()

    def get_mode(self):
        return self._mode

    def set_io_sample_rates(self, in_sr, out_sr):
        self._io_in_sr, self._io_out_sr = int(in_sr), int(out_sr)

    def set_viz_enabled(self, enabled: bool):
        self._viz_in.enabled = bool(enabled)
        self._viz_out.enabled = bool(enabled)

    # ── EQ ──
    def set_eq_gains(self, gains):
        if gains:
            self._eq.set_gains(gains)

    def get_eq_freqs(self):
        return list(EQ_FREQS[:EQ_BANDS])

    def get_eq_band_count(self):
        return EQ_BANDS

    def process_eq_only(self, in_samples):
        """前置链预览（增益+EQ），入出等长；供频谱输入侧显示。"""
        x = np.asarray(in_samples, dtype=np.float32).reshape(-1).copy()
        g = self._gain.agc.tick() if self._gain.agc.enabled else self._gain.pre_gain
        x *= np.float32(g)
        x = self._eq.process(x, FrameContext())
        return x.tolist()

    # ── 主链路 ──
    def _run_chain(self, mic: np.ndarray, far=None) -> np.ndarray:
        ctx = FrameContext(mode=self._mode, far=far, far_sample_rate=self._far_sample_rate)
        return self.pipeline.process(mic, ctx)

    def process(self, mic):
        if len(mic) != HOP_LENGTH:
            raise RuntimeError("Input audio chunk length must be equal to hop length (1024)")
        out = self._run_chain(np.asarray(mic, dtype=np.float32))
        return out.tolist()

    def process_with_far(self, mic, far_end):
        if len(mic) != HOP_LENGTH:
            raise RuntimeError("Input audio chunk length must be equal to hop length (1024)")
        far = np.asarray(far_end, dtype=np.float32) if far_end is not None else None
        out = self._run_chain(np.asarray(mic, dtype=np.float32), far)
        return out.tolist()

    # ── 流式 pipeline（网络模式）──
    def process_pipeline(self, raw_input, far_end=None):
        if not len(raw_input):
            return []
        far = np.asarray(far_end, dtype=np.float32) if far_end is not None else None
        acc = np.asarray(raw_input, dtype=np.float32).reshape(-1)
        out_acc: list[float] = []
        # viz 仅在网络管线采集（对齐原 C：本地路径不产生 viz 数据）
        self._viz_in.enabled = True
        self._viz_out.enabled = True
        try:
            while len(acc) >= HOP_LENGTH:
                chunk, acc = acc[:HOP_LENGTH], acc[HOP_LENGTH:]
                out_acc.extend(self._run_chain(chunk, far).tolist())
            if len(acc) >= HOP_LENGTH * 3 // 4:      # 尾帧补零冲刷（对齐原 C）
                orig = len(acc)
                chunk = np.zeros(HOP_LENGTH, dtype=np.float32)
                chunk[:orig] = acc
                out_acc.extend(self._run_chain(chunk, far).tolist())
        finally:
            self._viz_in.enabled = False
            self._viz_out.enabled = False
        return out_acc

    # ── 可视化抽头 ──
    def get_and_clear_viz_input(self):
        return self._viz_in.take()

    def get_and_clear_viz_output(self):
        return self._viz_out.take()

    # ── AEC ──
    def set_aec_enabled(self, enabled: bool):
        if enabled:
            self.set_mode(MODE_AEC)
        elif self._mode == MODE_AEC:
            self.set_mode(MODE_PASSTHROUGH)

    def is_aec_available(self):
        return self._aec is not None

    def set_aec_far_sample_rate(self, sr: int):
        self._far_sample_rate = int(sr) if sr and sr > 0 else SAMPLE_RATE
        if self._aec:
            self._aec.set_far_sample_rate(self._far_sample_rate)

    def get_aec_far_sample_rate(self):
        return self._far_sample_rate

    def set_aec_far_rms_target(self, v: float):
        self._far_rms_target = float(v) if v > 0.0 else 0.05

    def get_aec_far_rms_target(self):
        return self._far_rms_target

    # ── TSE ──
    def set_tse_enabled(self, enabled: bool):
        if enabled:
            self.set_mode(MODE_TSE)
        elif self._mode == MODE_TSE:
            self.set_mode(MODE_PASSTHROUGH)

    def set_tse_reference(self, ref):
        if not ref or not self._tse:
            return
        self._tse.set_reference(np.asarray(ref, dtype=np.float32))

    def is_tse_reference_loaded(self):
        return bool(self._tse and self._tse.has_reference)

    def is_tse_available(self):
        return self._tse is not None

    def get_tse_recording_audio(self):
        return list(self._recorder.frame)

    def set_recording_enabled(self, enabled: bool):
        self._recorder.recording_enabled = bool(enabled)

    def is_recording_enabled(self):
        return self._recorder.recording_enabled

    # ── VAD ──
    def set_vad_enabled(self, enabled: bool):
        self._vad.set_enabled(bool(enabled))

    def is_vad_enabled(self):
        return self._vad.enabled

    def is_vad_active(self):
        return self._vad.active

    def set_vad_threshold(self, dbfs: float):
        self._vad.threshold_linear = 10.0 ** (float(dbfs) / 20.0)

    def get_vad_threshold(self):
        return 20.0 * np.log10(max(self._vad.threshold_linear, 1e-300))

    # ── AGC ──
    def set_agc_enabled(self, enabled: bool, initial_gain_db: float = 0.0):
        self._gain.set_agc_enabled(bool(enabled), float(initial_gain_db))

    def is_agc_enabled(self):
        return self._gain.agc.enabled

    def is_agc_voice_active(self):
        return self._gain.agc.voice_active

    def get_agc_gain_db(self):
        return self._gain.agc.gain_db

    def set_agc_target(self, dbfs: float):
        agc = self._gain.agc
        agc.target_dbfs = float(dbfs)
        agc.target_linear = 10.0 ** (agc.target_dbfs / 20.0)

    def get_agc_target(self):
        return self._gain.agc.target_dbfs

    # ── 压缩器 ──
    def set_compressor_enabled(self, enabled: bool):
        self._compressor.enabled = bool(enabled)

    def is_compressor_enabled(self):
        return self._compressor.enabled

    def set_compressor_threshold(self, db):
        self._compressor.threshold_db = float(db)

    def get_compressor_threshold(self):
        return self._compressor.threshold_db

    def set_compressor_ratio(self, r):
        self._compressor.ratio = max(float(r), 1.0)

    def get_compressor_ratio(self):
        return self._compressor.ratio

    def set_compressor_attack(self, ms):
        self._compressor.det_attack_alpha = 1.0 - np.exp(-1.0 / (float(ms) * 0.001 * self._compressor.fs))

    def get_compressor_attack(self):
        return -math.log(1.0 - self._compressor.det_attack_alpha) / (self._compressor.fs * 0.001) \
            if self._compressor.det_attack_alpha < 1.0 else 0.0

    def set_compressor_release(self, ms):
        self._compressor.det_release_alpha = 1.0 - np.exp(-1.0 / (float(ms) * 0.001 * self._compressor.fs))

    def get_compressor_release(self):
        import math
        a = self._compressor.det_release_alpha
        return -math.log(1.0 - a) / (self._compressor.fs * 0.001) if a < 1.0 else 0.0

    def set_compressor_makeup(self, db):
        self._compressor.makeup_db = float(db)

    def get_compressor_makeup(self):
        return self._compressor.makeup_db

    def set_compressor_knee(self, db):
        self._compressor.knee_db = float(db)

    def get_compressor_knee(self):
        return self._compressor.knee_db
