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
Windows 平台扬声器 loopback 采集（AEC far-end 数据源）。

通过 Windows Core Audio WASAPI loopback 捕获系统播放音频。
不走 PyAudio/PortAudio（其对 loopback 支持有限），直接用 IAudioClient
以 AUDCLNT_STREAMFLAGS_LOOPBACK 模式打开默认渲染设备。

接口契约（与 Linux 后端一致）：
    start() -> bool / stop() / read(n) / flush()
    dev_sr (int) / active (bool) / on_device_changed 回调
"""

import ctypes
import threading
import time
from ctypes import wintypes, POINTER, byref, cast, c_void_p
from typing import List, Optional, Callable

from .common import RingBuffer, HOP_LENGTH, _module_log


class SpeakerCaptureWin:
    """半双工扬声器采集 — WASAPI loopback（Windows 专用）。"""

    # COM 接口 GUID
    _CLSID_MMDeviceEnumerator = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
    _IID_IMMDeviceEnumerator    = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
    _IID_IMMDevice              = "{D666063F-1587-4E43-81F1-B948E807363F}"
    _IID_IAudioClient           = "{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}"
    _IID_IAudioCaptureClient    = "{C8ADBD64-E71E-48A0-A4DE-185C395CD317}"

    AUDCLNT_SHAREMODE_SHARED = 0
    AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
    CLSCTX_ALL = 0x17
    COINIT_MULTITHREADED = 0x0

    # 设备检查间隔（秒）
    DEVICE_CHECK_INTERVAL = 2.0

    AEC_FAR_SR = 48000  # AEC model requires far-end (speaker loopback) at 48kHz

    def __init__(self, on_device_changed: Optional[Callable[[int], None]] = None):
        self._buffer = RingBuffer(HOP_LENGTH * 2)  # 2帧缓冲
        self._active = False
        self._lock = threading.Lock()
        self._capture_thread: Optional[threading.Thread] = None
        self._device_check_thread: Optional[threading.Thread] = None
        # COM objects (used in capture thread)
        self._audio_client: Optional[ctypes.c_void_p] = None
        self._capture_client: Optional[ctypes.c_void_p] = None
        self._dev_ch: int = 1
        self._dev_sr: int = 48000  # 强制 48kHz
        self._dev_name: str = "Unknown"
        self._current_device_name: Optional[str] = None
        # Callback when device switches: called with new dev_sr
        self._on_device_changed = on_device_changed

    @property
    def active(self) -> bool:
        return self._active

    @property
    def dev_sr(self) -> int:
        """设备实际采样率（来自 WASAPI MixFormat）。"""
        return self._dev_sr

    def start(self) -> bool:
        """打开默认渲染设备的 WASAPI loopback。Returns True on success."""
        try:
            ole32 = ctypes.windll.ole32

            def _make_guid(s):
                buf = (ctypes.c_ubyte * 16)()
                ole32.CLSIDFromString(s, buf)
                return buf

            CLSID_MMDE = _make_guid(self._CLSID_MMDeviceEnumerator)
            IID_IMMDE = _make_guid(self._IID_IMMDeviceEnumerator)
            IID_IMMD  = _make_guid(self._IID_IMMDevice)
            IID_IAC   = _make_guid(self._IID_IAudioClient)
            IID_IACC  = _make_guid(self._IID_IAudioCaptureClient)

            ole32.CoInitializeEx(None, self.COINIT_MULTITHREADED)

            # 1. 创建 IMMDeviceEnumerator
            pEnum = c_void_p()
            hr = ole32.CoCreateInstance(
                byref(CLSID_MMDE), None, self.CLSCTX_ALL,
                byref(IID_IMMDE), byref(pEnum))
            if hr < 0 or not pEnum:
                _module_log("[AEC] 无法创建 MMDeviceEnumerator")
                return False

            vtbl_enum = cast(pEnum, POINTER(POINTER(c_void_p))).contents

            # 2. GetDefaultAudioEndpoint(eRender, eConsole)
            fn_GetDefault = cast(vtbl_enum[4], ctypes.WINFUNCTYPE(
                ctypes.c_long, c_void_p, wintypes.DWORD, wintypes.DWORD, POINTER(c_void_p)))
            pDevice = c_void_p()
            hr = fn_GetDefault(pEnum, 0, 0, byref(pDevice))  # eRender=0, eConsole=0
            if hr < 0 or not pDevice:
                _module_log("[AEC] 无法获取默认渲染设备")
                return False

            # 3. 获取设备名
            # OpenPropertyStore → GetValue(PKEY_Device_FriendlyName)
            vtbl_dev = cast(pDevice, POINTER(POINTER(c_void_p))).contents
            fn_OpenPS = cast(vtbl_dev[4], ctypes.WINFUNCTYPE(
                ctypes.c_long, c_void_p, wintypes.DWORD, POINTER(c_void_p)))
            pStore = c_void_p()
            dev_name = "Unknown"
            if fn_OpenPS(pDevice, 0, byref(pStore)) == 0 and pStore:
                # PKEY_Device_FriendlyName: {a45c254e-df1c-4efd-8020-67d146a850e0}, 14
                pkey_fmtid = _make_guid("{a45c254e-df1c-4efd-8020-67d146a850e0}")
                class PK(ctypes.Structure):
                    _fields_ = [("fmtid", ctypes.c_ubyte*16), ("pid", wintypes.DWORD)]
                pk = PK()
                ctypes.memmove(pk.fmtid, pkey_fmtid, 16)
                pk.pid = 14
                vtbl_store = cast(pStore, POINTER(POINTER(c_void_p))).contents
                fn_GetValue = cast(vtbl_store[5], ctypes.WINFUNCTYPE(
                    ctypes.c_long, c_void_p, ctypes.c_void_p, ctypes.c_void_p))
                pv = (ctypes.c_ubyte * 24)()
                if fn_GetValue(pStore, byref(pk), pv) == 0:
                    vt = ctypes.c_ushort.from_buffer(pv, 0).value
                    if vt == 31:  # VT_LPWSTR
                        ptr = c_void_p.from_buffer(pv, 8).value
                        if ptr:
                            dev_name = ctypes.wstring_at(ptr)
                # Release store
                cast(vtbl_store[2], ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p))(pStore)

            # 4. Activate IAudioClient
            fn_Activate = cast(vtbl_dev[3], ctypes.WINFUNCTYPE(
                ctypes.c_long, c_void_p, ctypes.c_void_p, wintypes.DWORD,
                c_void_p, POINTER(c_void_p)))
            pAudioClient = c_void_p()
            hr = fn_Activate(pDevice, byref(IID_IAC), self.CLSCTX_ALL,
                             None, byref(pAudioClient))
            if hr < 0 or not pAudioClient:
                _module_log("[AEC] 无法激活 IAudioClient")
                return False

            # 5. GetMixFormat → 获取设备原生格式
            vtbl_ac = cast(pAudioClient, POINTER(POINTER(c_void_p))).contents

            class WAVEFORMATEX(ctypes.Structure):
                _fields_ = [
                    ("wFormatTag",      wintypes.WORD),
                    ("nChannels",       wintypes.WORD),
                    ("nSamplesPerSec",  wintypes.DWORD),
                    ("nAvgBytesPerSec", wintypes.DWORD),
                    ("nBlockAlign",     wintypes.WORD),
                    ("wBitsPerSample",  wintypes.WORD),
                    ("cbSize",          wintypes.WORD),
                ]
            fn_GetMixFormat = cast(vtbl_ac[8], ctypes.WINFUNCTYPE(
                ctypes.c_long, c_void_p, POINTER(ctypes.c_void_p)))
            pWfx = ctypes.c_void_p()
            hr = fn_GetMixFormat(pAudioClient, byref(pWfx))
            if hr < 0 or not pWfx:
                return False
            wfx = cast(pWfx, POINTER(WAVEFORMATEX)).contents
            self._dev_sr = int(wfx.nSamplesPerSec)
            self._dev_name = dev_name
            _module_log(f"[AEC] 扬声器采集: {dev_name} ({self._dev_sr}Hz, ch={wfx.nChannels})")
            self._buffer = RingBuffer(HOP_LENGTH * 2)

            # 6. Initialize with AUDCLNT_STREAMFLAGS_LOOPBACK
            REFERENCE_TIME = 100000  # 10ms buffer
            fn_Initialize = cast(vtbl_ac[3], ctypes.WINFUNCTYPE(
                ctypes.c_long, c_void_p, wintypes.DWORD, wintypes.DWORD,
                ctypes.c_longlong, ctypes.c_longlong, ctypes.c_void_p, ctypes.c_void_p))
            hr = fn_Initialize(
                pAudioClient,
                self.AUDCLNT_SHAREMODE_SHARED,
                self.AUDCLNT_STREAMFLAGS_LOOPBACK,
                REFERENCE_TIME, 0,
                pWfx, None)
            ole32.CoTaskMemFree(pWfx)
            if hr < 0:
                _module_log(f"[AEC] IAudioClient::Initialize failed: 0x{hr:08X}")
                return False

            # 7. GetService -> IAudioCaptureClient
            fn_GetService = cast(vtbl_ac[14], ctypes.WINFUNCTYPE(
                ctypes.c_long, c_void_p, ctypes.c_void_p, POINTER(c_void_p)))
            pCaptureClient = c_void_p()
            hr = fn_GetService(pAudioClient, byref(IID_IACC), byref(pCaptureClient))
            if hr < 0 or not pCaptureClient:
                _module_log("[AEC] failed to get IAudioCaptureClient")
                return False

            # 8. Start
            fn_Start = cast(vtbl_ac[10], ctypes.WINFUNCTYPE(
                ctypes.c_long, c_void_p))
            hr = fn_Start(pAudioClient)
            if hr < 0:
                _module_log("[AEC] IAudioClient::Start failed")
                return False

            self._audio_client = pAudioClient
            self._capture_client = pCaptureClient
            self._dev_name = dev_name

            # Release COM objects no longer needed
            cast(vtbl_dev[2], ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p))(pDevice)
            cast(vtbl_enum[2], ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p))(pEnum)

            self._active = True
            self._current_device_name = dev_name

            # 9. Start capture thread
            self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
            self._capture_thread.start()

            # 10. Start device monitor thread
            self._device_check_thread = threading.Thread(target=self._device_check_loop, daemon=True)
            self._device_check_thread.start()

            return True

        except Exception as e:
            import traceback
            _module_log(f"[AEC] speaker capture failed: {e}")
            _module_log(traceback.format_exc())
            self._active = False
            return False

    def _get_default_device_name(self) -> Optional[str]:
        """获取当前默认渲染设备名称"""
        try:
            ole32 = ctypes.windll.ole32
            ole32.CoInitializeEx(None, self.COINIT_MULTITHREADED)

            def _make_guid(s):
                buf = (ctypes.c_ubyte * 16)()
                ole32.CLSIDFromString(s, buf)
                return buf

            CLSID_MMDE = _make_guid(self._CLSID_MMDeviceEnumerator)
            IID_IMMDE = _make_guid(self._IID_IMMDeviceEnumerator)

            pEnum = c_void_p()
            hr = ole32.CoCreateInstance(
                byref(CLSID_MMDE), None, self.CLSCTX_ALL,
                byref(IID_IMMDE), byref(pEnum))
            if hr < 0 or not pEnum:
                return None

            vtbl_enum = cast(pEnum, POINTER(POINTER(c_void_p))).contents

            fn_GetDefault = cast(vtbl_enum[4], ctypes.WINFUNCTYPE(
                ctypes.c_long, c_void_p, wintypes.DWORD, wintypes.DWORD, POINTER(c_void_p)))
            pDevice = c_void_p()
            hr = fn_GetDefault(pEnum, 0, 0, byref(pDevice))
            if hr < 0 or not pDevice:
                cast(vtbl_enum[2], ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p))(pEnum)
                return None

            vtbl_dev = cast(pDevice, POINTER(POINTER(c_void_p))).contents
            fn_OpenPS = cast(vtbl_dev[4], ctypes.WINFUNCTYPE(
                ctypes.c_long, c_void_p, wintypes.DWORD, POINTER(c_void_p)))
            pStore = c_void_p()
            dev_name = None

            if fn_OpenPS(pDevice, 0, byref(pStore)) == 0 and pStore:
                pkey_fmtid = _make_guid("{a45c254e-df1c-4efd-8020-67d146a850e0}")
                class PK(ctypes.Structure):
                    _fields_ = [("fmtid", ctypes.c_ubyte*16), ("pid", wintypes.DWORD)]
                pk = PK()
                ctypes.memmove(pk.fmtid, pkey_fmtid, 16)
                pk.pid = 14
                vtbl_store = cast(pStore, POINTER(POINTER(c_void_p))).contents
                fn_GetValue = cast(vtbl_store[5], ctypes.WINFUNCTYPE(
                    ctypes.c_long, c_void_p, ctypes.c_void_p, ctypes.c_void_p))
                pv = (ctypes.c_ubyte * 24)()
                if fn_GetValue(pStore, byref(pk), pv) == 0:
                    vt = ctypes.c_ushort.from_buffer(pv, 0).value
                    if vt == 31:  # VT_LPWSTR
                        ptr = c_void_p.from_buffer(pv, 8).value
                        if ptr:
                            dev_name = ctypes.wstring_at(ptr)
                cast(vtbl_store[2], ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p))(pStore)

            cast(vtbl_dev[2], ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p))(pDevice)
            cast(vtbl_enum[2], ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p))(pEnum)

            return dev_name
        except Exception:
            return None

    def _device_check_loop(self) -> None:
        """后台线程：定期检查默认设备是否变化，如果变化则自动重新连接"""
        while self._active:
            try:
                time.sleep(self.DEVICE_CHECK_INTERVAL)

                if not self._active:
                    break

                new_device_name = self._get_default_device_name()
                if new_device_name and new_device_name != self._current_device_name:
                    _module_log(f"[AEC] device changed: {self._current_device_name} -> {new_device_name}")

                    # Stop old capture (but keep monitor thread)
                    with self._lock:
                        self._stop_capture_internal()

                    # Reinitialize capture (don't start new monitor thread)
                    if self._restart_capture():
                        _module_log(f"[AEC] device switched: {self._dev_name} (sr={self._dev_sr}Hz)")
                        # Notify parent to update far-end sample rate
                        if self._on_device_changed:
                            try:
                                self._on_device_changed(self._dev_sr)
                            except Exception:
                                pass
                    else:
                        _module_log(f"[AEC] device switch failed")

            except Exception as e:
                _module_log(f"[AEC] device check error: {e}")
                time.sleep(1.0)

    def _restart_capture(self) -> bool:
        """重新初始化采集（不启动新的监听线程）"""
        try:
            ole32 = ctypes.windll.ole32
            ole32.CoInitializeEx(None, self.COINIT_MULTITHREADED)

            def _make_guid(s):
                buf = (ctypes.c_ubyte * 16)()
                ole32.CLSIDFromString(s, buf)
                return buf

            CLSID_MMDE = _make_guid(self._CLSID_MMDeviceEnumerator)
            IID_IMMDE = _make_guid(self._IID_IMMDeviceEnumerator)
            IID_IMMD  = _make_guid(self._IID_IMMDevice)
            IID_IAC   = _make_guid(self._IID_IAudioClient)
            IID_IACC  = _make_guid(self._IID_IAudioCaptureClient)

            pEnum = c_void_p()
            hr = ole32.CoCreateInstance(
                byref(CLSID_MMDE), None, self.CLSCTX_ALL,
                byref(IID_IMMDE), byref(pEnum))
            if hr < 0 or not pEnum:
                return False

            vtbl_enum = cast(pEnum, POINTER(POINTER(c_void_p))).contents

            fn_GetDefault = cast(vtbl_enum[4], ctypes.WINFUNCTYPE(
                ctypes.c_long, c_void_p, wintypes.DWORD, wintypes.DWORD, POINTER(c_void_p)))
            pDevice = c_void_p()
            hr = fn_GetDefault(pEnum, 0, 0, byref(pDevice))
            if hr < 0 or not pDevice:
                cast(vtbl_enum[2], ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p))(pEnum)
                return False

            vtbl_dev = cast(pDevice, POINTER(POINTER(c_void_p))).contents
            fn_OpenPS = cast(vtbl_dev[4], ctypes.WINFUNCTYPE(
                ctypes.c_long, c_void_p, wintypes.DWORD, POINTER(c_void_p)))
            pStore = c_void_p()
            dev_name = "Unknown"

            if fn_OpenPS(pDevice, 0, byref(pStore)) == 0 and pStore:
                pkey_fmtid = _make_guid("{a45c254e-df1c-4efd-8020-67d146a850e0}")
                class PK(ctypes.Structure):
                    _fields_ = [("fmtid", ctypes.c_ubyte*16), ("pid", wintypes.DWORD)]
                pk = PK()
                ctypes.memmove(pk.fmtid, pkey_fmtid, 16)
                pk.pid = 14
                vtbl_store = cast(pStore, POINTER(POINTER(c_void_p))).contents
                fn_GetValue = cast(vtbl_store[5], ctypes.WINFUNCTYPE(
                    ctypes.c_long, c_void_p, ctypes.c_void_p, ctypes.c_void_p))
                pv = (ctypes.c_ubyte * 24)()
                if fn_GetValue(pStore, byref(pk), pv) == 0:
                    vt = ctypes.c_ushort.from_buffer(pv, 0).value
                    if vt == 31:
                        ptr = c_void_p.from_buffer(pv, 8).value
                        if ptr:
                            dev_name = ctypes.wstring_at(ptr)
                cast(vtbl_store[2], ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p))(pStore)

            fn_Activate = cast(vtbl_dev[3], ctypes.WINFUNCTYPE(
                ctypes.c_long, c_void_p, ctypes.c_void_p, wintypes.DWORD,
                c_void_p, POINTER(c_void_p)))
            pAudioClient = c_void_p()
            hr = fn_Activate(pDevice, byref(IID_IAC), self.CLSCTX_ALL,
                             None, byref(pAudioClient))
            if hr < 0 or not pAudioClient:
                cast(vtbl_dev[2], ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p))(pDevice)
                cast(vtbl_enum[2], ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p))(pEnum)
                return False

            vtbl_ac = cast(pAudioClient, POINTER(POINTER(c_void_p))).contents

            # 获取 MixFormat
            class WAVEFORMATEX(ctypes.Structure):
                _fields_ = [
                    ("wFormatTag",      wintypes.WORD),
                    ("nChannels",       wintypes.WORD),
                    ("nSamplesPerSec",  wintypes.DWORD),
                    ("nAvgBytesPerSec", wintypes.DWORD),
                    ("nBlockAlign",     wintypes.WORD),
                    ("wBitsPerSample",  wintypes.WORD),
                    ("cbSize",          wintypes.WORD),
                ]
            fn_GetMixFormat = cast(vtbl_ac[8], ctypes.WINFUNCTYPE(
                ctypes.c_long, c_void_p, POINTER(ctypes.c_void_p)))
            pWfx = ctypes.c_void_p()
            hr = fn_GetMixFormat(pAudioClient, byref(pWfx))
            if hr < 0 or not pWfx:
                cast(vtbl_dev[2], ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p))(pDevice)
                cast(vtbl_enum[2], ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p))(pEnum)
                return False
            wfx = cast(pWfx, POINTER(WAVEFORMATEX)).contents
            self._dev_sr = int(wfx.nSamplesPerSec)
            self._dev_name = dev_name
            self._current_device_name = dev_name

            REFERENCE_TIME = 100000  # 10ms buffer
            fn_Initialize = cast(vtbl_ac[3], ctypes.WINFUNCTYPE(
                ctypes.c_long, c_void_p, wintypes.DWORD, wintypes.DWORD,
                ctypes.c_longlong, ctypes.c_longlong, ctypes.c_void_p, ctypes.c_void_p))
            hr = fn_Initialize(
                pAudioClient,
                self.AUDCLNT_SHAREMODE_SHARED,
                self.AUDCLNT_STREAMFLAGS_LOOPBACK,
                REFERENCE_TIME, 0,
                pWfx, None)
            ole32.CoTaskMemFree(pWfx)

            fn_GetService = cast(vtbl_ac[14], ctypes.WINFUNCTYPE(
                ctypes.c_long, c_void_p, ctypes.c_void_p, POINTER(c_void_p)))
            pCaptureClient = c_void_p()
            hr = fn_GetService(pAudioClient, byref(IID_IACC), byref(pCaptureClient))
            if hr < 0 or not pCaptureClient:
                cast(vtbl_dev[2], ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p))(pDevice)
                cast(vtbl_enum[2], ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p))(pEnum)
                return False

            fn_Start = cast(vtbl_ac[10], ctypes.WINFUNCTYPE(
                ctypes.c_long, c_void_p))
            hr = fn_Start(pAudioClient)
            if hr < 0:
                cast(vtbl_dev[2], ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p))(pDevice)
                cast(vtbl_enum[2], ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p))(pEnum)
                return False

            self._audio_client = pAudioClient
            self._capture_client = pCaptureClient

            cast(vtbl_dev[2], ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p))(pDevice)
            cast(vtbl_enum[2], ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p))(pEnum)

            return True

        except Exception as e:
            _module_log(f"[AEC] restart capture failed: {e}")
            return False

    def _stop_capture_internal(self) -> None:
        """内部方法：停止采集（不停监听线程）"""
        if self._audio_client:
            try:
                vtbl_ac = ctypes.cast(self._audio_client, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
                fn_Stop = ctypes.cast(vtbl_ac[11], ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p))
                fn_Stop(self._audio_client)
                fn_Rel = ctypes.cast(vtbl_ac[2], ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p))
                fn_Rel(self._audio_client)
            except Exception:
                pass
            self._audio_client = None
        self._capture_client = None

    def _capture_loop(self) -> None:
        """后台线程：持续从 IAudioCaptureClient 读取并写入 RingBuffer。"""
        import struct

        vtbl_cc = cast(self._capture_client, POINTER(POINTER(c_void_p))).contents
        fn_GetBuffer = cast(vtbl_cc[3], ctypes.WINFUNCTYPE(
            ctypes.c_long, c_void_p,
            POINTER(ctypes.POINTER(ctypes.c_ubyte)),     # BYTE **ppData
            POINTER(wintypes.DWORD),                      # UINT32 *pNumFramesToRead
            POINTER(wintypes.DWORD),                      # DWORD *pdwFlags
            POINTER(ctypes.c_uint64),                     # UINT64 *pu64DevicePosition
            POINTER(ctypes.c_uint64)))                    # UINT64 *pu64QPCPosition
        fn_ReleaseBuffer = cast(vtbl_cc[4], ctypes.WINFUNCTYPE(
            ctypes.c_long, c_void_p, wintypes.DWORD))

        while self._active:
            try:
                pData = ctypes.POINTER(ctypes.c_ubyte)()
                numFrames = wintypes.DWORD()
                flags = wintypes.DWORD()
                devPos = ctypes.c_uint64()
                qpcPos = ctypes.c_uint64()
                hr = fn_GetBuffer(self._capture_client, byref(pData),
                                  byref(numFrames), byref(flags),
                                  byref(devPos), byref(qpcPos))
                if hr < 0 or numFrames.value == 0:
                    if hr != 0x88890008:  # AUDCLNT_S_BUFFER_EMPTY, normal
                        pass
                    time.sleep(0.001)
                    continue

                # Read audio data: float32 interleaved format
                frame_count = numFrames.value
                ch = self._dev_ch
                fmt = f'{frame_count * ch}f'
                raw = list(struct.unpack(fmt, bytes(pData[:frame_count * ch * 4])))

                fn_ReleaseBuffer(self._capture_client, numFrames)

                # Downmix to mono
                if ch > 1:
                    mono = [0.0] * frame_count
                    for i in range(frame_count):
                        s = 0.0
                        for c in range(ch):
                            s += raw[i * ch + c]
                        mono[i] = s / ch
                else:
                    mono = raw
                self._buffer.write(mono)

            except Exception:
                time.sleep(0.005)

    def stop(self) -> None:
        """停止 loopback 采集。"""
        self._active = False

        # Stop device monitor thread
        if self._device_check_thread:
            self._device_check_thread.join(timeout=1.0)
            self._device_check_thread = None

        # Stop capture thread
        if self._capture_thread:
            self._capture_thread.join(timeout=1.0)
            self._capture_thread = None

        # Release audio client
        if self._audio_client:
            try:
                vtbl_ac = ctypes.cast(self._audio_client, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
                fn_Stop = ctypes.cast(vtbl_ac[11], ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p))
                fn_Stop(self._audio_client)
                fn_Rel = ctypes.cast(vtbl_ac[2], ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p))
                fn_Rel(self._audio_client)
            except Exception:
                pass
            self._audio_client = None
        self._capture_client = None

    def read(self, n_samples: int) -> Optional[list]:
        """从缓冲区读取 n_samples 个 FIFO 采样；数据不足时返回 None。"""
        return self._buffer.read(n_samples)

    def flush(self) -> None:
        """清空缓冲区。"""
        self._buffer = RingBuffer(HOP_LENGTH * 2)
