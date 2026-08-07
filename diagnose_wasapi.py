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
WASAPI 设备诊断脚本
枚举所有音频端点并打印 PyAudio 信息 vs Windows Core Audio 真实混音格式
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import ctypes
from ctypes import wintypes, POINTER, byref, cast, c_void_p, sizeof
import pyaudio

# ==================== Windows Core Audio API ====================

_ole32 = ctypes.windll.ole32
CLSCTX_ALL = 0x17
COINIT_MULTITHREADED = 0x0

def _guid(s):
    buf = (ctypes.c_ubyte * 16)()
    ctypes.windll.ole32.CLSIDFromString(s, buf)
    return buf

CLSID_MMDeviceEnumerator = _guid("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
IID_IMMDeviceEnumerator    = _guid("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
IID_IMMDevice              = _guid("{D666063F-1587-4E43-81F1-B948E807363F}")
IID_IAudioClient           = _guid("{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}")
IID_IPropertyStore         = _guid("{886d8eeb-8cf2-4446-8d02-cdba1dbdcf99}")

# PKEY_Device_FriendlyName
class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", ctypes.c_ubyte * 16), ("pid", wintypes.DWORD)]
PKEY_FriendlyName = PROPERTYKEY()
ctypes.windll.ole32.CLSIDFromString("{a45c254e-df1c-4efd-8020-67d146a850e0}", PKEY_FriendlyName.fmtid)
PKEY_FriendlyName.pid = 14

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


def get_endpoint_name(pDevice):
    """通过 IPropertyStore 获取设备友好名称"""
    vtbl_dev = cast(pDevice, POINTER(POINTER(c_void_p))).contents
    fn_OpenPS = cast(vtbl_dev[4], ctypes.WINFUNCTYPE(
        ctypes.c_long, c_void_p, wintypes.DWORD, POINTER(c_void_p)))
    pStore = c_void_p()
    if fn_OpenPS(pDevice, 0, byref(pStore)) != 0 or not pStore:
        return None
    try:
        vtbl_store = cast(pStore, POINTER(POINTER(c_void_p))).contents
        fn_GetValue = cast(vtbl_store[5], ctypes.WINFUNCTYPE(
            ctypes.c_long, c_void_p, ctypes.c_void_p, ctypes.c_void_p))
        pv = (ctypes.c_ubyte * 24)()
        if fn_GetValue(pStore, byref(PKEY_FriendlyName), pv) == 0:
            vt = ctypes.c_ushort.from_buffer(pv, 0).value
            if vt == 31:  # VT_LPWSTR
                ptr = ctypes.c_void_p.from_buffer(pv, 8).value
                if ptr:
                    return ctypes.wstring_at(ptr)
    finally:
        fn_rel = cast(vtbl_store[2], ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p))
        fn_rel(pStore)
    return None


def get_mix_format(pDevice):
    """获取 IAudioClient::GetMixFormat"""
    vtbl_dev = cast(pDevice, POINTER(POINTER(c_void_p))).contents
    fn_Activate = cast(vtbl_dev[3], ctypes.WINFUNCTYPE(
        ctypes.c_long, c_void_p, ctypes.c_void_p, wintypes.DWORD,
        c_void_p, POINTER(c_void_p)))
    pAudioClient = c_void_p()
    hr = fn_Activate(pDevice, byref(IID_IAudioClient), CLSCTX_ALL, None, byref(pAudioClient))
    if hr != 0 or not pAudioClient:
        print(f"      Activate IAudioClient 失败: 0x{hr:08X}")
        return None
    try:
        vtbl_ac = cast(pAudioClient, POINTER(POINTER(c_void_p))).contents
        fn_GetMixFormat = cast(vtbl_ac[8], ctypes.WINFUNCTYPE(
            ctypes.c_long, c_void_p, POINTER(POINTER(WAVEFORMATEX))))
        pWfx = POINTER(WAVEFORMATEX)()
        hr2 = fn_GetMixFormat(pAudioClient, byref(pWfx))
        if hr2 == 0 and pWfx:
            wfx = pWfx.contents
            sr = wfx.nSamplesPerSec
            ch = wfx.nChannels
            bits = wfx.wBitsPerSample
            tag = wfx.wFormatTag
            cb = wfx.cbSize
            _ole32.CoTaskMemFree(pWfx)
            return (sr, ch, bits, tag, cb)
        else:
            print(f"      GetMixFormat 失败: 0x{hr2:08X}")
    finally:
        fn_rel = cast(vtbl_ac[2], ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p))
        fn_rel(pAudioClient)
    return None


