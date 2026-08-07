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

from setuptools import setup, Extension
from setuptools.command.build_ext import build_ext
import sys
import os

# 设置扩展模块名称
module_name = "aimic"

# 设置源文件
source_files = ["aimic.cpp"]

# libsamplerate 源文件
libsamplerate_source_files = [
    "packages/libsamplerate/samplerate.c",
    "packages/libsamplerate/src_sinc.c",
    "packages/libsamplerate/src_linear.c",
    "packages/libsamplerate/src_zoh.c"
]

# 设置PFFFT源文件
pffft_source_files = [
    "packages/pffft/pffft.c",
    "packages/pffft/pffft_double.c",
    "packages/pffft/pffastconv.c",
    "packages/pffft/pffft_common.c"
]

# 设置头文件目录
import pybind11

# 获取pybind11的include路径
pybind11_include = pybind11.get_include()

# ── 平台相关配置（Windows 用捆绑的 onnxruntime SDK；Linux/macOS 用系统安装的 libonnxruntime） ──
IS_WINDOWS = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")
IS_MACOS = sys.platform == "darwin"

if IS_WINDOWS:
    ORT_SDK = "packages/onnxruntime-win-x64-1.24.4"
    onnxruntime_include = f"{ORT_SDK}/include"
    onnxruntime_libdir = f"{ORT_SDK}/lib"
    # 链接的库：Windows 需要 providers_shared
    onnx_libraries = ["onnxruntime", "onnxruntime_providers_shared"]
    extra_compile_args = [
        "/O2",               # 优化级别
        "/std:c++17",        # C++17标准
        "/arch:AVX2",        # 启用AVX2指令集
        "/MT",               # 静态链接MSVC运行时
        "/DHAVE_CONFIG_H",   # libsamplerate 配置头
    ]
    extra_link_args = []
else:
    # Linux/macOS：默认系统级 onnxruntime（oma 包 onnxruntime，/usr/include/onnxruntime + /usr/lib）。
    # CI / pip 安装场景可用环境变量覆盖（ORT_INCLUDE_DIR / ORT_LIB_DIR 指向 onnxruntime wheel）。
    onnxruntime_include = os.environ.get("ORT_INCLUDE_DIR", "/usr/include/onnxruntime")
    onnxruntime_libdir = os.environ.get("ORT_LIB_DIR", "/usr/lib")
    onnx_libraries = ["onnxruntime"]
    extra_compile_args = [
        "-O2",
        "-std=c++17",
        "-mavx2",
        "-fPIC",
        "-DHAVE_CONFIG_H",
    ]
    extra_link_args = []

include_dirs = [
    "packages/pffft",
    onnxruntime_include,
    "packages/libsamplerate",
    pybind11_include,
    os.path.join(pybind11_include, "pybind11")
]

library_dirs = [
    onnxruntime_libdir
]

# 创建扩展模块
extension = Extension(
    module_name,
    sources=source_files + pffft_source_files + libsamplerate_source_files,
    include_dirs=include_dirs,
    library_dirs=library_dirs,
    libraries=onnx_libraries,
    extra_compile_args=extra_compile_args,
    extra_link_args=extra_link_args,
    language="c++"
)

# Linux：原生 PipeWire 桥接扩展（pvpipe，链接系统 libpipewire-0.3）。
# 声明 F32 单声道 48000Hz，PipeWire 负责重采样与声道转换。
ext_modules = [extension]
if IS_LINUX:
    import subprocess as _sp
    pw_cflags = _sp.run(["pkg-config", "--cflags", "libpipewire-0.3"],
                        capture_output=True, text=True).stdout.strip()
    pw_libs = _sp.run(["pkg-config", "--libs", "libpipewire-0.3"],
                      capture_output=True, text=True).stdout.strip()
    pw_extension = Extension(
        "pvpipe",
        sources=["pipewire_client.cpp"],
        include_dirs=[pybind11_include, os.path.join(pybind11_include, "pybind11")],
        libraries=["pipewire-0.3"],
        extra_compile_args=["-O2", "-std=c++17", "-fPIC"] + pw_cflags.split(),
        extra_link_args=pw_libs.split(),
        language="c++"
    )
    ext_modules.append(pw_extension)

# 自定义构建命令
class BuildExt(build_ext):
    def build_extensions(self):
        # 确保使用MSVC编译器（仅 Windows）
        if self.compiler.compiler_type == 'msvc':
            # 添加额外的编译选项
            for ext in self.extensions:
                ext.extra_compile_args.extend([
                    "/EHsc",  # 异常处理
                    "/DNDEBUG",  # 禁用调试信息
                    "/DONNXruntime_API=__declspec(dllimport)"
                ])
        build_ext.build_extensions(self)

# 设置setup参数
setup(
    name=module_name,
    version="1.0.0",
    description="基于 ONNX Runtime 和 PFFFT 的音频处理模块",
    author="",
    author_email="",
    ext_modules=ext_modules,
    cmdclass={
        'build_ext': BuildExt
    },
    install_requires=[
        "pybind11==3.0.1"
    ]
)
