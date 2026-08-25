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
平台音频后端抽象层。

对外暴露统一接口（与 audio_processor 的调用方契约一致）：

    SpeakerCapture
        扬声器 loopback 采集（AEC far-end 数据源）。
        接口：start() -> bool, stop(), read(n) -> Optional[list],
              dev_sr(int), active(bool), flush()。

    create_speaker_capture(on_device_changed=None, pw_bridge=None, far_sink="")
        工厂函数，按当前平台返回具体后端实例：
        - Windows: WASAPI loopback（COM）
        - Linux:   原生 PipeWire capture.sink（复用已有 PwBridge，AEC far 流）
        - macOS:   预留
    pw_bridge / far_sink 仅 Linux 使用：pw_bridge 为已打开的 PwBridge；
    far_sink 为扬声器 sink 节点名（空则取物理扬声器兜底）。
"""

from .. import IS_WINDOWS, IS_LINUX, IS_MACOS


def create_speaker_capture(on_device_changed=None, pw_bridge=None, far_sink=""):
    """按平台创建 SpeakerCapture 实例。"""
    if IS_WINDOWS:
        from .speaker_capture_win import SpeakerCaptureWin
        return SpeakerCaptureWin(on_device_changed=on_device_changed)
    if IS_LINUX:
        from .speaker_capture_linux import SpeakerCaptureLinux
        return SpeakerCaptureLinux(on_device_changed=on_device_changed,
                                   bridge=pw_bridge, sink_name=far_sink)
    if IS_MACOS:
        from .speaker_capture_macos import SpeakerCaptureMacOS
        return SpeakerCaptureMacOS(on_device_changed=on_device_changed)
    raise RuntimeError(f"不支持的平台: {__import__('sys').platform}")


__all__ = ["create_speaker_capture"]
