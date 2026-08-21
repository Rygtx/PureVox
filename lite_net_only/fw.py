# PureVox Lite Net Only — 防火墙状态检查（无管理员路径）
# Copyright (C) 2024-2026 a2heng <752848283@qq.com>
# SPDX-License-Identifier: GPL-3.0-or-later
#
# 程序化加规则（netsh/COM INetFwPolicy2/PowerShell）一律需要管理员令牌，
# 无提权依赖它没有意义。本模块只做只读检查：
#   规则不存在时，WSS 开始监听后 Windows 会自动弹「安全中心警报」，
#   用户点「允许访问」即由系统生成放行规则——这是唯一的免管理员申请途径。
#   这里负责检测该规则是否已出现（用户点了允许 / 手动放过）。

import os
import subprocess
import sys
import time

_RULE_TCP = "PureVox Net Lite WSS"
_RULE_MDNS = "PureVox Net Lite mDNS"


def _current_exe():
    return os.path.abspath(sys.executable)


def _script_path():
    # 冻结 exe：提权子进程是自身；dev：main.py
    if getattr(sys, "frozen", False):
        return ""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "main.py"))


def manual_install(port, local_ip=None, wait_timeout=60):
    """手动申请：弹 UAC 由提权子进程安装两条最小规则，轮询确认生效。
    （程序化加规则必须管理员令牌；这是确定性的手动路径，
    与「等系统安全中心警报」的自动路径互补）"""
    script = _script_path()
    params = "--fw-install %d %s" % (int(port), local_ip or "")
    if script:
        params = f'"{script}" {params}'
    try:
        import ctypes
        h = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
        if h <= 32:
            return False, "已取消提权"
    except Exception as e:
        return False, str(e)
    deadline = time.time() + wait_timeout
    while time.time() < deadline:
        if rules_present(port, local_ip):
            return True, ""
        time.sleep(0.8)
    return False, "规则未生效（UAC 未确认？）"


def _current_exe():
    return os.path.abspath(sys.executable)


def _run(args):
    try:
        r = subprocess.run(args, capture_output=True, timeout=15,
                           creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0)
        raw = r.stderr or r.stdout
        # netsh 输出编码随系统/代码页变化（实测有 UTF-8 与 GBK 两种），
        # 先试 UTF-8 再回退 GBK，否则规则明明存在却判定为不存在
        for enc in ("utf-8", "gbk"):
            try:
                return r.returncode == 0, raw.decode(enc).strip()
            except UnicodeDecodeError:
                continue
        return r.returncode == 0, raw.decode("utf-8", "replace").strip()
    except Exception:
        return False, ""


def _rule_ok(name, need_port, need_ip):
    """单条规则存在且端口/IP 匹配（读操作无需管理员）。
    注意：netsh show 输出不含程序字段，规则名即唯一标识
    （安装时删旧建新，名字归我们管）。"""
    ok, out = _run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"])
    low = out.lower()
    if not ok or ("no rules match" in low) or ("没有与指定的标准相匹配的规则" in low):
        return False
    if need_port not in out:
        return False
    if need_ip and need_ip not in out:
        return False
    return True


def rules_present(port, local_ip=None):
    """两条最小化入站规则均已生效？(TCP 端口/IP + mDNS 5353)"""
    try:
        return (_rule_ok(_RULE_TCP, str(int(port)), local_ip)
                and _rule_ok(_RULE_MDNS, "5353", None))
    except Exception:
        return False
