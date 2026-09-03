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

"""FarSync 合成测试（无硬件，可 CI 运行）：python tests/test_far_sync.py

用合成时钟（非真实时间）验证采集对齐不变量——pull 恒满帧、永不断档：
1. 速率差 ±2% 长跑：无填零帧、延迟有界（水位不爬升/不见底）；
2. 到达抖动（每 hop 到达量波动）：输出连续（相邻样本跳变有界）；
3. far 断流：conceal 保持连续（非整帧静音跳变），恢复后水位回到目标附近；
4. far 突发：封顶丢最旧，延迟有界。
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pvengine.dsp.far_sync import FarSync

HOP = 480
SR = 48000
SINE_STEP = 2 * math.pi * 220 / SR
# 连续正弦相邻样本差上界（含伺服变速 ±3% 的拉伸）；conceal 衰减台阶远小于此
STEP_BOUND = SINE_STEP * 1.1 + 1e-6
PRIME_SILENCE_HOPS = 4  # prime 期静音帧数上界（hop*4 水位）


def run(far_rate, n_hops, jitter=0, stall_at=None, stall_hops=0,
        burst_at=None, seed_phase=0.0):
    """合成时钟推演。far_rate: 每 mic hop far 到达样本数（标称 480）。
    jitter: 到达量逐 hop 交替 ±jitter。返回 (frames, sync)。frames 为 pull 序列。"""
    sync = FarSync(hop=HOP)
    frames = []
    produced_phase = seed_phase
    for h in range(n_hops):
        stalled = stall_at is not None and stall_at <= h < stall_at + stall_hops
        burst = burst_at is not None and h == burst_at
        if not stalled:
            n = far_rate + (jitter if h % 2 == 0 else -jitter)
            if burst:
                n += HOP * 40  # 突发灌入 ~400ms（超过 cap=hop*32，必触顶）
            n = max(0, int(n))
            push = [math.sin(produced_phase + i * SINE_STEP) for i in range(n)]
            produced_phase += n * SINE_STEP
            sync.push(push)
        frames.append(sync.pull(HOP))
    return frames, sync


def max_jump(frames, skip_hops=PRIME_SILENCE_HOPS + 1):
    """拼接后相邻样本最大跳变（跳过启动 prime 静音段）。"""
    flat = [s for f in frames[skip_hops:] for s in f]
    return max(abs(b - a) for a, b in zip(flat, flat[1:]))


def check(name, cond, detail=""):
    print(("  PASS " if cond else "  FAIL ") + name + (" — " + detail if detail else ""))
    return cond


def main():
    ok = True
    print("FarSync 合成测试:")

    # 1. 标称速率：恒满帧 + 连续
    frames, sync = run(480, 300)
    ok &= check("标称 480/hop 恒满帧", all(len(f) == HOP for f in frames))
    ok &= check("标称连续", max_jump(frames) < STEP_BOUND, "jump=%.5f" % max_jump(frames))

    # 2. ±2% 长跑：不断档（除 prime 期外无静音帧）、水位有界、伺服收敛到真实差
    for rate in (470, 490):  # ≈∓2%
        frames, sync = run(rate, 600)
        tail = frames[PRIME_SILENCE_HOPS + 1:]
        silent = sum(1 for f in tail if all(v == 0.0 for v in f))
        d = sync.diag()
        ok &= check("far=%d 无填零帧" % rate, silent == 0, "silent=%d" % silent)
        ok &= check("far=%d 水位有界" % rate, d["level"] < HOP * 32, "lvl=%d" % d["level"])
        ok &= check("far=%d 伺服收敛" % rate, abs(d["rate"] - rate / 480.0) < 0.01,
                    "rate=%.4f" % d["rate"])
        ok &= check("far=%d 连续" % rate, max_jump(frames) < STEP_BOUND,
                    "jump=%.5f" % max_jump(frames))

    # 3. 到达抖动 ±96（20%）：不丢样本概念下连续
    frames, sync = run(480, 300, jitter=96)
    ok &= check("抖动连续", max_jump(frames) < STEP_BOUND, "jump=%.5f" % max_jump(frames))

    # 4. 断流 10 hops：不断档（恒满帧）、conceal 非跳变、恢复后水位回目标附近
    frames, sync = run(480, 300, stall_at=150, stall_hops=10)
    ok &= check("断流恒满帧", all(len(f) == HOP for f in frames))
    ok &= check("断流 conceal 连续", max_jump(frames) < 0.05,
                "jump=%.5f" % max_jump(frames))
    d = sync.diag()
    ok &= check("断流有 conceal 计数", d["conceals"] > 0, "conc=%d" % d["conceals"])
    ok &= check("恢复后水位有界", d["level"] < HOP * 32, "lvl=%d" % d["level"])

    # 5. 突发：封顶，延迟有界（触顶丢最旧本身是一次时间跳变，
    #    对标 test_playback_sink.test_burst_cap：不断言穿过丢弃点的连续，
    #    只断言恒满帧 + 水位有界 + 丢弃点前后各段连续）
    frames, sync = run(480, 300, burst_at=150)
    d = sync.diag()
    ok &= check("突发恒满帧", all(len(f) == HOP for f in frames))
    ok &= check("突发封顶有界", d["level"] < HOP * 32 and d["drops"] > 0,
                "lvl=%d drops=%d" % (d["level"], d["drops"]))
    ok &= check("突发前段连续", max_jump(frames[:140]) < STEP_BOUND,
                "jump=%.5f" % max_jump(frames[:140]))
    ok &= check("突发后段连续", max_jump(frames[200:]) < STEP_BOUND,
                "jump=%.5f" % max_jump(frames[200:]))

    print("ALL PASS" if ok else "FAILURES PRESENT")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
