# PureVox Lite Denoise Only — 音频流
# Copyright (C) 2024-2026 a2heng <752848283@qq.com>
# SPDX-License-Identifier: GPL-3.0-or-later
#
# 零复用：不 import audio_processor / pvplatform
# 48kHz 强制检测 + 前后增益 + 纯 Python 引擎

import math
import threading
import numpy as np

try:
    import pyaudio
except ImportError:
    pyaudio = None

SAMPLE_RATE = 48000
HOP = 1024
FORMAT = pyaudio.paFloat32 if pyaudio else None
CHANNELS = 1

def db_to_linear(db):
    return 10.0 ** (db / 20.0)

def _api_short(name):
    n = (name or "").lower()
    if "wasapi" in n:
        return "WASAPI"
    if "mme" in n or "wave" in n:
        return "MME"
    return None  # 其它 API 不展示

def _device_api(idx):
    if pyaudio is None:
        return "UNK"
    pa = pyaudio.PyAudio()
    try:
        info = pa.get_device_info_by_index(idx)
        api_idx = info.get("hostApi", 0)
        api_info = pa.get_host_api_info_by_index(api_idx)
        v = _api_short(api_info.get("name", ""))
        return v or "UNK"
    except Exception:
        return "UNK"
    finally:
        try:
            pa.terminate()
        except Exception:
            pass

def check_api_match(in_idx, out_idx):
    if in_idx < 0 or out_idx < 0:
        return True, ""
    a1 = _device_api(in_idx)
    a2 = _device_api(out_idx)
    if a1 != a2:
        return False, f"组合非法：输入为 {a1}，输出为 {a2}，需同为 {a1} 或同为 {a2}（WASAPI/MME 不能混用）"
    return True, ""

def list_devices():
    if pyaudio is None:
        return [], []
    pa = pyaudio.PyAudio()
    ins, outs = [], []
    for i in range(pa.get_device_count()):
        try:
            info = pa.get_device_info_by_index(i)
        except Exception:
            continue
        name = info.get("name", "")
        # 兼容 GBK：复用主程序 pvplatform/audio/device_api.fix_device_name
        # PortAudio 返回 UTF-8，被 PyAudio 按 GBK 误读的乱码需按 GBK 编回再按 UTF-8 解
        def _fix_garbled(s):
            if not s:
                return s
            try:
                fixed = s.encode("gbk").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                return s
            if fixed == s or "\ufffd" in fixed:
                return s
            return fixed
        name = _fix_garbled(name)
        name = (name or "").strip()
        if not name:
            continue
        # 过滤空白名，避免输入空行导致显示与索引错位
        # 仅保留 WASAPI/MME
        try:
            api_idx = info.get("hostApi", 0)
            api_info = pa.get_host_api_info_by_index(api_idx)
            api_name = _api_short(api_info.get("name", ""))
        except Exception:
            api_name = None
        if api_name not in ("WASAPI", "MME"):
            continue
        # 第二行属性：通道/默认采样率/延迟
        ch = int(info.get("maxInputChannels", 0) or info.get("maxOutputChannels", 0) or 0)
        sr = int(info.get("defaultSampleRate", 0) or 0)
        try:
            lat = float(info.get("defaultLowInputLatency", info.get("defaultLowOutputLatency", 0)) * 1000)
            lat_s = f"{lat:.1f}ms"
        except Exception:
            lat_s = ""
        props = f"{api_name} · {ch}ch · {sr}Hz" + (f" · {lat_s}" if lat_s else "")
        disp = f"[{api_name}] {name}"
        if info.get("maxInputChannels", 0) > 0:
            ins.append((disp, i, props))
        if info.get("maxOutputChannels", 0) > 0:
            outs.append((disp, i, props))
    pa.terminate()
    def dedup(lst):
        seen = {}
        out = []
        for disp, idx, props in lst:
            base = disp
            cnt = seen.get(base, 0)
            seen[base] = cnt + 1
            if cnt == 0:
                out.append((base, idx, props))
            else:
                out.append((f"{base} #{cnt+1}", idx, props))
        return out
    ins, outs = dedup(ins), dedup(outs)
    # WASAPI 在上，MME 在下
    ins.sort(key=lambda x: (0 if x[0].startswith("[WASAPI]") else 1, x[0]))
    outs.sort(key=lambda x: (0 if x[0].startswith("[WASAPI]") else 1, x[0]))
    return ins, outs

