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
平台感知的音频设备 API 枚举。

PortAudio 的 host API 类型编号因平台而异（WASAPI=13、PulseAudio=15、
ALSA=8、JACK=12、Core Audio=5…），且同一数值在另一平台毫无意义。
为避免把 Windows 的 13 硬编码到 Linux，本模块提供：

    resolve_api_names(api_type)  配置值 → 候选 host API 名字列表
    _get_host_api_indices(p, api_type)  按名字匹配 host API 索引
    platform_default_api_type()  当前平台默认 API 数值
    get_api_options()            UI 下拉选项 [(label, type), ...]
    get_api_name(api_type)       类型 → 显示名

核心策略：配置存的 api_type 若在本机不存在（如 Windows 的 WASAPI=13
在 Linux 上），自动回退到平台默认 host API（Linux: PulseAudio → ALSA）。
"""

from .. import IS_WINDOWS, IS_LINUX, IS_MACOS

# 端口音频 host API 类型编号（PortAudio PaHostApiTypeId）
API_DIRECTSOUND = 1
API_MME = 2
API_ASIO = 3
API_COREAUDIO = 5
API_OSS = 7
API_ALSA = 8
API_JACK = 12
API_WASAPI = 13
API_PULSE = 15
API_SNDIO = 16

# 网络输入模式（非 PortAudio host API）
API_NETWORK = 99

# 编号 → host API 显示名（仅列当前会用到的）
PTYPE_TO_NAME = {
    API_DIRECTSOUND: "DirectSound",
    API_MME: "MME",
    API_ASIO: "ASIO",
    API_COREAUDIO: "Core Audio",
    API_OSS: "OSS",
    API_ALSA: "ALSA",
    API_JACK: "JACK",
    API_WASAPI: "WASAPI",
    API_PULSE: "PulseAudio",
    API_SNDIO: "Sndio",
}

# 显示名 → 类型编号
NAME_TO_PTYPE = {v: k for k, v in PTYPE_TO_NAME.items()}


def platform_default_api_type() -> int:
    """返回当前平台默认的 PortAudio host API 类型编号。"""
    if IS_WINDOWS:
        return API_WASAPI
    if IS_LINUX:
        # 现代 Linux 桌面普遍是 PipeWire（带 PulseAudio 兼容层），Pulse 优先
        return API_PULSE
    if IS_MACOS:
        return API_COREAUDIO
    return API_ALSA


def platform_api_names() -> list:
    """当前平台候选 host API 名字（按优先级排序）。"""
    if IS_WINDOWS:
        return ["WASAPI"]
    if IS_LINUX:
        return ["PulseAudio", "ALSA"]
    if IS_MACOS:
        return ["Core Audio"]
    return ["ALSA"]


def get_api_name(api_type: int) -> str:
    """类型编号 → 显示名。网络模式返回 'NETWORK'。"""
    if api_type == API_NETWORK:
        return "NETWORK"
    return PTYPE_TO_NAME.get(api_type, f"API({api_type})")


def get_api_options() -> list:
    """返回 UI 下拉选项 [(label, type), ...]：一个本地 + 网络。"""
    local_type = platform_default_api_type()
    opts = [("本地设备", local_type)]
    opts.append(("网络(API)", API_NETWORK))
    return opts


def resolve_api_names(api_type: int) -> list:
    """把配置的 api_type 解析为实际 host API 名字候选列表。

    规则：
      - 网络模式（99）→ 空列表（不走 PortAudio host API）。
      - 配置的 API 名若在本机 host API 中存在，则匹配之。
      - 否则回退到平台默认候选（Windows 的 13 在 Linux 上自动变
        PulseAudio → ALSA）。
    """
    if api_type == API_NETWORK:
        return []
    names = []
    if api_type in PTYPE_TO_NAME:
        names.append(PTYPE_TO_NAME[api_type])
    for default_name in platform_api_names():
        if default_name not in names:
            names.append(default_name)
    return names


def get_host_api_indices(p, api_type: int) -> list:
    """按名字匹配 host API 索引列表（跨平台）。

    若配置的 API 名在本机一个都没匹配到（例如配置存了 Windows 的
    PulseAudio/WASAPI，但当前 PortAudio 构建只有 ALSA/OSS/JACK），
    回退到全部 host API——保证虚拟 sink（可能挂在任意 API 下）仍能被枚举。
    """
    names = resolve_api_names(api_type)
    if not names:
        return []
    indices = []
    for i in range(p.get_host_api_count()):
        try:
            info = p.get_host_api_info_by_index(i)
        except Exception:
            continue
        if info.get('name') in names:
            indices.append(i)
    # 配置的 API 名在本机不存在 → 全枚举兜底（虚拟 sink 等跨 API 设备）
    if not indices:
        return list(range(p.get_host_api_count()))
    return indices
