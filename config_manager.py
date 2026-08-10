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

# config_manager.py
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List


def _default_api_type() -> int:
    """平台默认音频 API 类型（WASAPI=13 / PulseAudio=15 / ALSA=8 / CoreAudio=5）。"""
    if sys.platform.startswith("linux"):
        return 15  # API_TYPE_PULSE
    if sys.platform.startswith("darwin"):
        return 5   # API_TYPE_COREAUDIO
    return 13      # API_TYPE_WASAPI


def _default_output_device() -> str:
    """平台默认输出设备：
    - Windows 用 VB-CABLE 虚拟声卡（CABLE Input）
    - Linux/macOS 无虚拟声卡概念，留空由 UI 首项兜底
      （Linux 虚拟麦克风由系统桥接，不通过 PortAudio 直接打开）
    """
    if sys.platform.startswith("win"):
        return "CABLE Input"
    return ""


@dataclass
class ConfigDefaults:
    """默认配置常量。"""
    # 模式
    mode: str = "denoise"  # "off" / "denoise" / "tse"
    # 增益
    pre_gain_db: float = 0.0
    # AGC / VAD
    agc_enabled: bool = False
    vad_enabled: bool = False
    compressor_enabled: bool = False
    # 均衡器（8个预设插槽 + 当前激活的插槽 + 当前展示的增益）
    eq_preset_0: List[float] = field(default_factory=lambda: [0.0] * 61)
    eq_preset_1: List[float] = field(default_factory=lambda: [0.0] * 61)
    eq_preset_2: List[float] = field(default_factory=lambda: [0.0] * 61)
    eq_preset_3: List[float] = field(default_factory=lambda: [0.0] * 61)
    eq_preset_4: List[float] = field(default_factory=lambda: [0.0] * 61)
    eq_preset_5: List[float] = field(default_factory=lambda: [0.0] * 61)
    eq_preset_6: List[float] = field(default_factory=lambda: [0.0] * 61)
    eq_preset_7: List[float] = field(default_factory=lambda: [0.0] * 61)
    eq_active_slot: int = 0
    eq_current_gains: List[float] = field(default_factory=lambda: [0.0] * 61)
    # 设备
    api_type: int = field(default_factory=_default_api_type)
    input_device: str = ""
    output_device: str = field(default_factory=_default_output_device)
    monitor_device: str = ""
    aec_far_sink: str = ""  # AEC far 端手动选择（node.name，Linux）
    NETWORK_input_url: str = "ws://0.0.0.0:59123/ws/audio"
    monitor_enabled: bool = False
    # TSE 参考音频
    tse_reference_wav_path: str = ""
    # 服务器
    server_enabled: bool = False
    server_port: int = 59123
    # 主题
    theme: str = "system"  # "system" / "light" / "dark"
    # 启动 / 快捷键
    auto_start: bool = False
    registry_auto_start: bool = False
    hotkey_enabled: bool = True
    # VB-CABLE 检测（Windows 虚拟声卡）：False 表示用户勾选了"不再提示"
    vbcable_check_enabled: bool = True

    @classmethod
    def to_dict(cls) -> Dict[str, Any]:
        """将默认配置转换为字典。"""
        instance = cls()
        return {
            "mode": instance.mode,
            "pre_gain_db": instance.pre_gain_db,
            "agc_enabled": instance.agc_enabled,
            "vad_enabled": instance.vad_enabled,
            "compressor_enabled": instance.compressor_enabled,
            "api_type": instance.api_type,
            "input_device": instance.input_device,
            "output_device": instance.output_device,
            "monitor_device": instance.monitor_device,
            "aec_far_sink": instance.aec_far_sink,
            "NETWORK_input_url": instance.NETWORK_input_url,
            "monitor_enabled": instance.monitor_enabled,
            "tse_reference_wav_path": instance.tse_reference_wav_path,
            "server_enabled": instance.server_enabled,
            "server_port": instance.server_port,
            "theme": instance.theme,
            "auto_start": instance.auto_start,
            "registry_auto_start": instance.registry_auto_start,
            "hotkey_enabled": instance.hotkey_enabled,
            "vbcable_check_enabled": instance.vbcable_check_enabled,
            "eq_preset_0": instance.eq_preset_0.copy(),
            "eq_preset_1": instance.eq_preset_1.copy(),
            "eq_preset_2": instance.eq_preset_2.copy(),
            "eq_preset_3": instance.eq_preset_3.copy(),
            "eq_preset_4": instance.eq_preset_4.copy(),
            "eq_preset_5": instance.eq_preset_5.copy(),
            "eq_preset_6": instance.eq_preset_6.copy(),
            "eq_preset_7": instance.eq_preset_7.copy(),
            "eq_active_slot": instance.eq_active_slot,
            "eq_current_gains": instance.eq_current_gains.copy(),
        }