def try_open_48k(device_index, is_input):
    if pyaudio is None:
        return False, "PyAudio 未安装"
    pa = pyaudio.PyAudio()
    try:
        if is_input:
            ok = pa.is_format_supported(
                rate=SAMPLE_RATE,
                input_device=device_index,
                input_channels=CHANNELS,
                input_format=FORMAT,
                output_device=None,
                output_channels=None,
                output_format=None,
            )
        else:
            ok = pa.is_format_supported(
                rate=SAMPLE_RATE,
                input_device=None,
                input_channels=None,
                input_format=None,
                output_device=device_index,
                output_channels=CHANNELS,
                output_format=FORMAT,
            )
        pa.terminate()
        return True, ""
    except Exception as e:
        pa.terminate()
        return False, str(e)

class LiteAudioStream:
    def __init__(self, in_idx, out_idx, engine, pre_db=0.0, post_db=0.0):
        self.in_idx = in_idx
        self.out_idx = out_idx
        self.engine = engine
        self.pre_gain = db_to_linear(pre_db)
        self.post_gain = db_to_linear(post_db)
        self._lock = threading.Lock()
        self._pa = None
        self._stream = None
        self._running = False
        # ring for incomplete frames
        self._in_buf = np.zeros(0, dtype=np.float32)

    def set_gains(self, pre_db, post_db):
        with self._lock:
            self.pre_gain = db_to_linear(pre_db)
            self.post_gain = db_to_linear(post_db)

    def _callback(self, in_data, frame_count, time_info, status):
        # in_data: bytes float32
        try:
            chunk = np.frombuffer(in_data, dtype=np.float32).astype(np.float32)
            # mono: if stereo, take first channel? Pa gives interleaved if channels>1 but we request 1
            # apply pre gain
            with self._lock:
                pre = self.pre_gain
                post = self.post_gain
            chunk = chunk * pre
            # accumulate to HOP
            self._in_buf = np.concatenate([self._in_buf, chunk])
            out_all = np.zeros(0, dtype=np.float32)
            while self._in_buf.shape[0] >= HOP:
                hop_in = self._in_buf[:HOP]
                self._in_buf = self._in_buf[HOP:]
                hop_out = self.engine.process(hop_in)
                hop_out = hop_out * post
                # clip
                hop_out = np.clip(hop_out, -1.0, 1.0)
                out_all = np.concatenate([out_all, hop_out])
            # if not enough, output silence for requested frames
            # out_all may be less than frame_count, pad with zeros
            if out_all.shape[0] < frame_count:
                pad = np.zeros(frame_count - out_all.shape[0], dtype=np.float32)
                out_all = np.concatenate([out_all, pad])
            else:
                out_all = out_all[:frame_count]
            return (out_all.tobytes(), pyaudio.paContinue)
        except Exception:
            # fail safe: output silence
            return (np.zeros(frame_count, dtype=np.float32).tobytes(), pyaudio.paContinue)

    def start(self):
        if pyaudio is None:
            raise RuntimeError("PyAudio 未安装")
        if self._running:
            return
        # 48k check
        ok, msg = try_open_48k(self.in_idx, True)
        if not ok:
            raise RuntimeError(f"输入设备不支持 48kHz: {msg}")
        ok, msg = try_open_48k(self.out_idx, False)
        if not ok:
            raise RuntimeError(f"输出设备不支持 48kHz: {msg}")
        self._pa = pyaudio.PyAudio()
        self._stream = self._pa.open(
            rate=SAMPLE_RATE,
            channels=CHANNELS,
            format=FORMAT,
            input=True,
            output=True,
            input_device_index=self.in_idx,
            output_device_index=self.out_idx,
            frames_per_buffer=HOP,
            stream_callback=self._callback,
        )
        self._stream.start_stream()
        self._running = True

    def stop(self):
        self._running = False
        try:
            if self._stream:
                self._stream.stop_stream()
                self._stream.close()
        except Exception:
            pass
        try:
            if self._pa:
                self._pa.terminate()
        except Exception:
            pass
        self._stream = None
        self._pa = None
