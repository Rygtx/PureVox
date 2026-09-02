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
else:
    from pvplatform.audio.pa_backend import PaBridge as _PaBridge


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
    from pvengine import AudioProcessor, PlaybackSink, RingBuffer, Resampler
    ENGINE_AVAILABLE = True
except ImportError:
    ENGINE_AVAILABLE = False
    _module_log("[音频] 引擎模块 pvengine 不可用（缺 numpy/onnxruntime？）")

SAMPLE_RATE = 48000
HOP_LENGTH = 480                # 10ms @48kHz（202609 模型契约：波形 hop 进出）


TSE_SAMPLE_RATE = 48000        # TSE 模型采样率 (48kHz)
TSE_HOP_LENGTH = 480           # 480 samples @ 48kHz = 10ms


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
        # 传输后端插件（Linux=PwBridge / Windows=PaBridge）+ 每输出一个
        # PlaybackSink（跨时钟域播放正确性唯一实现点，见 pvengine.dsp.playback）。
        # 回调/流只做搬运；处理在本线程 _bridge_loop，速率差由 sink 消化。
        self._use_pw = False
        self._bridge = None                      # 后端实例（open 于 _create_stream）
        self._pw_ports: Tuple[List[str], List[str]] = ([], [])  # (输入列表, 输出列表)
        self._sinks: List[PlaybackSink] = []     # 与输出路一一对应
        # 网络读侧状态（_network_reader）
        self._net_acc: List[float] = []
        self._net_last_data: float = 0.0
        # 循环诊断（60s 汇总一行，见 _bridge_loop）
        self._diag = {"fc": 0, "t_sum": 0.0, "t_max": 0.0, "t0": time.time(),
                      "s_pads": 0, "s_drops": 0, "s_urs": 0}
        self._process_frame: Callable[[List[float]], List[float]] = process_frame
        self._hop_length: int = hop_length
        # 多输出扇出（原「监听」机制的泛化）：主输出之外的全部播放设备
        self._extra_out_ids: List[int] = [i for i in (extra_output_ids or [])
                                          if i is not None]
        self._stop_event: threading.Event = threading.Event()
        # 流就绪事件：_create_stream 成功后 set()，失败时记录 _start_error
        self._ready_event: threading.Event = threading.Event()
        self._start_error: Optional[str] = None
        self._p: Optional[Any] = None
        self._vu_peak: float = 0.0  # L4:VU峰值快照（最新帧dBFS）
        self._spectrum_in: RingBuffer = RingBuffer(SAMPLE_RATE * 2)   # L4:频谱输入 2s
        self._spectrum_out: RingBuffer = RingBuffer(SAMPLE_RATE * 2)  # L4:频谱输出 2s
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
                        pw_bridge=self._bridge,
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
        """打开传输后端并装配播放 sink（一个输出路一个 PlaybackSink）。

        后端为哑传输（搬运 + 设备时钟回调），见 pvplatform.audio；
        跨时钟域播放正确性在 PlaybackSink（pvengine.dsp.playback）。
        """
        hop = self._hop_length

        # Linux：PwBridge（libpulse 原生流），F32 单声道 48000Hz
        if IS_LINUX and self._use_pw:
            in_names, out_names = self._pw_ports
            self._sinks = [PlaybackSink(hop=hop) for _ in out_names]
            bridge = _PwBridge()
            if not bridge.open(in_names, out_names,
                               [s.pull for s in self._sinks]):
                err = bridge.last_error()
                bridge.close()
                raise OSError(f"PipeWire 连接失败: {err}")
            self._bridge = bridge
            _module_log(f"[PipeWire] 输入x{len(in_names)}: {','.join(in_names) or '(未选)'}  "
                        f"输出x{len(out_names)}: {','.join(out_names) or '(未选)'}")
            _module_log("[PipeWire] F32 单声道 48000Hz 协商（PipeWire 负责重采样/声道转换）")
            return

        if IS_LINUX:
            # Linux 强制原生 PipeWire：不落 PortAudio/PyAudio 备选
            raise OSError("Linux 音频仅支持原生 PipeWire（未配置输入/输出节点或 pvpipe 不可用）")

        # Windows：PaBridge（PortAudio 输入/输出独立流，每输出一个 pull）
        self._p = pyaudio.PyAudio()
        if self._p is None:
            raise OSError("PyAudio 初始化失败")

        in_id = self._input_id
        out_id = self._validate_and_fix_device(self._output_id, want_input=False)
        out_ids = [out_id]      # None = 系统默认输出（PaBridge 透传）
        for dev in self._extra_out_ids:
            fixed = self._validate_and_fix_device(dev, want_input=False)
            if fixed is not None and fixed not in out_ids:
                out_ids.append(fixed)

        if in_id is not None:
            in_id = self._validate_and_fix_device(in_id, want_input=True)

        self._sinks = [PlaybackSink(hop=hop) for _ in out_ids]
        bridge = _PaBridge()
        if not bridge.open(in_id, out_ids, [s.pull for s in self._sinks],
                           p=self._p):
            err = bridge.last_error()
            bridge.close()
            raise OSError(f"音频流打开失败: {err}")
        self._bridge = bridge
        if in_id is None:
            _module_log(f"[输出] x{len(out_ids)}（网络输入模式）")

    # ── 统一处理循环 ────────────────────────────────────────────────
    #
    # 时钟模型（2026-09 播放重构沉淀，全部路径唯一）：
    #   设备时钟是唯一主时钟——播放侧由设备回调经 PlaybackSink.pull 拉帧，
    #   速率差/调度抖动由 sink 变速消化（pvengine.dsp.playback：PI 伺服
    #   ASRC ±3%、预热、欠载静音重同步、封顶丢最旧）。处理线程按 hop 推进
    #   （read → process → sinks.write），不做任何缓冲策略。
    #   历史病灶（已根除）：全双工单流内联处理 + 主输出帧长硬对齐 +
    #   额外输出手写 ASRC + 网络输出 drop/pad —— 5 路径 4 种时钟策略，
    #   修一处漏三处；现收敛为后端插件（哑）+ 一个 PlaybackSink。


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

        # 本地/网络同一处理循环（平台差异只在后端插件；纯媒体会话
        # 无设备输入不经本线程——EngineController 走 MediaSession）
        is_network = self._input_id is None and self._network_source is not None
        self._bridge_loop(is_network)

        _module_log("[DEV] 音频线程已退出")

    # ── 统一处理循环（本地/网络同一实现）────────────────────────────

    def _network_reader(self) -> Optional[List[float]]:
        """网络源读侧：flush / 突发硬顶 / 断流垫零 → 返回一个 hop 或 None。

        速率差稳态由 PlaybackSink 伺服消化，这里只兜突发（硬顶截断）
        与断流（150ms 无数据：残帧淡出补零成 hop，交给链推进状态）。
        """
        MAX_ACC = HOP_LENGTH * 8            # 硬上限 ~80ms
        TARGET_ACC = HOP_LENGTH * 5         # 目标缓冲 ~50ms
        STALL_TIMEOUT = 0.15                # 断流判定
        src = self._network_source
        acc = self._net_acc
        if src and src.flush_event.is_set():
            src.flush_event.clear()
            acc.clear()
            self._flush_sinks()
            _module_log("[网络] 缓冲已清空 (flush)")
            self._net_last_data = time.time()
        if src:
            navail = src.available()
            if navail > 0:
                chunk = src.read(navail)
                if chunk:
                    acc.extend(chunk)
                    self._net_last_data = time.time()
        if len(acc) > MAX_ACC:
            acc[:] = acc[-TARGET_ACC:]      # 突发兜底（稳态漂移由 sink 消化）
        stall = time.time() - self._net_last_data
        if stall > STALL_TIMEOUT and 0 < len(acc) < HOP_LENGTH:
            fade_len = min(64, len(acc))
            for i in range(fade_len):
                acc[-fade_len + i] *= 1.0 - (i + 1) / (fade_len + 1)
            acc.extend([0.0] * (HOP_LENGTH - len(acc)))
        if len(acc) < HOP_LENGTH:
            return None
        hop = acc[:HOP_LENGTH]
        del acc[:HOP_LENGTH]
        return hop

    def _flush_sinks(self) -> None:
        """清空全部播放 sink（回预热态，续播前重同步）。"""
        for sink in self._sinks:
            sink.reset()

    def _deliver(self, out: List[float]) -> None:
        """一帧产出 → 全部播放 sink。

        线性多出：链内 output 抽头各取所在位置的信号
        （take_output_frames 与 sink 一一对应）；无抽头/抽头空回退
        最近非空帧，再兜底主输出帧——sink 恒有喂，不进饥饿态。
        """
        out_frames = self.processor.take_output_frames()
        if self._sinks and len(out_frames) == len(self._sinks):
            last = None
            for sink, f in zip(self._sinks, out_frames):
                frame = f if f else last
                if not frame:
                    frame = out
                last = frame
                sink.write(frame)
        else:
            for sink in self._sinks:
                sink.write(out)

    def _bridge_loop(self, network: bool) -> None:
        """统一处理循环：read(hop) → process → sinks.write。

        本地模式 read 自传输后端混合输入环；网络模式 read 自
        _network_reader。处理/可视化/录音钩子/AEC far 两条路径共用，
        平台差异只在后端插件。每 ~2s 检查后端健康，流死即退出
        （上层走会话重启路径）。
        """
        d = self._diag
        last_viz = 0.0
        t_last_health = time.time()
        while not self._stop_event.is_set():
            if network:
                data = self._network_reader()
            else:
                data = self._bridge.read(HOP_LENGTH) \
                    if self._bridge is not None else None
            if not data:
                time.sleep(0.002)
                continue

            t0 = time.perf_counter()
            chunk = data if len(data) == HOP_LENGTH else data[-HOP_LENGTH:]

            if not network and self._aec_enabled and self._speaker_capture:
                far_need = int(HOP_LENGTH * self._speaker_capture.dev_sr
                               / SAMPLE_RATE)
                far_data = self._speaker_capture.read(far_need)
                if far_data is not None:
                    far_data = [x * self._aec_far_gain for x in far_data]
                if self._aec_warmup_frames > 0:
                    self._aec_warmup_frames -= 1
                    out = self.processor.process_with_far(
                        chunk, [0.0] * far_need)
                else:
                    out = self.processor.process_with_far(
                        chunk, far_data if far_data is not None
                        else [0.0] * far_need)
            elif network:
                out = self.processor.process_pipeline(chunk)
            else:
                out = self._process_frame(chunk)
            dt = time.perf_counter() - t0
            d["fc"] += 1
            d["t_sum"] += dt
            d["t_max"] = max(d["t_max"], dt)

            if out:
                if not network:
                    # 录音捕获：降噪后的音频（TSE 前）
                    if self._recording_hook is not None:
                        try:
                            self._recording_hook(list(out))
                        except Exception:
                            pass
                    # TSE 录音钩子兜底（挂了 recording_hook 时不重复喂）
                    if self._tse_hook is not None \
                            and self._recording_hook is None:
                        try:
                            pre_tse = self.processor.get_tse_recording_audio()
                            if pre_tse:
                                self._tse_hook(list(pre_tse))
                        except Exception:
                            pass
                    if self._viz_enabled:
                        try:
                            self._spectrum_in.write(
                                self.processor.process_eq_only(list(chunk)))
                        except Exception:
                            self._spectrum_in.write(list(chunk))
                        self._spectrum_out.write(list(out))
                    self._vu_peak = max(abs(x) for x in out)
                else:
                    # 网络可视化：管线抽头（process_pipeline 内启用），50ms 节流
                    now_v = time.time()
                    if self._viz_enabled and now_v - last_viz > 0.05:
                        viz_in = self.processor.get_and_clear_viz_input()
                        if viz_in:
                            self._spectrum_in.write(viz_in)
                        viz_out = self.processor.get_and_clear_viz_output()
                        if viz_out:
                            self._vu_peak = max(abs(x) for x in viz_out)
                            self._spectrum_out.write(viz_out)
                        last_viz = now_v

                self._deliver(out)

            now = time.time()
            if now - d["t0"] >= 60.0:
                self._loop_diag()
            if now - t_last_health > 2.0:
                t_last_health = now
                if self._bridge is not None and not self._bridge.active():
                    _module_log("[音频] 传输流已停止（设备断开/拔出）")
                    break

    def _loop_diag(self) -> None:
        """60s 一行健康汇总：处理耗时 / sink 水位 / 欠载·垫零·丢最旧。

        正常值：fps≈100、平均处理 <10ms、欠载/垫/丢 = 0（稳态速率差
        由 sink 伺服消化，不产生任何补零或丢弃）。
        """
        d = self._diag
        now = time.time()
        win = max(1e-6, now - d["t0"])
        fps = d["fc"] / win
        avg_ms = d["t_sum"] / max(1, d["fc"]) * 1000.0
        max_ms = d["t_max"] * 1000.0
        tot_pads = tot_drops = tot_urs = 0
        min_level = -1
        for s in self._sinks:
            g = s.diag()
            tot_pads += g["pads"]
            tot_drops += g["drops"]
            tot_urs += g["underruns"]
            if min_level < 0 or g["level"] < min_level:
                min_level = g["level"]
        _module_log(
            "[诊断] 处理循环健康（60s）: fps=%.1f/100 平均=%.2fms 最大=%.2fms "
            "sink最小水位=%d 欠载=%d 垫零=%d 丢最旧=%d (正常: 欠载/垫/丢=0)"
            % (fps, avg_ms, max_ms, min_level,
               tot_urs - d["s_urs"], tot_pads - d["s_pads"],
               tot_drops - d["s_drops"]))
        d.update(fc=0, t_sum=0.0, t_max=0.0, t0=now,
                 s_pads=tot_pads, s_drops=tot_drops, s_urs=tot_urs)

    def stop(self) -> None:
        """优雅地停止音频线程。"""
        self._stop_event.set()
        if self.is_alive():
            self.join(timeout=2.0)
        self._cleanup()

    def _cleanup(self) -> None:
        """释放音频资源（后端流 + PyAudio 实例）。"""
        if self._bridge is not None:
            try:
                self._bridge.close()
            except Exception as e:
                _module_log(f"[音频] 后端关闭异常: {e}")
            self._bridge = None
        self._sinks = []
        if self._p is not None:
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

    09c 契约: 注册取自然 10s（不足由引擎 fix_ref 平铺），不再做 WSOLA 压缩
    （10s→2s 是 tse15/2s 时代的遗留，对 10s 全帧 key 契约是错的）。
    enr_tok 结果缓存为 <wav>_enrtok.npz，键 = 录音 mtime/size + ref_encoder
    mtime/size —— 录音未变且模型版本未变时启动直接载入缓存（跳过
    STFT+ref_encoder 处理）；录音变了或换模型版本自动失效重算。
    """
    if not wav_path or not os.path.exists(wav_path):
        _module_log(f"[TSE] 参考 WAV 未找到: {wav_path!r}")
        return False

    try:
        from wav_io import read_wav
        audio, sr = read_wav(wav_path)
        if sr != TSE_SAMPLE_RATE:
            r = Resampler()
            audio = r.process(audio, float(TSE_SAMPLE_RATE) / sr, True)
    except Exception as e:
        _module_log(f"[TSE] 参考 WAV 加载失败: {e}")
        return False

    try:
        if hasattr(processor, 'set_tse_reference'):
            processor.set_tse_reference(audio, ref_key=wav_path)
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
        hop_length: 处理 hop 长度（默认 480，10ms @48kHz）。
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