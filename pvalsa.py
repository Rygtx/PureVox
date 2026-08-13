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
#
# pvalsa — 原生 ALSA 桥（alsa_client.c → libpvalsa.so）的 ctypes 绑定。
#
# Linux 第二个本地音频后端（默认仍为原生 PipeWire）。I/O 线程把 PCM 数据
# 搬进/搬出无锁 SPSC 环，Python 线程经本模块 read()/write() 搬运。AlsaBridge
# 类 API 与 PwBridge 一致：
#   open / close / active / last_error / sample_rate / buffer_size
#   set_monitor / set_far / read / read_far / write
#
# 仅 Linux。库加载失败时抛 ImportError（由 pvplatform 层捕获并降级）。

import ctypes as _ct
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
IS_LINUX = sys.platform.startswith("linux")

_c_void_p = _ct.c_void_p
_c_float_p = _ct.POINTER(_ct.c_float)
_c_csize = _ct.c_size_t


def _load_lib():
    if not IS_LINUX:
        raise ImportError("pvalsa 仅支持 Linux（原生 ALSA）")
    errors = []
    for name in ("libpvalsa.so",):
        for base in (_HERE, ""):
            path = os.path.join(base, name) if base else name
            try:
                return _ct.CDLL(path)
            except OSError as e:
                errors.append("%s: %s" % (path, e))
    raise ImportError("无法加载 pvalsa 共享库（libasound 依赖缺失或未构建）：%s"
                      % "; ".join(errors))
    return None


_lib = _load_lib()


def _fn(name, restype, *argtypes):
    f = getattr(_lib, name)
    f.restype = restype
    f.argtypes = list(argtypes)
    return f


_als_new = _fn("als_new", _c_void_p)
_als_free = _fn("als_free", None, _c_void_p)
_als_open = _fn("als_open", _ct.c_int, _c_void_p, _ct.c_char_p, _ct.c_char_p, _ct.c_char_p)
_als_close = _fn("als_close", None, _c_void_p)
_als_active = _fn("als_active", _ct.c_int, _c_void_p)
_als_last_error = _fn("als_last_error", _ct.c_char_p, _c_void_p)
_als_sample_rate = _fn("als_sample_rate", _ct.c_uint32, _c_void_p)
_als_buffer_size = _fn("als_buffer_size", _ct.c_uint32, _c_void_p)
_als_set_monitor = _fn("als_set_monitor", _ct.c_int, _c_void_p, _ct.c_char_p, _ct.c_int)
_als_set_far = _fn("als_set_far", _ct.c_int, _c_void_p, _ct.c_char_p, _ct.c_int)
_als_read = _fn("als_read", _c_csize, _c_void_p, _c_float_p, _c_csize)
_als_read_far = _fn("als_read_far", _c_csize, _c_void_p, _c_float_p, _c_csize)
_als_write = _fn("als_write", None, _c_void_p, _c_float_p, _c_csize)


class AlsaBridge:
    """PureVox 原生 ALSA 桥：input 采集 + output 播放 + 可选 monitor/far。

    F32 单声道 48000Hz，由 plughw 插件层负责重采样与声道转换。
    Python 线程 read()/write() 搬运，I/O 线程只动无锁环形缓冲。
    """

    def __init__(self):
        self._p = _als_new()
        self._free = _als_free
        if not self._p:
            raise RuntimeError("pvalsa: AlsaBridge alloc failed")

    def __del__(self):
        try:
            if getattr(self, "_p", None):
                self.close()
                self._free(self._p)
                self._p = None
        except Exception:
            pass

    def open(self, input_name, output_name, monitor_name=""):
        return bool(_als_open(self._p,
                              (input_name or "").encode("utf-8"),
                              (output_name or "").encode("utf-8"),
                              (monitor_name or "").encode("utf-8")))

    def close(self):
        _als_close(self._p)

    def active(self):
        return bool(_als_active(self._p))

    def last_error(self):
        s = _als_last_error(self._p)
        return s.decode("utf-8", "replace") if s else ""

    def sample_rate(self):
        return int(_als_sample_rate(self._p))

    def buffer_size(self):
        return int(_als_buffer_size(self._p))

    def set_monitor(self, monitor_name, enabled):
        return bool(_als_set_monitor(self._p, (monitor_name or "").encode("utf-8"),
                                     int(bool(enabled))))

    def set_far(self, far_name, enabled):
        """运行时开关 AEC far 采集流（从指定 capture 设备读取扬声器输出）。"""
        return bool(_als_set_far(self._p, (far_name or "").encode("utf-8"),
                                 int(bool(enabled))))

    def read(self, n):
        """从输入环读最多 n 个样本；无数据返回 None。"""
        if n <= 0:
            return None
        arr = (_ct.c_float * n)()
        got = _als_read(self._p, arr, n)
        if got == 0:
            return None
        return list(arr[:got])

    def read_far(self, n):
        """从 far 环读最多 n 个样本；无数据返回 None。"""
        if n <= 0:
            return None
        arr = (_ct.c_float * n)()
        got = _als_read_far(self._p, arr, n)
        if got == 0:
            return None
        return list(arr[:got])

    def write(self, data):
        """写样本到输出环（满则丢新；开启监听时同步写监听环）。"""
        if not data:
            return
        arr = (_ct.c_float * len(data))(*data)
        _als_write(self._p, arr, len(data))