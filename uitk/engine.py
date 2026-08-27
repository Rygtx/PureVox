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

"""uitk 引擎控制器：链文档 → 会话计划 → 音频流启停（对照 ui_pyside6 主路径精简）。

UI 只调 start(chain_cfg)/stop()，返回错误文案；不碰 Qt。
"""

import sys
from typing import Optional


def enum_io_devices():
    """枚举输入/输出设备 → {"inputs": [(显示,值)], "outputs": [(显示,值)],
    "voutputs": VB-CABLE 候选（模糊匹配 cable，排除 16ch 变体）}。"""
    def _vb_filter(names):
        out = []
        for n in names:
            low = n.lower()
            if "cable" not in low:
                continue
            if "16" in low:
                continue   # 排除 16ch 变体
            out.append(n)
        return out

    def _vb_of(pairs):
        return [(t, v) for t, v in pairs if _vb_filter([v])]
    if sys.platform.startswith("linux"):
        from pvplatform.audio.pwpipe_client import (
            list_sources, list_destinations, source_label, dest_label)
        src = [(source_label(p), p) for p in list_sources()]
        dst = [(dest_label(p), p) for p in list_destinations()]
        return {"inputs": src, "outputs": dst,
                "vinputs": _vb_of(src), "voutputs": _vb_of(dst)}
    from audio_processor import get_device_names, default_api_type
    inp, out = get_device_names(api_type=default_api_type())
    vb_in = [(n, n) for n in _vb_filter(inp)]
    vb_out = [(n, n) for n in _vb_filter(out)]
    if not vb_out:
        # 兜底：常见端点名（未插全时 pyaudio 枚举可能缺失）
        vb_out = [("CABLE Input (VB-Audio Virtual Cable)",
                   "CABLE Input (VB-Audio Virtual Cable)")]
    return {"inputs": [(n, n) for n in inp],
            "outputs": [(n, n) for n in out],
            "vinputs": vb_in, "voutputs": vb_out}


def _chain_enabled(chain, ptype):
    return any(e.get("type") == ptype and e.get("enabled", True)
               for e in chain)