def release(p):
    if p:
        vtbl = cast(p, POINTER(POINTER(c_void_p))).contents
        fn = cast(vtbl[2], ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p))
        fn(p)


# ==================== 主流程 ====================

print("=" * 70)
print("WASAPI 设备诊断")
print("=" * 70)

# --- Part 1: PyAudio 设备列表 ---
print("\n--- PyAudio 设备列表 ---")
pa = pyaudio.PyAudio()
wasapi_devices = {}  # index -> (name, maxIn, maxOut, defSR, hostApi)

for i in range(pa.get_device_count()):
    dev = pa.get_device_info_by_index(i)
    ha = pa.get_host_api_info_by_index(dev['hostApi'])
    if ha['type'] == 13:  # WASAPI
        wasapi_devices[i] = (
            dev['name'].strip(),
            dev['maxInputChannels'],
            dev['maxOutputChannels'],
            dev['defaultSampleRate'],
            dev['hostApi'],
        )
        dir_tag = []
        if dev['maxInputChannels'] > 0:
            dir_tag.append("IN")
        if dev['maxOutputChannels'] > 0:
            dir_tag.append("OUT")
        print(f"  [{i:2d}] {'+'.join(dir_tag):6s} | {dev['name']}")

pa.terminate()

# --- Part 2: Windows Core Audio 端点 ---
print("\n--- Windows Core Audio 端点 ---")

hr = _ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
print(f"CoInitializeEx: 0x{hr:08X}")

# Create enumerator
pEnum = c_void_p()
hr = _ole32.CoCreateInstance(
    byref(CLSID_MMDeviceEnumerator), None, CLSCTX_ALL,
    byref(IID_IMMDeviceEnumerator), byref(pEnum))
print(f"CoCreateInstance(MMDeviceEnumerator): 0x{hr:08X}, ptr={pEnum.value:#x}")

if hr < 0 or not pEnum:
    print("FATAL: 无法创建 MMDeviceEnumerator")
    _ole32.CoUninitialize()
    exit(1)

vtbl_enum = cast(pEnum, POINTER(POINTER(c_void_p))).contents
fn_Enum = cast(vtbl_enum[3], ctypes.WINFUNCTYPE(
    ctypes.c_long, c_void_p, wintypes.DWORD, wintypes.DWORD, POINTER(c_void_p)))

dataflow_names = {0: "eAll", 1: "eCapture", 2: "eRender"}

for dataflow in (1, 2):  # capture then render
    pColl = c_void_p()
    hr = fn_Enum(pEnum, dataflow, 0x1, byref(pColl))
    print(f"\n  EnumAudioEndpoints({dataflow_names[dataflow]}): 0x{hr:08X}")
    if hr < 0 or not pColl:
        continue

    vtbl_coll = cast(pColl, POINTER(POINTER(c_void_p))).contents
    fn_Count = cast(vtbl_coll[3], ctypes.WINFUNCTYPE(
        ctypes.c_long, c_void_p, POINTER(wintypes.UINT)))
    fn_Item = cast(vtbl_coll[4], ctypes.WINFUNCTYPE(
        ctypes.c_long, c_void_p, wintypes.UINT, POINTER(c_void_p)))

    cnt = wintypes.UINT()
    fn_Count(pColl, byref(cnt))
    print(f"  设备数: {cnt.value}")

    for idx in range(cnt.value):
        pDev = c_void_p()
        if fn_Item(pColl, idx, byref(pDev)) != 0 or not pDev:
            continue

        name = get_endpoint_name(pDev)
        fmt = get_mix_format(pDev)

        print(f"\n  [{idx}] name=\"{name}\"")
        if fmt:
            sr, ch, bits, tag, cb = fmt
            tag_names = {1: "PCM", 3: "IEEE_FLOAT", 0xFFFE: "EXTENSIBLE"}
            print(f"      格式: {sr}Hz, {ch}ch, {bits}bit, "
                  f"tag=0x{tag:04X}({tag_names.get(tag, '?')}), cbSize={cb}")

            # 匹配 PyAudio 设备
            for pai, (pa_name, maxIn, maxOut, defSR, ha) in wasapi_devices.items():
                if name and (name.lower() in pa_name.lower() or pa_name.lower() in name.lower()):
                    print(f"      → 匹配 PyAudio 设备 [{pai}] {pa_name} "
                          f"(maxIn={maxIn}, maxOut={maxOut}, defSR={defSR})")

        release(pDev)

    release(pColl)

release(pEnum)
_ole32.CoUninitialize()

print("\n" + "=" * 70)
print("诊断完成")
