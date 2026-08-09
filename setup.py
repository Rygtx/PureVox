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
# 纯 gcc 构建（无 C++、无 pybind11）：
#   python setup.py build_ext --inplace --force
# 产出  libaimic.so   （aimic.c + pffft + libsamplerate，链接捆绑 onnxruntime 1.11.1）
#       libpvpipe.so  （pipewire_client.c，链接系统 libpipewire-0.3）
# Python 侧由 aimic.py / pvpipe.py 用 ctypes 加载。
#
# onnxruntime 头/库目录环境变量覆盖（CI/pip 场景）：
#   ORT_INCLUDE_DIR / ORT_LIB_DIR（默认 packages/onnxruntime-linux-x64-1.11.1）

import os
import subprocess
import sys

from setuptools import setup
from setuptools.command.build_ext import build_ext as _build_ext

ROOT = os.path.dirname(os.path.abspath(__file__))
IS_LINUX = sys.platform.startswith("linux")
IS_WIN = sys.platform.startswith("win")

PFFFT_SOURCES = [
    "packages/pffft/pffft.c",
    "packages/pffft/pffft_double.c",
    "packages/pffft/pffastconv.c",
    "packages/pffft/pffft_common.c",
]

LIBSAMPLERATE_SOURCES = [
    "packages/libsamplerate/samplerate.c",
    "packages/libsamplerate/src_sinc.c",
    "packages/libsamplerate/src_linear.c",
    "packages/libsamplerate/src_zoh.c",
]


def _abs(*parts):
    return os.path.join(ROOT, *parts)


class BuildExt(_build_ext):
    def run(self):
        if IS_WIN:
            self._build_aimic_windows()
        elif IS_LINUX:
            self._build_aimic_linux()
            self._build_pvpipe_linux()
        else:
            raise SystemExit(
                "PureVox setup.py build_ext 仅支持 Linux(gcc)/Windows(mingw)，"
                "macOS 暂不支持")

    def _aimic_ort_paths(self, package):
        ort_inc = os.environ.get("ORT_INCLUDE_DIR",
                                 _abs("packages", package, "include"))
        ort_lib = os.environ.get("ORT_LIB_DIR",
                                 _abs("packages", package, "lib"))
        return ort_inc, ort_lib

    def _aimic_sources(self):
        return [_abs("aimic.c")] + [_abs(s) for s in PFFFT_SOURCES + LIBSAMPLERATE_SOURCES]

    def _build_aimic_windows(self):
        """Windows: mingw gcc 编 aimic.dll（aimic.c + pffft + libsamplerate）。

        onnxruntime 用仓库内 Windows SDK import lib（onnxruntime.lib）链接，
        onnxruntime.dll 由 aimic.py 运行期预加载（_preload_onnxruntime）。
        """
        cc = os.environ.get("CC", "gcc")
        ort_inc, ort_lib = self._aimic_ort_paths("onnxruntime-win-x64-1.11.1")
        cmd = [
            cc, "-O2", "-shared", "-static-libgcc", "-mavx2", "-std=gnu11",
            "-DHAVE_CONFIG_H",
            # mingw gcc 缺 MSVC SAL 宏（_Frees_ptr_opt_ 等），onnxruntime 头会
            # 因未定义宏连锁产生未知类型 → Release* API 缺失。Win7 实测必须 -include 此头。
            "-include", _abs("sal_fix.h"),
            "-I" + _abs("packages", "pffft"),
            "-I" + _abs("packages", "libsamplerate"),
            "-I" + ort_inc,
        ] + self._aimic_sources() + [
            "-L" + ort_lib, "-lonnxruntime",
            "-o", _abs("aimic.dll"),
        ]
        self._run(cmd)

    def _build_aimic_linux(self):
        cc = os.environ.get("CC", "gcc")
        ort_inc, ort_lib = self._aimic_ort_paths("onnxruntime-linux-x64-1.11.1")
        cmd = [
            cc, "-O2", "-fPIC", "-mavx2", "-std=gnu11", "-DHAVE_CONFIG_H",
            "-I" + _abs("packages", "pffft"),
            "-I" + _abs("packages", "libsamplerate"),
            "-I" + ort_inc,
        ] + self._aimic_sources() + [
            "-L" + ort_lib, "-lonnxruntime",
            "-lm", "-pthread",
            "-shared",
            "-o", _abs("libaimic.so"),
        ]
        self._run(cmd)

    def _build_pvpipe_linux(self):
        cc = os.environ.get("CC", "gcc")
        cflags = subprocess.run(["pkg-config", "--cflags", "libpipewire-0.3"],
                                capture_output=True, text=True).stdout.strip().split()
        libs = subprocess.run(["pkg-config", "--libs", "libpipewire-0.3"],
                              capture_output=True, text=True).stdout.strip().split()
        cmd = [
            cc, "-O2", "-fPIC",
        ] + cflags + [
            _abs("pipewire_client.c"),
        ] + libs + [
            "-shared",
            "-o", _abs("libpvpipe.so"),
        ]
        self._run(cmd)

    @staticmethod
    def _run(cmd):
        print("+ " + " ".join(cmd))
        subprocess.check_call(cmd)


setup(
    name="purevox-aimic",
    version="1.0.0",
    description=("Pure Vox pure C audio engine (aimic.c / pipewire_client.c) "
                 "shared libraries + ctypes binding"),
    cmdclass={"build_ext": BuildExt},
    ext_modules=[],
)