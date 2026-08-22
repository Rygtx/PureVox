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

"""全组件压测：链上加满注册表中每一个节点类型，offscreen 渲染 + 计划校验。

用法：
    python tools/smoke_all_components.py            # offscreen 冒烟
    python tools/smoke_all_components.py --real     # 追加真实启动（弹窗口 10 秒）

通过标准：零异常、行数=节点数、SessionPlan 校验通过、配置往返一致。
"""

import os
import sys
import traceback


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    os.chdir(root)

    failures = []

    def check(name, fn):
        try:
            fn()
            print(f"[OK] {name}")
        except Exception:
            failures.append(name)
            print(f"[FAIL] {name}\n{traceback.format_exc()}")

    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    app = QApplication(sys.argv)

    import ui_pyside6 as ui
    from session_plan import SessionPlan
    from pvengine.plugins import all_specs

    # ── 构造满链：每个注册类型一条；audio_output 双份验证扇出结构 ──
    specs = all_specs()
    print(f"注册节点数: {len(specs)}")

    def dev(kind_key):
        try:
            names = ui.get_device_names(api_type=ui.default_api_type())
            lst = names[0] if kind_key == "in" else names[1]
            return lst[0] if lst else ""
        except Exception:
            return ""

    in_dev, out_dev = dev("in"), dev("out")
    print(f"设备: in={in_dev!r} out={out_dev!r}")

    chain = []
    for sp in specs:
        entry = {"type": sp.name, "enabled": True,
                 "params": {k: v[3] for k, v in sp.params.items()}}
        if sp.kind == "input":
            entry["params"] = {"device": "" if sp.name == "remote_mic"
                               else in_dev}
            if sp.name == "remote_mic":
                entry["enabled"] = False          # 无外部对端，仅验证渲染
                entry["params"] = {"url": "https://192.0.2.1:59123"}
        elif sp.kind == "output":
            entry["params"] = {"device": out_dev}
        chain.append(entry)
    # 第二路输出（扇出）
    chain.append({"type": "audio_output", "enabled": True,
                  "params": {"device": out_dev}})
    print(f"压测链条目数: {len(chain)}")

    # ── 计划校验（不依赖 UI）──
    plan = SessionPlan.from_chain(chain)

    def t_plan():
        assert not plan.problems or (
            not in_dev and "音频输入" in "".join(plan.problems)), plan.problems
        if in_dev and out_dev:
            assert plan.ok(), plan.problems
            assert len(plan.outputs) == 2, plan.outputs
            assert len(plan.fx_chain) >= len(specs) - 5, len(plan.fx_chain)
    check("session_plan", t_plan)

    # ── 真实窗口构建 + 满链渲染 ──
    import tempfile
    cfg_path = os.path.join(tempfile.gettempdir(), "purevox_stress_cfg.json")
    if os.path.exists(cfg_path):
        os.remove(cfg_path)
    config = ui.ConfigManager(cfg_path)
    config.load_config()
    config.set("plugin_chain", chain)
    logger = ui.Logger()

    window = ui.MainWindow(config, logger)
    app_main = ui.MainApp()
    app_main._setup(window, root, config)
    app_main._create_ui(window, config, None, logger)

    fxp = ui._state.fx_panel

    def t_render():
        assert len(fxp._rows) == len(chain), \
            f"rows={len(fxp._rows)} expect={len(chain)}"
        kinds = {r.plugin_type: r.kind for r in fxp._rows}
        assert kinds["audio_input"] == "input"
        assert kinds["denoiser"] == "fx"
        assert kinds["vu_meter"] == "viz"
        assert fxp.vu_widget() is not None
        assert fxp.spectrum_widget() is not None
    check("render_all_nodes", t_render)

    window.show()
    for _ in range(20):                      # 2 秒事件循环：触发全部 paint/布局
        app.processEvents()
        QTimer.singleShot(0, app.processEvents)
        import time
        time.sleep(0.1)
    check("event_loop_2s", lambda: None)

    def t_roundtrip():
        out = fxp.to_config()
        assert len(out) == len(chain), (len(out), len(chain))
        assert [e["type"] for e in out] == [e["type"] for e in chain]
        plan2 = SessionPlan.from_chain(out)
        assert plan2.ok() == plan.ok()
    check("config_roundtrip", t_roundtrip)

    # ── 可选真实启动 ──
    if "--real" in sys.argv:
        def t_real_start():
            assert in_dev and out_dev, "需要真实输入/输出设备"
            # 模态弹框在无人值守测试中会永久阻塞：替换为记录式桩
            ui._warn_48k = lambda *a, **k: log_lines.append("[48k] 设备被弹框拦截")
            config.set("plugin_chain", fxp.to_config())
            config.save_config()
            ui._state.config = config
            log_lines = []
            ui.start_processing(ui._state,
                                type("L", (), {"msg": lambda s, m: log_lines.append(m),
                                               "warn": lambda s, m: log_lines.append(m),
                                               "err": lambda s, m: log_lines.append(m),
                                               "dev": lambda s, m: None})())
            assert ui._state.is_processing, "\n".join(log_lines[-15:])
            import time
            time.sleep(2)
            ui.stop_processing(ui._state, logger)
            print("  启动日志尾部:", log_lines[-3:])
        check("real_start_full_chain", t_real_start)

    print("=" * 40)
    if failures:
        print(f"FAILED: {failures}")
        return 1
    print("ALL COMPONENTS STRESS OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