class EngineController:
    """音频引擎启停封装。start 返回 None=成功，否则错误文案。"""

    def __init__(self, log, config=None):
        self.log = log
        self.config = config
        self.processor = None
        self.thread = None
        self.running = False

    def start(self, chain_cfg) -> Optional[str]:
        if self.running:
            return None
        self.stop()
        log = self.log
        try:
            from session_plan import SessionPlan
            plan = SessionPlan.from_chain(chain_cfg)
            for w in plan.warnings:
                log.warn(f"[节点] {w}")
            if not plan.ok():
                return "；".join(plan.problems)

            from pvplatform.audio.backends import select_backend
            required = set()
            if len(plan.inputs) > 1:
                required.add("multi_input")
            if len(plan.outputs) > 1:
                required.add("multi_output")
            if _chain_enabled(chain_cfg, "echo_cancel"):
                required.add("loopback_far")
            backend = select_backend(frozenset(required))
            if backend is None:
                return "当前平台没有可用的音频传输后端"
            log.msg(f"[后端] {backend.label} ({backend.name})")
            use_pw = backend.name == "pipewire"

            err = self._check_48k(chain_cfg, plan, use_pw) if not use_pw else None
            if err:
                return err

            from audio_processor import create_audio_processor, \
                start_audio_stream, HOP_LENGTH, get_device_id, default_api_type
            proc = create_audio_processor()
            self.processor = proc
            if getattr(proc, "plugin_errors", None):
                for perr in proc.plugin_errors:
                    log.warn(f"[插件] {perr}")
            proc.set_plugins([dict(e) for e in chain_cfg])

            pw_ports = ([], [])
            inp = out = None
            extra_out = []
            if use_pw:
                pw_ports = (list(plan.inputs), list(plan.outputs))
                log.msg(f"[启动] PipeWire 输入x{len(pw_ports[0])} "
                        f"输出x{len(pw_ports[1])}")
            else:
                api_type = default_api_type()
                inp = get_device_id(plan.inputs[0], True, api_type=api_type) \
                    if plan.inputs else None
                out = get_device_id(plan.outputs[0], False, api_type=api_type) \
                    if plan.outputs else None
                # 多路输出扇出：首输出走主流，其余设备经 extra 输出流写入
                extra_out = [get_device_id(n, False, api_type=api_type)
                             for n in plan.outputs[1:]]
                extra_out = [e for e in extra_out if e is not None]
                if extra_out:
                    log.msg(f"[启动] 多路输出 +{len(extra_out)}")

            active = [e.get("type") for e in chain_cfg if e.get("enabled", True)]
            ready_msg = "+".join(active) if active else "空链"
            self.thread = start_audio_stream(
                inp, out, proc, HOP_LENGTH,
                api_type=default_api_type(), ready_msg=ready_msg,
                extra_output_ids=extra_out, pw_ports=pw_ports)
            if self.thread and not self.thread.wait_ready(timeout=3.0):
                err = getattr(self.thread, "_start_error", None) or "音频流创建超时"
                self.stop()
                return f"音频流创建失败: {err}"

            # AEC far 端（链启用 echo_cancel 时）
            if _chain_enabled(chain_cfg, "echo_cancel") and self.thread:
                far = ""
                for e in chain_cfg:
                    if e.get("type") == "echo_cancel" and e.get("enabled", True):
                        far = (e.get("params") or {}).get("far_device", "")
                        break
                self.thread.set_aec_far_sink(far)
                self.thread.processor.set_aec_enabled(True)
                self.thread.set_aec_enabled(True)

            self.running = True

            # TSE：挂录音钩子 + 加载已保存参考（无参考时插件直通）
            try:
                from audio_processor import register_tse_audio_hook, \
                    load_tse_reference, CFG_REF_WAV_PATH
                register_tse_audio_hook(self.thread, log.msg)
                if _chain_enabled(chain_cfg, "tse"):
                    wav = (self.config.get(CFG_REF_WAV_PATH, "")
                           if self.config else "")
                    if wav:
                        load_tse_reference(proc, wav)
            except Exception as e:
                log.warn(f"[TSE] 初始化失败: {e}")
            return None
        except Exception as e:
            import traceback
            log.err(f"启动失败: {e}\n{traceback.format_exc()}")
            self.stop()
            return str(e)

    def _check_48k(self, chain_cfg, plan, use_pw) -> Optional[str]:
        """Windows PortAudio：逐设备 48k 打开检测（WASAPI 严格/MME 宽松为既定行为）。"""
        if use_pw:
            return None
        try:
            import pyaudio
        except ImportError:
            return None
        from audio_processor import get_device_id, default_api_type
        api_type = default_api_type()
        failed = []
        p = pyaudio.PyAudio()
        try:
            checks = [(True, n) for n in plan.inputs] + \
                     [(False, n) for n in plan.outputs]
            for is_in, name in checks:
                dev = get_device_id(name, is_in, api_type=api_type)
                if dev is None:
                    continue
                try:
                    if is_in:
                        s = p.open(format=pyaudio.paFloat32, channels=1,
                                   rate=48000, input=True,
                                   input_device_index=dev, frames_per_buffer=1024)
                    else:
                        s = p.open(format=pyaudio.paFloat32, channels=1,
                                   rate=48000, output=True,
                                   output_device_index=dev, frames_per_buffer=1024)
                    s.close()
                except Exception as e:
                    failed.append(f"{name or '系统默认'} ({e})")
        finally:
            p.terminate()
        if failed:
            return "以下设备不支持 48kHz，已阻止启动: " + "、".join(failed)
        return None

    def set_live_param(self, index, key, value):
        """滑杆实时生效：直接更新运行中处理器的插件参数（不重建链）。"""
        if not (self.processor and self.running):
            return
        try:
            self.processor.update_plugin_param(index, key, value)
        except Exception as e:
            self.log.warn(f"[参数] 实时更新失败 ({key}): {e}")

    def stop(self):
        if self.thread:
            try:
                self.thread.stop()
            except Exception:
                pass
            self.thread = None
        if self.processor:
            try:
                self.processor.cleanup()
            except Exception:
                pass
            self.processor = None
        self.running = False
