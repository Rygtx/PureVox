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
PureVox 音频处理核心模块。

提供以下功能：
- 音频常量（采样率、帧大小、hop 长度）
- 线程安全环形缓冲区
- 实时音频流处理线程
- 音频设备枚举与 WASAPI Core Audio 辅助工具
"""

import io
import math
import os
import socket
import struct
import threading
import time
import wave
from typing import Any, List, Optional, Callable, Tuple

# PyAudio（PortAudio）仅 Windows/macOS 后端使用；Linux 走原生 PipeWire，
# 允许无 PyAudio 环境运行。引用点都在非 Linux 执行路径内。
try:
    import pyaudio
except ImportError:
    pyaudio = None  # type: ignore

# Module-level log hook — set by ui.py to sync console + UI log.
_module_log = print

# 平台抽象层：SpeakerCapture 工厂 + 公共件（RingBuffer/常量/日志，避免循环导入）
from pvplatform import IS_LINUX
from pvplatform.audio import create_speaker_capture
from pvplatform.audio.common import set_module_log as _set_common_log

if IS_LINUX:
    from pvplatform.audio.pwpipe_client import PwBridge as _PwBridge
    from pvplatform.audio.pwpipe_client import list_sources as _pw_sources
    from pvplatform.audio.pwpipe_client import list_destinations as _pw_dests


def get_local_lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def set_module_log(func):
    global _module_log
    _module_log = func
    _set_common_log(func)


def _rms_of(samples: List[float]) -> float:
    """计算样本列表的 RMS。"""
    if not samples:
        return 0.0
    return (sum(x * x for x in samples) / len(samples)) ** 0.5

try:
    from pvengine import AudioProcessor, RingBuffer, Resampler
    ENGINE_AVAILABLE = True
except ImportError:
    ENGINE_AVAILABLE = False
    _module_log("[音频] 引擎模块 pvengine 不可用（缺 numpy/onnxruntime？）")

SAMPLE_RATE = 48000
FRAME_SIZE = 2048
HOP_LENGTH = 1024


def _bridge_stream_count(bridge):
    """当前桥接的播放路数（用于线性多出对齐判断）。"""
    try:
        return len(bridge._out_rings)
    except Exception:
        return 0


TSE_SAMPLE_RATE = 48000        # TSE15 模型采样率 (48kHz)
TSE_HOP_LENGTH = 1024          # 1024 samples @ 48kHz = 21.3ms


#  Speaker loopback capture — 平台抽象（WASAPI / PulseAudio）
#  实现见 platform.audio.speaker_capture_{win,linux,macos}
# ═══════════════════════════════════════════════════════════════

SpeakerCapture = create_speaker_capture  # 工厂别名：按平台返回后端实例


class RingBuffer:
    """线程安全环形缓冲区，满时自动丢弃旧数据。"""

    def __init__(self, capacity_samples: int) -> None:
        self._capacity: int = capacity_samples
        self._buffer: List[float] = [0.0] * capacity_samples
        self._write_pos: int = 0
        self._read_pos: int = 0
        self._count: int = 0
        self._lock: threading.Lock = threading.Lock()

    def write(self, data: List[float]) -> None:
        """线程安全地写入数据。"""
        with self._lock:
            data_len = len(data)
            if data_len >= self._capacity:
                start = data_len - self._capacity
                self._buffer[:] = data[start:]
                self._write_pos = 0
                self._read_pos = 0
                self._count = self._capacity
                return

            discard = max(0, self._count + data_len - self._capacity)
            if discard > 0:
                self._read_pos = (self._read_pos + discard) % self._capacity
                self._count -= discard

            first_part = min(data_len, self._capacity - self._write_pos)
            self._buffer[self._write_pos:self._write_pos + first_part] = data[:first_part]

            if first_part < data_len:
                self._buffer[:data_len - first_part] = data[first_part:]

            self._write_pos = (self._write_pos + data_len) % self._capacity
            self._count = min(self._count + data_len, self._capacity)

    def read(self, n_samples: int) -> Optional[List[float]]:
        """线程安全地读取 n_samples 个采样。"""
        with self._lock:
            if self._count < n_samples:
                return None

            first_part = min(n_samples, self._capacity - self._read_pos)
            result = self._buffer[self._read_pos:self._read_pos + first_part]

            if first_part < n_samples:
                result = result + self._buffer[:n_samples - first_part]

            self._read_pos = (self._read_pos + n_samples) % self._capacity
            self._count -= n_samples
            return result

    def available(self) -> int:
        """线程安全地获取可用采样数。"""
        with self._lock:
            return self._count

    def read_latest(self, n_samples: int) -> Optional[List[float]]:
        """读取最新 n_samples 个采样，丢弃更旧数据；无数据时返回 None。"""
        with self._lock:
            if self._count == 0:
                return None
            # Skip to latest data if we have more than n_samples
            skip = max(0, self._count - n_samples)
            if skip > 0:
                self._read_pos = (self._read_pos + skip) % self._capacity
                self._count -= skip
            # Read available samples (up to n_samples)
            to_read = min(n_samples, self._count)
            if to_read == 0:
                return None
            first_part = min(to_read, self._capacity - self._read_pos)
            result = self._buffer[self._read_pos:self._read_pos + first_part]
            if first_part < to_read:
                result = result + self._buffer[:to_read - first_part]
            self._read_pos = (self._read_pos + to_read) % self._capacity
            self._count -= to_read
            return result


class AudioThread(threading.Thread):
    """本地设备的音频处理线程。"""

    def __init__(self, input_id: Optional[int], output_id: int,
                 process_frame: Callable[[List[float]], List[float]],
                 hop_length: int,
                 processor: object = None,
                 network_source = None,
                 api_type: int = 13,
                 ready_msg: str = "",
                 extra_output_ids=None) -> None:
        super().__init__(name='AudioProcessor', daemon=True)
        self._ready_msg: str = ready_msg
        self._api_type: int = api_type
        self._input_id: Optional[int] = input_id
        self._output_id: int = output_id
        self._network_source = network_source  # RemoteAudioSource for network input mode
        # Linux：原生 PipeWire 输入/输出。格式协商 F32 单声道 48000Hz，
        # PipeWire 负责重采样与声道转换（模型永远拿 48k 单声道）。
        self._use_pw = False
        self._pw_bridge: Optional[_PwBridge] = None
        self._pw_ports: Tuple[List[str], List[str]] = ([], [])  # (输入列表, 输出列表)
        self._channels: int = 1                     # 设备通道数，在 _create_stream 中确定
        self._output_buffer = None  # L3:网络输出缓冲 (RingBuffer 或 None)
        self._output_stream: Optional[Any] = None
        self._out_channels: int = 1
        self._accum: List[float] = []  # 回调帧累积（frame_count→hop_length）
        # 多输出扇出（原「监听」机制的泛化）：主输出之外的全部播放设备
        self._extra_out_ids: List[int] = [i for i in (extra_output_ids or [])
                                          if i is not None]
        self._extra_out_streams: List[Any] = []
        self._extra_out_buffers: List[Any] = []
        self._extra_out_chs: List[int] = []
        self._process_frame: Callable[[List[float]], List[float]] = process_frame
        self._hop_length: int = hop_length
        self._stop_event: threading.Event = threading.Event()
        # 流就绪事件：_create_stream 成功后 set()，失败时记录 _start_error
        self._ready_event: threading.Event = threading.Event()
        self._start_error: Optional[str] = None
        self._p: Optional[Any] = None
        self._stream: Optional[Any] = None
        self._vu_peak: float = 0.0  # L4:VU峰值快照（最新帧dBFS）
        self._spectrum_in: RingBuffer = RingBuffer(SAMPLE_RATE * 2)   # L4:频谱输入 2s
        self._spectrum_out: RingBuffer = RingBuffer(SAMPLE_RATE * 2)  # L4:频谱输出 2s
        self._last_output_frame: List[float] = []  # 输出读空时回放
        self._viz_enabled: bool = True  # 频谱/VU 开关（窗口最小化时暂停）
        self._lock: threading.Lock = threading.Lock()  # Lock for stream operations
        self.processor: object = processor  # Reference to processor for dynamic control
        self._tse_hook: Optional[Callable[[List[float]], None]] = None  # TSE audio hook
        self._recording_hook: Optional[Callable[[List[float]], None]] = None  # 录音捕获钩子

        # ── AEC（SpeakerCapture 采集扬声器音频，AEC 处理在引擎管线内）──
        self._aec_enabled: bool = False
        self._speaker_capture: Optional[SpeakerCapture] = None
        self._aec_far_sink: str = ""  # AEC far 端手动选择的扬声器 sink（node.name）
        self._aec_warmup_frames: int = 0  # AEC 启动预填充计数器，>0 时喂静音积累远端缓冲
        self._aec_far_gain: float = 0.5623  # -5dB 固定衰减，防止远端回声过强

    def set_pw_ports(self, input_names: List[str], output_names: List[str]) -> None:
        """设置 Linux PipeWire 输入/输出节点名列表（node.name）。

        多输入自动混音，多输出扇出同一路降噪音频。须在 run() 之前调用；
        start_audio_stream 会据此选择原生 PipeWire 后端。
        """
        self._use_pw = bool(input_names or output_names) and IS_LINUX
        self._pw_ports = (list(input_names or []), list(output_names or []))

    def set_aec_far_sink(self, sink_name: str) -> bool:
        """运行时切换 AEC far 端扬声器 sink（Linux PipeWire，capture.sink 重挂）。

        AEC 未启用时仅记录目标；已启用时先停后开以换到新 sink。
        """
        self._aec_far_sink = sink_name or ""
        if self._speaker_capture is not None and IS_LINUX:
            was_enabled = self._aec_enabled
            if was_enabled:
                self.set_aec_enabled(False)
            if was_enabled:
                if self.set_aec_enabled(True):
                    _module_log(f"[AEC] far 端扬声器切换: {self._aec_far_sink or '(自动物理扬声器)'}")
                    return True
                return False
        return True

    def set_bypass(self, bypass: bool) -> None:
        """直通模式：跳过引擎处理，纯重采样透传。"""
        self._bypass = bypass

    def wait_ready(self, timeout: float = 3.0) -> bool:
        """等待音频流创建完成。返回 True 表示成功，False 表示失败/超时。"""
        if not self._ready_event.wait(timeout):
            return False
        return self._start_error is None

    def set_viz_enabled(self, enabled: bool) -> None:
        """暂停/恢复频谱和 VU 更新（窗口最小化时调用）。"""
        self._viz_enabled = enabled
        if not enabled:
            # 清空积压数据，避免恢复时瞬间刷新大量旧数据
            for buf in (self._spectrum_in, self._spectrum_out):
                if buf: buf.read_latest(HOP_LENGTH)

    def set_aec_enabled(self, enabled: bool, onnx_path: str = "") -> bool:
        """启用/禁用 AEC 扬声器采集。AEC 处理在引擎管线内完成。"""
        if enabled == self._aec_enabled:
            return True
        if enabled:
            try:
                if IS_LINUX and self._use_pw:
                    # Linux：AEC far 走原生 PipeWire（监听扬声器 sink 的 monitor 源）
                    far_sink = self._aec_far_sink
                    self._speaker_capture = SpeakerCapture(
                        on_device_changed=self._on_speaker_device_changed,
                        pw_bridge=self._pw_bridge,
                        far_sink=far_sink,
                    )
                else:
                    self._speaker_capture = SpeakerCapture(
                        on_device_changed=self._on_speaker_device_changed
                    )
                if not self._speaker_capture.start():
                    _module_log("[AEC] speaker capture failed")
                    self._speaker_capture = None
                    return False
                # 告知引擎 far-end 采样率（内部重采样到 48kHz）
                dev_sr = self._speaker_capture.dev_sr
                self.processor.set_aec_far_sample_rate(dev_sr)
                self.processor.set_aec_enabled(True)
                self._aec_enabled = True
                self._aec_warmup_frames = 8  # ~170ms 预填充，让远端缓冲积累
                return True
            except Exception as e:
                import traceback
                _module_log(f"[AEC] enable failed: {e}")
                _module_log(traceback.format_exc())
                self._aec_enabled = False
                if self._speaker_capture:
                    self._speaker_capture.stop()
                    self._speaker_capture = None
                return False
        else:
            self._aec_enabled = False
            self.processor.set_aec_enabled(False)
            if self._speaker_capture:
                self._speaker_capture.stop()
                self._speaker_capture = None
            _module_log("[AEC] disabled")
            return True

    def _on_speaker_device_changed(self, new_dev_sr: int) -> None:
        """回调：扬声器设备切换，更新 引擎 端的远端采样率。"""
        self.processor.set_aec_far_sample_rate(new_dev_sr)
        _module_log(f"[AEC] far-end sample rate updated: {new_dev_sr}Hz")

    def _validate_and_fix_device(self, device_id: int, want_input: bool) -> int:
        """验证设备 ID 是否仍然存在；如已拔出则自动查找备选设备。
        
        返回原 ID（若有效）或备选设备 ID。若无任何可用设备则返回 None。
        
        NOTE: 使用重试机制避免 WASAPI 瞬态枚举失败误判设备拔出。
        """
        if self._p is None or device_id < 0:
            return None

        # 重试：PortAudio 在设备枚举期间可能瞬态失败，连续失败才判拔出
        RETRIES = 5
        RETRY_DELAY = 0.05  # 50ms
        for attempt in range(RETRIES):
            try:
                self._p.get_device_info_by_index(device_id)
                return device_id  # 设备仍在
            except Exception:
                if attempt < RETRIES - 1:
                    time.sleep(RETRY_DELAY)
        # 全部重试失败 — 设备确实已拔出 → 查找备选
        fallback = None
        try:
            for i in range(self._p.get_device_count()):
                try:
                    info = self._p.get_device_info_by_index(i)
                except Exception:
                    continue
                if want_input and info.get('maxInputChannels', 0) > 0:
                    fallback = i
                    break
                if not want_input and info.get('maxOutputChannels', 0) > 0:
                    fallback = i
                    break
        except Exception:
            pass
        if fallback is not None:
            try:
                name = self._p.get_device_info_by_index(fallback).get('name', str(fallback))
            except Exception:
                name = str(fallback)
            _module_log(
                f"[设备] {'输入' if want_input else '输出'}设备 {device_id} 已拔出，"
                f"自动切换到 {name}")
        return fallback

    def _create_stream(self) -> None:
        """创建音频流。
        
        当 self._input_id 为 None 时（网络音频输入模式），只创建输出流。
        """
        # Linux PipeWire 模式：不创建任何 PortAudio 流（输入/输出/监听全走
        # 原生 PipeWire）。这里打开 PwBridge 并协商 F32 单声道 48000Hz；
        # 后续由 _pw_loop（本地）或 _network_loop（网络）读取→降噪→写入。
        if IS_LINUX and self._use_pw:
            self._last_output_frame = [0.0] * HOP_LENGTH
            self._output_buffer = RingBuffer(SAMPLE_RATE)
            self._output_stream = None
            self._extra_out_streams = []
            in_names, out_names = self._pw_ports
            bridge = _PwBridge()
            if not bridge.open(in_names, out_names):
                err = bridge.last_error()
                bridge.close()
                raise OSError(f"PipeWire 连接失败: {err}")
            sr = bridge.sample_rate()
            if sr and sr != SAMPLE_RATE:
                bridge.close()
                raise OSError(f"PipeWire 协商采样率为 {sr}Hz（应为 {SAMPLE_RATE}Hz）")
            self._pw_bridge = bridge
            _module_log(f"[PipeWire] 输入x{len(in_names)}: {','.join(in_names) or '(未选)'}  "
                        f"输出x{len(out_names)}: {','.join(out_names) or '(未选)'}")
            _module_log("[PipeWire] F32 单声道 48000Hz 协商（PipeWire 负责重采样/声道转换）")
            return

        if IS_LINUX:
            # Linux 强制原生 PipeWire：不落 PortAudio/PyAudio 备选
            raise OSError("Linux 音频仅支持原生 PipeWire（未配置输入/输出节点或 pvpipe 不可用）")

        self._p = pyaudio.PyAudio()
        if self._p is None:
            raise OSError("PyAudio 初始化失败")

        in_id = self._input_id
        out_id = self._validate_and_fix_device(self._output_id, want_input=False)
        fixed_extras = []
        for dev in self._extra_out_ids:
            fixed = self._validate_and_fix_device(dev, want_input=False)
            if fixed is not None and fixed != out_id:
                fixed_extras.append(fixed)
        self._extra_out_ids = fixed_extras

        if out_id is not None:
            try:
                out_info = self._p.get_device_info_by_index(out_id)
                out_max_ch = int(out_info.get('maxOutputChannels', 2))
                out_def_sr = int(out_info.get('defaultSampleRate', SAMPLE_RATE))
            except Exception:
                out_info, out_max_ch, out_def_sr = {}, 2, SAMPLE_RATE
        else:
            out_info, out_max_ch, out_def_sr = {}, 2, SAMPLE_RATE

        if out_def_sr <= 0: out_def_sr = SAMPLE_RATE

        # 网络输入模式：只创建输出流（48kHz），由 _network_loop 写缓冲。
        if in_id is None:
            self._last_output_frame = [0.0] * HOP_LENGTH
            self._output_buffer = RingBuffer(SAMPLE_RATE)  # 1秒缓冲吸收网络抖动
            self._output_buffer.write([0.0] * HOP_LENGTH * 3)  # ~64ms 预填充防 underrun
            self._out_channels = max(1, out_max_ch)
            _module_log(f"[输出] {out_max_ch}ch（网络输入模式）")
            for out_ch in (1, out_max_ch):
                try:
                    self._output_stream = self._p.open(
                        format=pyaudio.paFloat32, channels=out_ch,
                        rate=SAMPLE_RATE, output=True,
                        output_device_index=out_id,
                        frames_per_buffer=HOP_LENGTH,
                        stream_callback=self._get_output_callback()
                    )
                    self._out_channels = out_ch
                    self._output_stream.start_stream()
                    _module_log(f"[网络输出] {out_ch}ch")
                    break
                except (OSError, ValueError) as e:
                    last_err = e
            else:
                raise OSError(f"无法打开输出流: {last_err}")
            self._create_extra_outputs()
            return

        in_id = self._validate_and_fix_device(self._input_id, want_input=True)

        # 查询输入设备信息
        if in_id is not None:
            try:
                in_info = self._p.get_device_info_by_index(in_id)
                in_max_ch = int(in_info.get('maxInputChannels', 2))
                in_def_sr = int(in_info.get('defaultSampleRate', SAMPLE_RATE))
            except Exception:
                in_info, in_max_ch, in_def_sr = {}, 2, SAMPLE_RATE
        else:
            in_info, in_max_ch, in_def_sr = {}, 2, SAMPLE_RATE

        if in_def_sr <= 0: in_def_sr = SAMPLE_RATE

        # 全双工尝试列表
        common_ch = [1]
        if in_max_ch >= 2 or out_max_ch >= 2:
            common_ch.append(2)
        ab = max(1, min(in_max_ch, out_max_ch))
        if ab not in common_ch:
            common_ch.append(ab)

        # ── 全双工（48kHz）──
        try_full_duplex = True

        if try_full_duplex:
            self._half_duplex = False
            fpb = HOP_LENGTH  # 1024 frames = 21.3ms
            last_err = None
            for ch in common_ch:
                self._channels = ch
                try:
                    self._stream = self._p.open(
                        format=pyaudio.paFloat32, channels=self._channels,
                        rate=SAMPLE_RATE, input=True, output=True,
                        input_device_index=in_id,
                        output_device_index=out_id,
                        frames_per_buffer=fpb,
                        stream_callback=self._get_full_duplex_callback()
                    )
                    self._stream.start_stream()
                    break
                except (OSError, ValueError) as e:
                    last_err = e
            else:
                self._stream = None
        else:
            self._stream = None
            last_err = None

        if self._stream is None:
            err_msg = str(last_err).split(']')[-1].strip() if last_err else "采样率不匹配"
            raise OSError(f"无法以 48kHz 全双工打开音频流 ({err_msg})")

        self._create_extra_outputs()

    def _get_full_duplex_callback(self) -> Callable:
        """全双工模式的音频回调（输入+输出同流）。"""
        def callback(in_data: bytes, frame_count: int, time_info, status) -> Tuple[bytes, int]:
            if self._stop_event.is_set():
                return (None, pyaudio.paComplete)

            try:
                # === 第1层：前处理 —— 设备格式 → 48kHz单声道 ===
                total_samples = frame_count * self._channels
                raw = list(struct.unpack(f'{total_samples}f', in_data))

                if self._channels > 1:
                    # 多声道下混为单声道
                    mono_chunk = [0.0] * frame_count
                    for i in range(frame_count):
                        s = 0.0
                        for ch in range(self._channels):
                            s += raw[i * self._channels + ch]
                        mono_chunk[i] = s / self._channels
                else:
                    mono_chunk = raw

                # 全双工仅 48kHz，累积后分批处理（hop=1024）
                self._accum.extend(mono_chunk)
                denoised_48k: List[float] = []
                while len(self._accum) >= self._hop_length:
                    chunk = self._accum[:self._hop_length]
                    del self._accum[:self._hop_length]
                    if self._aec_enabled and self._speaker_capture:
                        far_need = int(HOP_LENGTH * self._speaker_capture.dev_sr / SAMPLE_RATE)
                        far_data = self._speaker_capture.read(far_need)
                        if far_data is not None:
                            far_data = [x * self._aec_far_gain for x in far_data]
                        if self._aec_warmup_frames > 0:
                            self._aec_warmup_frames -= 1
                            far_silence = [0.0] * far_need
                            out = self.processor.process_with_far(chunk, far_silence)
                        elif far_data is not None:
                            out = self.processor.process_with_far(chunk, far_data)
                        else:
                            far_silence = [0.0] * far_need
                            out = self.processor.process_with_far(chunk, far_silence)
                    else:
                        out = self._process_frame(chunk)
                    denoised_48k.extend(out)

                # 补齐/裁剪到 frame_count
                if len(denoised_48k) < frame_count:
                    denoised_48k += [0.0] * (frame_count - len(denoised_48k))
                elif len(denoised_48k) > frame_count:
                    # 多余的存回 accumulator 供下次回调用（正常不会超过）
                    denoised_48k = denoised_48k[:frame_count]

                # 录音捕获：降噪后的音频（TSE 前）
                if self._recording_hook is not None:
                    try:
                        self._recording_hook(list(denoised_48k))
                    except Exception:
                        pass

                # TSE 已在 引擎 noise_reduction 中频域处理，不需要 Python 侧介入
                output_48k = denoised_48k

                # 全双工仅 48kHz，直通
                denoised = output_48k

                # TSE录音钩子 → 取 post-gain+clip 后、TSE 前的音频
                if self._tse_hook is not None:
                    try:
                        pre_tse = self.processor.get_tse_recording_audio()
                        if pre_tse:
                            self._tse_hook(list(pre_tse))
                    except Exception:
                        pass
                for buf in self._extra_out_buffers:
                    buf.write(list(denoised_48k))
                # VU 峰值快照（避免缓存整段波形）
                self._vu_peak = max(abs(x) for x in denoised_48k) if denoised_48k else 0.0
                # 写入频谱缓冲 (输入=pre_gain+EQ后, 输出=降噪后)
                try:
                    eq_in = self.processor.process_eq_only(list(mono_chunk))
                    self._spectrum_in.write(list(eq_in))
                except Exception:
                    _module_log(f"[频谱] process_eq_only 失败（全双工回调），回退 raw 输入")
                    self._spectrum_in.write(list(mono_chunk))
                self._spectrum_out.write(list(denoised_48k))

                if self._channels > 1:
                    # 单声道上混为多声道
                    processed = [0.0] * (frame_count * self._channels)
                    for i in range(frame_count):
                        for ch in range(self._channels):
                            processed[i * self._channels + ch] = denoised[i]
                else:
                    processed = denoised

                return (struct.pack(f'{len(processed)}f', *processed), pyaudio.paContinue)
            except Exception as e:
                _module_log(f"[音频] 全双工回调异常: {e}")
                return (struct.pack(f'{frame_count * self._channels}f',
                       *([0.0] * frame_count * self._channels)), pyaudio.paContinue)

        return callback

    def _get_output_callback(self) -> Callable:
        """网络输出回调：从 output_buffer 读取 → 上混 → 输出"""
        def callback(in_data: bytes, frame_count: int, time_info, status) -> Tuple[bytes, int]:
            if self._stop_event.is_set():
                return (None, pyaudio.paComplete)

            try:
                buf = self._output_buffer
                if buf is None:
                    return (struct.pack(f'{frame_count * self._out_channels}f',
                           *([0.0] * frame_count * self._out_channels)), pyaudio.paContinue)
                mono_data = buf.read(frame_count)
                if not mono_data:
                    last = self._last_output_frame
                    if last is not None and len(last) >= frame_count:
                        mono_data = list(last[:frame_count])
                    else:
                        mono_data = [0.0] * frame_count
                else:
                    self._last_output_frame = list(mono_data)

                # 上混为多声道
                if self._out_channels > 1:
                    out = [0.0] * (frame_count * self._out_channels)
                    for i in range(frame_count):
                        for ch in range(self._out_channels):
                            out[i * self._out_channels + ch] = mono_data[i]
                else:
                    out = mono_data

                return (struct.pack(f'{len(out)}f', *out), pyaudio.paContinue)
            except Exception as e:
                _module_log(f"[音频] 输出回调异常: {e}")
                return (struct.pack(f'{frame_count * self._out_channels}f',
                       *([0.0] * frame_count * self._out_channels)), pyaudio.paContinue)

        return callback

    def _get_extra_callback(self, idx: int, ch: int) -> Callable:
        """额外输出流回调：从对应缓冲读取降噪音频 → 上混 → 输出。"""
        def callback(in_data: bytes, frame_count: int, time_info, status) -> Tuple[bytes, int]:
            if self._stop_event.is_set():
                return (None, pyaudio.paComplete)

            try:
                buf = self._extra_out_buffers[idx]
                mono_data = buf.read(frame_count) if buf else None
                if not mono_data:
                    mono_data = [0.0] * frame_count

                if ch > 1:
                    out_data = [0.0] * (frame_count * ch)
                    for i in range(frame_count):
                        for c in range(ch):
                            out_data[i * ch + c] = mono_data[i]
                else:
                    out_data = mono_data

                return (struct.pack(f'{len(out_data)}f', *out_data), pyaudio.paContinue)
            except Exception as e:
                _module_log(f"[音频] 输出回调异常: {e}")
                return (struct.pack(f'{frame_count * ch}f',
                       *([0.0] * frame_count * ch)), pyaudio.paContinue)

        return callback


    def _close_stream_safely(self, stream: Optional[Any], stream_name: str = "stream") -> None:
        if stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except Exception as e:
                print(f"[WARN] Error closing {stream_name}: {e}")

    def _create_extra_outputs(self) -> None:
        """创建全部额外输出流（多输出扇出）。失败设备静默跳过。"""
        self._extra_out_streams = []
        self._extra_out_buffers = []
        self._extra_out_chs = []
        for dev_id in self._extra_out_ids:
            if dev_id is None or dev_id < 0 or dev_id == self._output_id:
                continue
            try:
                info = self._p.get_device_info_by_index(dev_id)
                ch = max(1, int(info.get('maxOutputChannels', 1)))
            except Exception:
                continue
            try:
                s = self._p.open(
                    format=pyaudio.paFloat32,
                    channels=ch,
                    rate=SAMPLE_RATE,
                    output=True,
                    output_device_index=dev_id,
                    frames_per_buffer=HOP_LENGTH,
                    stream_callback=self._get_extra_callback(
                        len(self._extra_out_streams), ch))
                s.start_stream()
                self._extra_out_streams.append(s)
                self._extra_out_buffers.append(RingBuffer(SAMPLE_RATE // 5))
                self._extra_out_chs.append(ch)
                _module_log(f"[多输出] 已连接额外输出设备 #{dev_id} ({ch}ch)")
            except (OSError, ValueError) as e:
                _module_log(f"[多输出] 设备 #{dev_id} 打开失败: {e}")

    def run(self) -> None:
        """运行音频处理线程。"""
        self._stop_event.clear()
        try:
            self._create_stream()
        except Exception as e:
            _module_log(f"[音频] 音频流创建失败（线程将退出）: {e}")
            import traceback as _tb
            _module_log(f"[音频] 堆栈: {_tb.format_exc()}")
            self._start_error = str(e)
            self._ready_event.set()  # 通知等待方：失败
            return

        self._ready_event.set()  # 通知等待方：成功

        if self._ready_msg:
            _module_log(f"[启动] {self._ready_msg}")

        is_network = self._input_id is None and self._network_source is not None

        if is_network:
            self._network_loop()
        elif IS_LINUX and self._use_pw:
            self._pw_loop()
        else:
            self._health_check_loop()

        _module_log("[DEV] 音频线程已退出")

    # ── 网络输入处理循环 ──
    def _network_loop(self):
        """网络源 → 累积 → process(HOP_LENGTH) → output_buffer (带时钟漂移补偿 + 速率匹配)"""
        MAX_ACC = HOP_LENGTH * 8            # 硬上限 ~171ms
        TARGET_ACC = HOP_LENGTH * 5         # 目标缓冲 ~107ms
        CROSSFADE_LEN = HOP_LENGTH // 4     # 交叉淡入淡出长度 ~5.3ms
        STALL_TIMEOUT = 0.15               # 欠载判定：150ms 无新数据
        TARGET_OBUF = HOP_LENGTH * 3        # 输出缓冲目标 ~64ms（超出则丢帧限速）
        IDEAL_FRAME_S = HOP_LENGTH / SAMPLE_RATE  # ~21.3ms
        acc: List[float] = []
        last_viz = 0
        # ── 诊断 ──
        fc = 0                # 总帧数
        fc_drop = 0           # 丢帧次数（时钟漂移补偿）
        fc_underrun = 0       # 输出underrun次数
        fc_pad = 0            # 零填充次数
        t_start = time.time()
        t_last_diag = t_start
        t_last_frame = t_start
        t_last_data = t_start  # 最后一次收到网络数据的时间
        acc_smoothed = 0.0    # 低通滤波后的缓冲区大小
        while not self._stop_event.is_set():
            # ── Flush 信号：客户端请求清空缓冲 ──
            if self._network_source and self._network_source.flush_event.is_set():
                self._network_source.flush_event.clear()
                acc.clear()
                # 先锁 _last_output_frame 为静音，防输出回调在 clear→write 窗口回退到旧有声帧
                self._last_output_frame = [0.0] * HOP_LENGTH
                if self._output_buffer is not None:
                    self._output_buffer.clear()
                    self._output_buffer.write([0.0] * HOP_LENGTH * 3)
                _module_log("[网络] 缓冲已清空 (flush)")
                t_last_data = time.time()

            if self._network_source:
                navail = self._network_source.available()
                if navail > 0:
                    chunk = self._network_source.read(navail)
                    if chunk:
                        acc.extend(chunk)
                        t_last_data = time.time()
                        if fc < 5: _module_log(f"[dbg] 网络读入 {len(chunk)} samples, acc={len(acc)}")

            # ── 时钟漂移补偿：缓冲区溢出时交叉淡入淡出丢弃旧帧 ──
            if len(acc) > MAX_ACC:
                drop_amount = len(acc) - TARGET_ACC
                if drop_amount > CROSSFADE_LEN * 2:
                    keep_start = drop_amount
                    fade_end = keep_start
                    fade_start = keep_start - CROSSFADE_LEN
                    target_start = keep_start
                    if fade_start >= 0:
                        for i in range(CROSSFADE_LEN):
                            w = (i + 1) / CROSSFADE_LEN
                            acc[target_start + i] = (
                                acc[fade_start + i] * (1.0 - w)
                                + acc[target_start + i] * w
                            )
                    acc = acc[drop_amount:]
                else:
                    acc = acc[-TARGET_ACC:]
                fc_drop += 1

            # ── 低通滤波平滑缓冲区大小 ──
            acc_smoothed = acc_smoothed * 0.95 + len(acc) * 0.05

            # ── 欠载检测：长时间无新数据且缓冲区不足 → 淡出 + 零填充 ──
            stall_duration = time.time() - t_last_data
            if stall_duration > STALL_TIMEOUT and 0 < len(acc) < HOP_LENGTH:
                fade_len = min(64, len(acc))
                for i in range(fade_len):
                    w = 1.0 - (i + 1) / (fade_len + 1)
                    acc[-fade_len + i] *= w
                need = HOP_LENGTH - len(acc)
                acc.extend([0.0] * need)
                fc_pad += 1

            while len(acc) >= HOP_LENGTH:
                raw = acc[:HOP_LENGTH]
                acc = acc[HOP_LENGTH:]
                t0 = time.time()
                rms_in = (sum(x*x for x in raw) / HOP_LENGTH) ** 0.5
                out = self.processor.process_pipeline(raw)
                dt = time.time() - t0
                if out:
                    rms_out = (sum(x*x for x in out) / len(out)) ** 0.5
                    if IS_LINUX and self._use_pw and self._pw_bridge is not None:
                        # PipeWire 模式：直接写输出环（溢出由 引擎侧丢新，不膨胀）
                        self._pw_bridge.write(out)
                    else:
                        self._output_buffer.write(out)
                        for buf in self._extra_out_buffers:
                            buf.write(list(out))
                        # ── 速率补偿：输出缓冲过大时主动丢帧，防止延迟膨胀 ──
                        obuf_avail = self._output_buffer.available()
                        if obuf_avail > TARGET_OBUF * 2:
                            drop_frames = (obuf_avail - TARGET_OBUF) // HOP_LENGTH
                            for _ in range(drop_frames):
                                self._output_buffer.read(HOP_LENGTH)
                            fc_drop += 1
                        elif obuf_avail < TARGET_OBUF // 2 and len(acc) < HOP_LENGTH:
                            # 输出缓冲偏低，稍等数据
                            time.sleep(0.002)
                    if fc < 5: _module_log(f"[dbg] process: rms_in={rms_in:.4f} rms_out={rms_out:.4f} out_len={len(out)} dt={dt*1000:.1f}ms")
                else:
                    fc_underrun += 1
                    if fc < 5: _module_log(f"[dbg] process: rms_in={rms_in:.4f} → out 空!")
                fc += 1
                t_last_frame = time.time()
                now = t_last_frame
                if self._viz_enabled and now - last_viz > 0.05:
                    viz_in = self.processor.get_and_clear_viz_input()
                    if viz_in: self._spectrum_in.write(viz_in)
                    viz_out = self.processor.get_and_clear_viz_output()
                    if viz_out:
                        self._vu_peak = max(abs(x) for x in viz_out)
                        self._spectrum_out.write(viz_out)
                    if viz_in or viz_out: last_viz = now

                # 每2秒诊断
                if now - t_last_diag >= 2.0:
                    elapsed = now - t_start
                    fps = fc / elapsed if elapsed > 0 else 0
                    ideal = SAMPLE_RATE / HOP_LENGTH
                    rms = (sum(x*x for x in raw) / HOP_LENGTH) ** 0.5
                    _module_log(
                        f"[网络] fps={fps:.1f}/{ideal:.1f}  "
                        f"proc={dt*1000:.1f}ms  acc={len(acc)}  "
                        f"acc_s={acc_smoothed:.0f}  "
                        f"obuf={self._output_buffer.available()}  "
                        f"net={self._network_source.available() if self._network_source else 0}  "
                        f"drop={fc_drop}  ur={fc_underrun}  pad={fc_pad}  "
                        f"rms={rms:.4f}  dev=48kHz"
                    )
                    t_last_diag = now

            if len(acc) < HOP_LENGTH:
                time.sleep(0.001)

    # ── Linux 原生 PipeWire 输入/输出循环 ──

    def _pw_loop(self):
        """PipeWire 模式（本地输入）：input 采集 → 降噪 → output 播放。

        进程回调只做无锁环形缓冲搬运；本循环在 Python 线程
        读取→降噪→写入，2s 环形缓冲吸收调度抖动。桥接已在 _create_stream 打开。
        """
        bridge = self._pw_bridge
        if bridge is None:
            self._start_error = "PipeWire 桥接未就绪"
            return
        acc: List[float] = []
        fc = 0
        try:
            while not self._stop_event.is_set():
                data = bridge.read(HOP_LENGTH)
                if not data:
                    time.sleep(0.002)
                    continue
                acc.extend(data)

                if self._viz_enabled:
                    viz_in = self.processor.process_eq_only(list(data))
                    self._spectrum_in.write(viz_in)

                while len(acc) >= HOP_LENGTH:
                    chunk = acc[:HOP_LENGTH]
                    del acc[:HOP_LENGTH]
                    if self._aec_enabled and self._speaker_capture:
                        far_need = int(HOP_LENGTH * self._speaker_capture.dev_sr / SAMPLE_RATE)
                        far_data = self._speaker_capture.read(far_need)
                        if far_data is not None:
                            far_data = [x * self._aec_far_gain for x in far_data]
                        if self._aec_warmup_frames > 0:
                            self._aec_warmup_frames -= 1
                            out = self.processor.process_with_far(chunk, [0.0] * far_need)
                        elif far_data is not None:
                            out = self.processor.process_with_far(chunk, far_data)
                        else:
                            out = self.processor.process_with_far(chunk, [0.0] * far_need)
                    else:
                        out = self._process_frame(chunk)

                    if self._recording_hook is not None:
                        try:
                            self._recording_hook(list(out))
                        except Exception:
                            pass
                    # 线性多出：链内有 output 位置抽头时，每路写自己位置
                    # 上的信号；无抽头（旧配置/单出）回退统一扇出
                    out_frames = self.processor.take_output_frames()
                    if len(out_frames) == _bridge_stream_count(bridge):
                        bridge.write_per_output(out_frames)
                    else:
                        bridge.write(list(out))
                    # VU 电平显示降噪输出（out）的峰值
                    self._vu_peak = max(abs(x) for x in out) if out else 0.0
                    if self._viz_enabled:
                        self._spectrum_out.write(list(out))
                    fc += 1

                if fc % 1000 == 0:
                    _module_log(f"[PipeWire] 处理 {fc} 帧 (rms={_rms_of(data):.4f})")
        finally:
            _module_log("[PipeWire] 循环退出")

    # ── 健康检查循环 ──


    def _health_check_loop(self):
        """全双工模式：只做健康检查，音频由回调驱动"""
        main_fail = 0
        out_fail = 0
        monitor_fail = 0
        monitor_retry_count = 0
        MAX_CONSECUTIVE_FAILS = 3
        MAX_MONITOR_RETRIES = 3

        while not self._stop_event.is_set():
            time.sleep(0.2)

            with self._lock:
                try:
                    stream_active = self._stream is not None and self._stream.is_active()
                except OSError:
                    stream_active = False
                try:
                    out_active = (self._output_stream is None or self._output_stream.is_active())
                except OSError:
                    out_active = True  # 网络模式下才有 _output_stream
                try:
                    extras_active = all(
                        s is None or s.is_active() for s in self._extra_out_streams)
                except OSError:
                    extras_active = False

            if not stream_active:
                main_fail += 1
                if main_fail >= MAX_CONSECUTIVE_FAILS:
                    _module_log("[音频] 输入流已停止")
                    break
            else:
                main_fail = 0

            if not out_active:
                out_fail += 1
                if out_fail >= MAX_CONSECUTIVE_FAILS:
                    _module_log("[音频] 输出流已停止")
                    break
            else:
                out_fail = 0

            if not extras_active and self._extra_out_streams:
                monitor_fail += 1
                if monitor_fail >= MAX_CONSECUTIVE_FAILS:
                    _module_log("[音频] 额外输出流已断开，尝试重建...")
                    monitor_fail = 0
                    monitor_retry_count += 1
                    if monitor_retry_count <= MAX_MONITOR_RETRIES:
                        with self._lock:
                            for s in self._extra_out_streams:
                                self._close_stream_safely(s, "extra output stream")
                            self._create_extra_outputs()
                        if any(s is not None and s.is_active()
                               for s in self._extra_out_streams):
                            _module_log("[音频] 额外输出流已重连")
                            monitor_retry_count = 0
                    else:
                        _module_log("[音频] 额外输出流重连次数用尽")
                        for s in self._extra_out_streams:
                            self._close_stream_safely(s, "extra output stream")
                        self._extra_out_streams = []
            else:
                monitor_fail = 0

    def stop(self) -> None:
        """优雅地停止音频线程。"""
        self._stop_event.set()

        self._close_stream_safely(self._stream, "main stream")
        self._close_stream_safely(self._output_stream, "output stream")
        for s in self._extra_out_streams:
            self._close_stream_safely(s, "extra output stream")

        # Wait for PortAudio callbacks to finish executing.
        # stream.stop_stream() only prevents *new* callbacks; any callback
        # already in-flight continues to run on PortAudio's thread.  The
        # callback accesses engine buffers that
        # will be freed later by processor.cleanup().  Without this delay
        # the still-running callback can dereference freed memory → crash.
        time.sleep(0.05)

        if self.is_alive():
            self.join(timeout=1.0)

        self._cleanup()

    def _cleanup(self) -> None:
        """释放音频资源。"""
        self._close_stream_safely(self._stream, "main stream")
        self._stream = None
        self._close_stream_safely(self._output_stream, "output stream")
        self._output_stream = None
        self._output_buffer = None
        for s in self._extra_out_streams:
            self._close_stream_safely(s, "extra output stream")
        self._extra_out_streams = []
        self._extra_out_buffers = []

        # PipeWire 桥接：关闭流（断开全部连接）
        if self._pw_bridge is not None:
            try:
                self._pw_bridge.close()
            except Exception:
                pass
            self._pw_bridge = None

        if self._p:
            try:
                self._p.terminate()
            except Exception as e:
                _module_log(f"[音频] _cleanup() PortAudio 终止异常: {e}")
            self._p = None

    def set_tse_audio_hook(self, hook: Optional[Callable[[List[float]], None]]) -> None:
        """设置 TSE 音频钩子回调函数"""
        self._tse_hook = hook

    def set_recording_hook(self, hook: Optional[Callable[[List[float]], None]]) -> None:
        """设置录音捕获钩子（捕获降噪后、TSE 前的音频）"""
        self._recording_hook = hook

    def set_tse_enabled(self, enabled: bool) -> None:
        """动态启用/禁用 TSE 处理（委托给 引擎 AudioProcessor）"""
        if hasattr(self.processor, 'set_tse_enabled'):
            self.processor.set_tse_enabled(enabled)

    def set_recording_enabled(self, enabled: bool) -> None:
        """启用/禁用录音模式（委托给 引擎 AudioProcessor）"""
        if hasattr(self.processor, 'set_recording_enabled'):
            self.processor.set_recording_enabled(enabled)

    def is_recording_enabled(self) -> bool:
        if hasattr(self.processor, 'is_recording_enabled'):
            return self.processor.is_recording_enabled()
        return False

    def set_tse_reference_wav(self, wav_path: str) -> None:
        """设置 TSE 参考音频（线程启动后动态重设时用）。"""
        load_tse_reference(self.processor, wav_path)

    def is_tse_reference_loaded(self) -> bool:
        """检查 TSE 参考音频是否已加载"""
        if hasattr(self.processor, 'is_tse_reference_loaded'):
            return self.processor.is_tse_reference_loaded()
        return False


# ═══════════════════════════════════════════════════════════════
#  TSE 参考音频工具（录音器 / WAV 转换 / WSOLA 时间压缩）
# ═══════════════════════════════════════════════════════════════

TSE_SAMPLE_RATE = 48000           # TSE 模型要求 48kHz
HOOK_SAMPLE_RATE = 48000
HOOK_HOP_LENGTH = 480

RECORD_DURATION = 10.0            # 参考录音总时长（秒）
TARGET_REF_SECS = 2.0              # WSOLA 时间压缩目标时长（秒），不改变音调
TARGET_REF_SAMPLES = int(TARGET_REF_SECS * TSE_SAMPLE_RATE)  # 96000

WSOLA_WINDOW_MS = 30.0            # WSOLA 分析窗长度（毫秒）
WSOLA_SYNTH_HOP_MS = 7.5           # WSOLA 合成步进（毫秒），越小越平滑

CFG_REF_WAV_PATH = "tse_reference_wav_path"   # 参考音频 WAV 路径 config 键


def _samples_to_wav_bytes(audio: List[float], sr: int = TSE_SAMPLE_RATE) -> bytes:
    """float 样本列表 → 单声道 16bit WAV bytes。"""
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        clamped = [max(-1.0, min(1.0, s)) for s in audio]
        ints = [int(s * 32767) for s in clamped]
        wf.writeframes(struct.pack(f'{len(ints)}h', *ints))
    return buf.getvalue()


def _wsola_time_stretch(audio: List[float], stretch_factor: float,
                        sr: int = TSE_SAMPLE_RATE) -> List[float]:
    """WSOLA 波形相似重叠相加时间压缩——保持音调不变。

    以合成步进间隔在输出中放置窗口，输入中以分析步进跳跃，在跳跃点附近
    搜索最相似波形以最小化重叠伪影。
    """
    if abs(stretch_factor - 1.0) < 1e-6 or stretch_factor <= 0:
        return audio[:]

    win_len = int(WSOLA_WINDOW_MS * sr / 1000.0)
    synth_hop = int(WSOLA_SYNTH_HOP_MS * sr / 1000.0)
    analysis_hop = int(round(synth_hop / stretch_factor))
    overlap = win_len - synth_hop

    window = [0.5 * (1.0 - math.cos(2.0 * math.pi * i / (win_len - 1)))
              for i in range(win_len)]

    out_len = int(len(audio) * stretch_factor)
    if out_len <= 0:
        return []
    output = [0.0] * out_len
    weight = [0.0] * out_len

    in_pos = 0
    out_pos = 0
    prev_in_pos = 0

    while in_pos + win_len <= len(audio) and out_pos + win_len <= out_len:
        # 波形相似搜索（除第一帧外）
        if out_pos > 0 and overlap > 0:
            prev_tail_start = prev_in_pos + win_len - overlap
            if prev_tail_start >= 0:
                search_rad = win_len // 4
                lo = max(0, in_pos - search_rad)
                hi = min(len(audio) - win_len, in_pos + search_rad)
                best_pos = in_pos
                best_corr = -1e10
                for p in range(lo, hi + 1, 4):
                    corr = sum(audio[prev_tail_start + j] * audio[p + j]
                               for j in range(overlap)
                               if prev_tail_start + j < len(audio) and p + j < len(audio))
                    if corr > best_corr:
                        best_corr = corr
                        best_pos = p
                for p in range(max(lo, best_pos - 4), min(hi, best_pos + 4) + 1):
                    corr = sum(audio[prev_tail_start + j] * audio[p + j]
                               for j in range(overlap)
                               if prev_tail_start + j < len(audio) and p + j < len(audio))
                    if corr > best_corr:
                        best_corr = corr
                        best_pos = p
                in_pos = best_pos

        # 重叠加窗
        for i in range(win_len):
            output[out_pos + i] += audio[in_pos + i] * window[i]
            weight[out_pos + i] += window[i]

        prev_in_pos = in_pos
        in_pos += analysis_hop
        out_pos += synth_hop

    # 归一化（去除加窗效应）
    for i in range(out_len):
        if weight[i] > 1e-10:
            output[i] /= weight[i]
    return output


def _process_reference_audio(raw: List[float]) -> List[float]:
    """10 秒录音 → WSOLA 时间压缩到 2 秒（保持音调，0 依赖）"""
    if len(raw) == 0:
        return raw
    target_len = TARGET_REF_SAMPLES
    stretch_factor = target_len / len(raw)  # e.g. 96000 / 480000 = 0.2
    if abs(stretch_factor - 1.0) < 0.01:
        return raw
    stretched = _wsola_time_stretch(raw, stretch_factor)
    if len(stretched) > target_len:
        stretched = stretched[:target_len]
    elif len(stretched) < target_len:
        stretched.extend([0.0] * (target_len - len(stretched)))
    return stretched


class _Recorder:
    """参考音频录音器：处理流钩子喂入，10 秒后取回。"""

    __slots__ = ('_buf', '_lock', '_active', '_start_time')

    def __init__(self):
        self._buf: List[float] = []
        self._lock = threading.Lock()
        self._active = False
        self._start_time = 0.0

    @property
    def start_time(self) -> float:
        return self._start_time

    def feed(self, samples: List[float]):
        if not self._active:
            return
        with self._lock:
            self._buf.extend(samples)
            cap = int(RECORD_DURATION * HOOK_SAMPLE_RATE) + HOOK_HOP_LENGTH * 2
            if len(self._buf) > cap:
                self._buf = self._buf[-cap:]

    def start(self):
        with self._lock:
            self._buf.clear()
            self._active = True
            self._start_time = time.time()

    def wait_and_get(self) -> Optional[List[float]]:
        if not self._active:
            return None
        deadline = self._start_time + RECORD_DURATION + 0.5
        while time.time() < deadline:
            if time.time() - self._start_time >= RECORD_DURATION:
                break
            time.sleep(0.05)
        with self._lock:
            self._active = False
            need = int(RECORD_DURATION * HOOK_SAMPLE_RATE)
            if len(self._buf) >= need:
                audio = self._buf[-need:]
                self._buf.clear()
                return audio
            if len(self._buf) > 0:
                audio = self._buf[:]
                self._buf.clear()
                return audio + [0.0] * (need - len(audio))
            return None


_recorder = _Recorder()


def get_tse_recorder() -> _Recorder:
    """返回 TSE 参考录音器单例。"""
    return _recorder


def register_tse_audio_hook(thread, log: Callable):
    """把线程的处理后音频钩子接到参考录音器（TSE 录音用）。"""
    if thread is None:
        return
    try:
        thread.set_tse_audio_hook(lambda s: _recorder.feed(list(s)))
    except AttributeError:
        log("[TSE] 钩子注册失败")


def load_tse_reference(processor, wav_path: str) -> bool:
    """加载 TSE 参考音频到处理器，成功返回 True。

    优先加载已缓存的 .bin；否则 WAV → WSOLA 时间压缩 → 缓存 .bin → 送入 引擎。
    必须在启动音频线程前调用（TSE 模型首帧就需要参考，空参考会崩）。
    """
    if not wav_path or not os.path.exists(wav_path):
        _module_log(f"[TSE] 参考 WAV 未找到: {wav_path!r}")
        return False

    bin_path = os.path.splitext(wav_path)[0] + '.bin'
    audio = None

    # 1) 尝试加载缓存（仅当 .bin 比 WAV 新时有效）
    if os.path.exists(bin_path) and os.path.getmtime(bin_path) >= os.path.getmtime(wav_path):
        try:
            with open(bin_path, 'rb') as f:
                raw = f.read()
            n = len(raw) // 4
            import struct
            audio = list(struct.unpack(f'{n}f', raw))
            _module_log(f"[TSE] 加载缓存 .bin ({len(audio)} samples, {len(audio)/TSE_SAMPLE_RATE:.1f}s)")
        except Exception as e:
            _module_log(f"[TSE] .bin 加载失败: {e}，重新处理 WAV")

    # 2) 回退到 WAV → WSOLA
    if audio is None:
        try:
            from wav_io import read_wav
            audio, sr = read_wav(wav_path)
            if sr != TSE_SAMPLE_RATE:
                r = Resampler()
                audio = r.process(audio, float(TSE_SAMPLE_RATE) / sr, True)
            # WSOLA 时间压缩：10s → 2s，保持音调
            audio = _process_reference_audio(audio)
            # 保存加速后的 WAV 用于调试
            try:
                from wav_io import write_wav
                write_wav(os.path.splitext(wav_path)[0] + '_wsola.wav', audio, TSE_SAMPLE_RATE)
            except Exception:
                pass
            # 保存 .bin 缓存
            import struct
            with open(bin_path, 'wb') as f:
                f.write(struct.pack(f'{len(audio)}f', *audio))
            _module_log(f"[TSE] 处理完成 → 缓存 {bin_path} ({len(audio)} samples, {len(audio)/TSE_SAMPLE_RATE:.1f}s)")
        except Exception as e:
            _module_log(f"[TSE] 参考 WAV 加载失败: {e}")
            return False

    try:
        if hasattr(processor, 'set_tse_reference'):
            processor.set_tse_reference(audio)
        return True
    except Exception as e:
        _module_log(f"[TSE] 设置参考失败: {e}")
        return False


def create_audio_processor(pre_gain_db: float = 0.0):
    """创建音频处理器实例（pvengine 纯 Python 组件化引擎）。

    模型路径由插件内部按 model_config 常量解析，无需外部传入。
    """
    if not ENGINE_AVAILABLE:
        raise RuntimeError("pvengine not available")
    return AudioProcessor(pre_gain_db)


def start_audio_stream(input_id: Optional[int], output_id: int,
                       processor: object, hop_length: Optional[int] = None,
                       network_source = None,
                       api_type: int = 13,
                       ready_msg: str = "",
                       extra_output_ids=None,
                       pw_ports: Tuple[List[str], List[str]] = ([], [])) -> AudioThread:
    """启动音频流并返回线程实例。

    参数:
        input_id: 输入设备 ID（网络输入模式下为 None）。
        output_id: 输出设备 ID。
        processor: pvengine.AudioProcessor 实例（处理器，直接使用）。
        hop_length: 处理 hop 长度（默认 1024）。
        network_source: 网络输入模式下的 RemoteAudioSource（可选）。
        ready_msg: 音频流就绪后由 AudioThread 记录的日志消息。
        extra_output_ids: 额外输出设备 ID 列表（仅 Windows PortAudio 路径，
            多输出扇出同一路降噪音频）。
        pw_ports: Linux PipeWire 模式的 (输入节点列表, 输出节点列表)；
            多输入自动混音、多输出扇出同一路降噪音频。
    """
    if hop_length is None:
        hop_length = HOP_LENGTH
    thread = AudioThread(input_id, output_id, processor.process, hop_length,
                         processor, network_source=network_source,
                         api_type=api_type, ready_msg=ready_msg,
                         extra_output_ids=extra_output_ids)
    if any(pw_ports[0]) or any(pw_ports[1]):
        thread.set_pw_ports(pw_ports[0], pw_ports[1])
    thread.start()
    return thread


# ═══════════════════════════════════════════════════════════════
#  音频设备枚举 & Core Audio 工具（原 audio_device.py；平台感知）
# ═══════════════════════════════════════════════════════════════

# 平台感知的设备 API（WASAPI=13 / PulseAudio=15 / ALSA=8 …）。
# 同一数值在别的平台会被 device_api 自动回退到该平台默认 host API。
from pvplatform.audio import device_api as _device_api

API_TYPE_WASAPI = _device_api.API_WASAPI
API_TYPE_MME = _device_api.API_MME
API_TYPE_NETWORK = _device_api.API_NETWORK
API_TYPE_PULSE = _device_api.API_PULSE
API_TYPE_ALSA = _device_api.API_ALSA
API_TYPE_PIPEWIRE = _device_api.API_PIPEWIRE


def get_api_name_by_type(api_type: int) -> str:
    """API 类型 → 显示名。"""
    return _device_api.get_api_name(api_type)


def device_config_suffix(api_type: int) -> str:
    """API 类型 → 设备配置键后缀（如 wasapi / mme / pulse）。"""
    return _device_api.api_config_suffix(api_type)


def get_platform_api_options() -> list:
    """当前平台可选的 API 下拉选项 [(label, type), ...]。"""
    return _device_api.get_api_options()


def default_api_type() -> int:
    """当前平台默认的 PortAudio host API 类型。"""
    return _device_api.platform_default_api_type()


def _get_host_api_indices(p: Any, api_type: int) -> List[int]:
    """获取指定 API 类型的 host API 索引列表（平台感知，按名字匹配）。"""
    return _device_api.get_host_api_indices(p, api_type)


def get_device_names(api_type: int = None) -> Tuple[List[str], List[str]]:
    """获取设备名称列表（已去重）。api_type 为 None 时用平台默认。

    Linux：原生 PipeWire 节点枚举（api_type==API_PIPEWIRE）。
    Windows/macOS：只枚举所选 host API（`_get_host_api_indices` 分级匹配，
    如 WASAPI / MME）下的设备，避免混入其它 host API 的重复/无关端点，
    设备按方向区分。
    """
    if api_type is None:
        api_type = default_api_type()
    if IS_LINUX:
        return _pw_sources(), _pw_dests()
    p = pyaudio.PyAudio()
    try:
        host_api_indices = _get_host_api_indices(p, api_type)
        input_names: List[str] = []
        output_names: List[str] = []
        for i in range(p.get_device_count()):
            try:
                dev = p.get_device_info_by_index(i)
            except Exception:
                continue
            if dev['hostApi'] not in host_api_indices:
                continue
            name = _device_api.fix_device_name(dev['name']).strip()
            if dev['maxInputChannels'] > 0 and name not in input_names:
                input_names.append(name)
            if dev['maxOutputChannels'] > 0 and name not in output_names:
                output_names.append(name)
        return input_names, output_names
    finally:
        p.terminate()


def get_device_id(device_name: str, is_input: bool, api_type: int = None) -> Optional[int]:
    """按设备名获取设备索引（支持前缀模糊匹配）。

    对 PortAudio 设备（Windows/macOS），会验证设备方向（输入/输出）匹配，
    避免返回同名输出端点。
    Linux 走原生 PipeWire（node.name 直接使用，不需要 PortAudio 索引），
    返回 None。
    """
    if api_type is None:
        api_type = default_api_type()
    # Linux：输入/输出都是 PipeWire 节点名，直接使用，无 PortAudio 索引
    if IS_LINUX:
        return None
    try:
        input_names, output_names = get_device_names(api_type=api_type)
    except Exception:
        input_names, output_names = [], []
    target_names = input_names if is_input else output_names

    matched_names = [name for name in target_names if name.startswith(device_name)]
    if not matched_names:
        # 兼容：配置里存的设备名被过滤/已不存在时，回退到第一个可用设备
        if target_names:
            matched_names = [target_names[0]]
        else:
            raise ValueError(f"Device not found")

    p = pyaudio.PyAudio()
    try:
        host_api_indices = _get_host_api_indices(p, api_type)
        for i in range(p.get_device_count()):
            try:
                dev = p.get_device_info_by_index(i)
            except Exception:
                continue
            if dev['hostApi'] not in host_api_indices:
                continue
            if _device_api.fix_device_name(dev['name']).strip() == matched_names[0]:
                if is_input and dev.get('maxInputChannels', 0) <= 0:
                    continue
                if not is_input and dev.get('maxOutputChannels', 0) <= 0:
                    continue
                return i

        raise ValueError(f"Device '{matched_names[0]}' ID not found")
    finally:
        p.terminate()