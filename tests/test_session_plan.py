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

"""SessionPlan 纯函数测试（L3 会话计划契约，无音频副作用）：
python tests/test_session_plan.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from session_plan import SessionPlan


def test_valid_chain():
    plan = SessionPlan.from_chain([
        {"type": "audio_input", "enabled": True, "params": {"device": "Mic"}},
        {"type": "denoiser", "enabled": True, "params": {}},
        {"type": "audio_output", "enabled": True, "params": {"device": "Spk"}},
        {"type": "vu_meter", "enabled": True, "params": {}},
    ])
    assert plan.ok(), plan.problems
    assert plan.inputs == ("Mic",)
    assert plan.outputs == ("Spk",)
    assert plan.remote_url is None
    assert plan.viz == frozenset({"vu_meter"})
    assert plan.fx_chain == ({"type": "denoiser", "enabled": True, "params": {}},)
    print("  合法链 → ok、字段抽取正确  OK")


def test_no_input_blocked():
    plan = SessionPlan.from_chain([
        {"type": "audio_output", "enabled": True, "params": {"device": "Spk"}},
    ])
    assert not plan.ok()
    assert any("音频输入" in p for p in plan.problems)
    print("  无输入节点 → 阻断  OK")


def test_no_output_blocked():
    plan = SessionPlan.from_chain([
        {"type": "audio_input", "enabled": True, "params": {"device": "Mic"}},
    ])
    assert not plan.ok()
    assert any("音频输出" in p for p in plan.problems)
    print("  无输出节点 → 阻断  OK")


def test_empty_device_skipped_with_warning():
    plan = SessionPlan.from_chain([
        {"type": "audio_input", "enabled": True, "params": {"device": ""}},
        {"type": "denoiser", "enabled": True, "params": {}},
        {"type": "audio_output", "enabled": True, "params": {"device": ""}},
    ])
    assert not plan.ok()
    assert any("未选设备" in w for w in plan.warnings)
    assert plan.inputs == () and plan.outputs == ()
    print("  空 device 行跳过并告警  OK")


def test_disabled_nodes_skipped():
    plan = SessionPlan.from_chain([
        {"type": "audio_input", "enabled": False, "params": {"device": "Mic"}},
        {"type": "denoiser", "enabled": False, "params": {}},
        {"type": "audio_output", "enabled": True, "params": {"device": "Spk"}},
    ])
    assert not plan.ok()          # 输入行被禁用 = 无输入 → 阻断
    assert plan.fx_chain == ()
    print("  禁用节点不参与计划  OK")


def test_unknown_type_warning():
    plan = SessionPlan.from_chain([
        {"type": "audio_input", "enabled": True, "params": {"device": "Mic"}},
        {"type": "audio_output", "enabled": True, "params": {"device": "Spk"}},
        {"type": "totally_new_node", "enabled": True, "params": {}},
    ])
    assert plan.ok(), plan.problems
    assert any("未知节点类型" in w for w in plan.warnings)
    print("  未知类型忽略并告警（向前兼容）  OK")


def test_remote_mic():
    plan = SessionPlan.from_chain([
        {"type": "remote_mic", "enabled": True, "params": {"url": "wss://x"}},
        {"type": "audio_output", "enabled": True, "params": {"device": "Spk"}},
    ])
    assert plan.ok()
    assert plan.remote_url == "wss://x"
    assert plan.inputs == ()      # 远程源不进本地设备列表
    # url 为空 → 阻断
    plan2 = SessionPlan.from_chain([
        {"type": "remote_mic", "enabled": True, "params": {"url": "  "}},
        {"type": "audio_output", "enabled": True, "params": {"device": "Spk"}},
    ])
    assert not plan2.ok()
    assert any("地址为空" in p for p in plan2.problems)
    print("  remote_mic url 抽取/空地址阻断  OK")


def test_pure_media_session():
    plan = SessionPlan.from_chain([
        {"type": "desktop_audio", "enabled": True, "params": {}},
        {"type": "vu_meter", "enabled": True, "params": {}},
    ])
    assert plan.ok(), plan.problems
    assert plan.inputs == () and plan.outputs == ()
    assert any("仅媒体源发声" in w for w in plan.warnings)
    print("  纯媒体会话（媒体节点即输入源）合法  OK")


def test_multiple_outputs_ordered():
    plan = SessionPlan.from_chain([
        {"type": "audio_input", "enabled": True, "params": {"device": "Mic"}},
        {"type": "audio_output", "enabled": True, "params": {"device": "A"}},
        {"type": "audio_output", "enabled": True, "params": {"device": "B"}},
    ])
    assert plan.ok()
    assert plan.outputs == ("A", "B")
    print("  多输出按链序  OK")


if __name__ == "__main__":
    print("SessionPlan 测试:")
    test_valid_chain()
    test_no_input_blocked()
    test_no_output_blocked()
    test_empty_device_skipped_with_warning()
    test_disabled_nodes_skipped()
    test_unknown_type_warning()
    test_remote_mic()
    test_pure_media_session()
    test_multiple_outputs_ordered()
    print("全部通过")
