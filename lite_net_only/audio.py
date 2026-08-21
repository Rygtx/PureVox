# PureVox Lite Net Only — 音频输出流（仅 WASAPI）
# Copyright (C) 2024-2026 a2heng <752848283@qq.com>
# SPDX-License-Identifier: GPL-3.0-or-later
#
# 零复用：不 import audio_processor / pvplatform
# 网络解码写入 JitterRing，本模块回调读环播放；输出设备只列 WASAPI

import numpy as np

try:
    import pyaudio
except ImportError:
    pyaudio = None

SAMPLE_RATE = 48000
HOP = 960          # 与网络帧一致 (20ms)
FORMAT = pyaudio.paFloat32 if pyaudio else None
CHANNELS = 1

def db_to_linear(db):
    return 10.0 ** (db / 20.0)

def list_output_devices():
    """仅 WASAPI 输出设备，返回 [(disp, idx), ...]"""
    if pyaudio is None:
        return []
    pa = pyaudio.PyAudio()
    outs = []
    for i in range(pa.get_device_count()):
        try:
            info = pa.get_device_info_by_index(i)
        except Exception:
            continue
        name = info.get("name", "")
        # 兼容 GBK：PortAudio 返回 UTF-8 被 PyAudio 按 GBK 误读的乱码需编回再解
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
        try:
            api_idx = info.get("hostApi", 0)
            api_info = pa.get_host_api_info_by_index(api_idx)
            api_name = (api_info.get("name", "") or "").lower()
        except Exception:
            api_name = ""
        if "wasapi" not in api_name:
            continue  # 输出只要 WASAPI
        if info.get("maxOutputChannels", 0) <= 0:
            continue
        ch = int(info.get("maxOutputChannels", 0))
        sr = int(info.get("defaultSampleRate", 0) or 0)
        disp = f"{name} ({ch}ch {sr}Hz)"
        outs.append((disp, i))
    pa.terminate()
    def dedup(lst):
        seen = {}
        out = []
        for disp, idx in lst:
            cnt = seen.get(disp, 0)
            seen[disp] = cnt + 1
            out.append((disp, idx) if cnt == 0 else (f"{disp} #{cnt+1}", idx))
        return out
    return dedup(outs)

def check_output_48k(idx):
    """WASAPI 共享模式锁 MixFormat，非 48k 设备直接拒绝并提示"""
    if pyaudio is None:
        return False, "PyAudio 未安装"
    pa = pyaudio.PyAudio()
    try:
        pa.is_format_supported(
            rate=SAMPLE_RATE,
            input_device=None,
            input_channels=None,
            input_format=None,
            output_device=idx,
            output_channels=CHANNELS,
            output_format=FORMAT,
        )
        return True, ""
    except Exception as e:
        return False, str(e)
    finally:
        pa.terminate()

class LiteNetStream:
    """输出流：回调从 JitterRing 取样本，后增益后播放"""
    def __init__(self, out_idx, ring, post_db=0.0):
        self.out_idx = out_idx
        self.ring = ring
        self.post_gain = db_to_linear(post_db)
        self._pa = None
        self._stream = None
        self._running = False

    def set_post_gain(self, post_db):
        self.post_gain = db_to_linear(post_db)

    def _callback(self, _in_data, frame_count, _time_info, _status):
        try:
            data = self.ring.read(frame_count) * self.post_gain
            np.clip(data, -1.0, 1.0, out=data)
            return (data.tobytes(), pyaudio.paContinue)
        except Exception:
            return (np.zeros(frame_count, dtype=np.float32).tobytes(), pyaudio.paContinue)

    def start(self):
        if pyaudio is None:
            raise RuntimeError("PyAudio 未安装")
        if self._running:
            return
        ok, msg = check_output_48k(self.out_idx)
        if not ok:
            raise RuntimeError(f"输出设备不支持 48kHz: {msg}")
        self._pa = pyaudio.PyAudio()
        self._stream = self._pa.open(
            rate=SAMPLE_RATE,
            channels=CHANNELS,
            format=FORMAT,
            input=False,
            output=True,
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