class ConfigManager:
    """应用配置管理器。"""

    def __init__(self, config_path: str) -> None:
        """使用配置文件路径初始化配置管理器。

        参数:
            config_path: 配置文件路径。
        """
        self._config_path: str = config_path
        self._config: Dict[str, Any] = ConfigDefaults.to_dict()
        self.load_config()

    def load_config(self) -> None:
        """从文件加载配置；文件缺失/损坏/为空时使用默认值。
        门卫模式：只保留已知配置键，不认识的一律删除。"""
        if not os.path.exists(self._config_path):
            return
        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return
                loaded_config: Dict[str, Any] = json.loads(content)

            # 兼容迁移：旧 key → 新 key
            _OLD_NEW = {
                "WASAPI_input_device": "input_device",
                "WASAPI_output_device": "output_device",
                "WASAPI_monitor_device": "monitor_device",
            }
            for old_k, new_k in _OLD_NEW.items():
                if old_k in loaded_config and new_k not in loaded_config:
                    loaded_config[new_k] = loaded_config.pop(old_k)
                elif old_k in loaded_config:
                    loaded_config.pop(old_k)

            defaults = ConfigDefaults.to_dict()
            # 白名单：只保留 defaults 中的键
            cleaned = {k: v for k, v in loaded_config.items() if k in defaults}
            self._config = {**defaults, **cleaned}
        except (json.JSONDecodeError, OSError):
            return

    # 键输出顺序：设备在前，EQ 在后
    _KEY_ORDER = [
        "mode",
        "pre_gain_db",
        "agc_enabled", "vad_enabled", "compressor_enabled",
        "api_type",
        "input_device", "output_device",
        "monitor_device",
        "aec_far_sink",
        "NETWORK_input_url", "monitor_enabled",
        "tse_reference_wav_path",
        "server_enabled", "server_port",
        "theme",
        "auto_start", "registry_auto_start", "hotkey_enabled",
        "vbcable_check_enabled",
        "eq_preset_0", "eq_preset_1", "eq_preset_2", "eq_preset_3",
        "eq_preset_4", "eq_preset_5", "eq_preset_6", "eq_preset_7",
        "eq_active_slot", "eq_current_gains",
    ]

    def save_config(self) -> None:
        """保存配置到文件。门卫模式：只写入已知键。"""
        dir_path = os.path.dirname(self._config_path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)

        defaults = ConfigDefaults.to_dict()
        # 白名单：只保留 defaults 中的键
        allowed = set(defaults.keys())

        ordered: Dict[str, Any] = {}
        for k in self._KEY_ORDER:
            if k in self._config and k in allowed:
                ordered[k] = self._config[k]

        with open(self._config_path, "w", encoding="utf-8") as f:
            json.dump(ordered, f, ensure_ascii=False, indent=2)

    def get(self, key: str, default: Any = None) -> Any:
        """按键获取配置值。

        参数:
            key: 配置键名。
            default: 键不存在时返回的默认值。

        返回:
            配置值或默认值。
        """
        return self._config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """设置配置值。

        参数:
            key: 配置键名。
            value: 要设置的值。
        """
        self._config[key] = value

    def get_all(self) -> Dict[str, Any]:
        """获取全部配置的副本。

        返回:
            全部配置值的副本。
        """
        return self._config.copy()
