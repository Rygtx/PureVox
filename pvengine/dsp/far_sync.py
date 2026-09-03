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

"""FarSync——AEC far 端采集时钟对齐器（采集侧时钟域翻译器，与 PlaybackSink 对偶）。

问题：mic 与 far 是两条独立设备时钟（不同录制流/不同设备，实测 ±2% 漂移
+ 调度抖动）。旧逻辑每 mic hop 做一次 `speaker_capture.read(far_need)` 刚性
FIFO 消费：far 稍慢 → 频繁取空 → 整帧填零（参考信号断档，AEC 失配/抽动）；
far 稍快 → 环满静默丢最旧（参考信号跳变）。零散填零即"断档"。

原理（与 PlaybackSink 同构，方向相反）：
- **mic hop 是主时钟**。生产者（far 设备回调→环）按 far 时钟 push，
  消费者（处理线程每 mic hop）按 mic 节奏 pull(far_need)；
  速率差与抖动由本组件消化。
- **变速（ASRC）而非丢弃/填零**：按水位伺服微调消费步长（线性插值变率
  重采样 ±3%）。PI 环——积分项慢速收敛到真实速率差，比例项小系数阻尼；
  稳态速率差被平滑消化，延迟恒定。
- **欠载 = 保持 conceal 而非填零**：数据不够时延续上一样本值并向零快速
  衰减（短缺口平滑过渡），不断参考流的连续性；统计 conceal 样本数供诊断。
- **过载封顶**：极端漂移丢最旧，防延迟爬升（仅兜瞬态，稳态靠伺服）。

选型说明（AGENTS.md 先扩展再新建）：PlaybackSink 是播放方向（生产 bursty、
消费按设备时钟 pull），Resampler 是无状态变率原语；采集对齐需要"水位伺服
+ 欠载 conceal + 常满帧 pull" 的有状态闭环，两者都无法直接扩展，故单列此文件；
时钟域翻译仍是"每方向唯一实现点"（播放=PlaybackSink，采集=AEC far=FarSync），
禁止在 audio_processor/回调里另写对齐逻辑。

push() 由引擎线程调（每次先把 far 环里全部可用样本搬入），pull() 同线程
每 mic hop 调一次，恒返回恰好 n 个样本。
"""

import threading
from collections import deque
from typing import List


class FarSync:
    """far 设备时钟 → mic hop 时钟的自适应对齐缓冲。

    参数均为 far 域样本数（48kHz 下 1 hop = 480 = 10ms）：
    - hop：每 mic hop 消费的标称 far 样本数（far_need，随 dev_sr 派生）
    - prime：预热水位（攒够前 pull 返回静音，避免启动毛刺）
    - target：伺服目标水位（稳态延迟 ≈ target/48000 秒）
    - cap：封顶水位（超出丢最旧，防延迟爬升）
    """

    def __init__(self, hop: int = 480, prime: int = None, target: int = None,
                 cap: int = None):
        self._hop = max(1, int(hop))
        self._prime = prime if prime is not None else self._hop * 4
        self._target = target if target is not None else self._hop * 8
        self._cap = cap if cap is not None else self._hop * 32
        self._lock = threading.Lock()
        self._buf: deque = deque()
        self._pos = 0.0          # 下一个输出样本在 buf[0..1] 间的小数位
        self._primed = False
        self._integ = 0.0        # 积分项（真实速率差，稳态恒定）
        self._last = 0.0         # 上一输出样本（欠载 conceal 用）
        # 诊断计数
        self.n_conceals = 0      # 欠载保持样本数
        self.n_drops = 0         # 封顶丢最旧样本数
        self.n_underruns = 0     # 欠载事件数

    # ── 生产侧（far 设备域）──

    def push(self, frame) -> None:
        """推入 far 设备新到样本（任意长度，常态 = 本 hop 到达量）。"""
        if not frame:
            return
        with self._lock:
            self._buf.extend(frame)
            level = len(self._buf) - int(self._pos)
            if level > self._cap:
                drop = int(level - self._cap)
                for _ in range(drop):
                    self._buf.popleft()
                self._pos = max(0.0, self._pos - drop)
                self.n_drops += drop

    def reset(self) -> None:
        """清空并回到预热态（AEC 启停/sink 切换时调）。"""
        with self._lock:
            self._buf.clear()
            self._pos = 0.0
            self._primed = False
            self._integ = 0.0
            self._last = 0.0

    def set_hop(self, hop: int) -> None:
        """far 设备采样率变化时更新标称 hop（附带 reset）。"""
        hop = max(1, int(hop))
        with self._lock:
            self._hop = hop
            self._prime = hop * 4
            self._target = hop * 8
            self._cap = hop * 32
            self._buf.clear()
            self._pos = 0.0
            self._primed = False
            self._integ = 0.0
            self._last = 0.0

    # ── 消费侧（mic hop 域，主时钟）──

    def pull(self, n: int) -> List[float]:
        """按 mic 节奏取 n 个 far 样本；恒返回恰好 n 个，永不断档。

        水位伺服微调消费步长（±3%）；样本不足时保持上一值向零衰减
        （conceal），不整帧填零。
        """
        out = [0.0] * max(0, int(n))
        n = len(out)
        if n == 0:
            return out
        with self._lock:
            if not self._primed:
                if len(self._buf) - int(self._pos) < self._prime:
                    return out          # 预热期静音（有界，仅启动一次）
                self._primed = True

            level = max(0, len(self._buf) - int(self._pos))
            err = (level - self._target) / float(self._target)
            integ = self._integ + max(-0.3, min(0.3, err)) * 0.002
            self._integ = max(-0.03, min(0.03, integ))
            r = 1.0 + self._integ + max(-0.1, min(0.1, err)) * 0.01
            if level < self._hop:
                r += 0.01               # 饥饿边缘安全阀（温和加速）
            r = max(0.97, min(1.03, r))

            buf = self._buf
            decay = 0.0
            i = 0
            while i < n:
                if len(buf) >= 2:
                    b0 = buf[0]
                    t = self._pos
                    v = b0 + (buf[1] - b0) * t
                    out[i] = v
                    self._last = v
                    decay = 0.0
                    self._pos += r
                    while self._pos >= 1.0 and len(buf) > 1:
                        buf.popleft()
                        self._pos -= 1.0
                    i += 1
                else:
                    # 欠载：保持上一值向零衰减（短缺口平滑过渡，不断流）
                    self.n_underruns += 1
                    if decay == 0.0:
                        decay = self._last / 32.0 if self._last else 0.0
                    while i < n:
                        self._last -= decay
                        if (decay > 0 and self._last < 0) or \
                           (decay < 0 and self._last > 0):
                            self._last = 0.0
                            decay = 0.0
                        out[i] = self._last
                        self.n_conceals += 1
                        i += 1
                    buf.clear()
                    self._pos = 0.0
                    break

            # 消费后再查封顶（写侧与读侧任一侧增长都受控）
            level = max(0, len(buf) - int(self._pos))
            if level > self._cap:
                drop = int(level - self._cap)
                for _ in range(drop):
                    buf.popleft()
                self._pos = max(0.0, self._pos - drop)
                self.n_drops += drop
        return out

    # ── 观测 ──

    def level(self) -> int:
        with self._lock:
            return max(0, len(self._buf) - int(self._pos))

    def rate(self) -> float:
        """当前消费步长（诊断用；稳态 ≈1.0）。"""
        with self._lock:
            return 1.0 + self._integ

    def diag(self) -> dict:
        with self._lock:
            return {"level": max(0, len(self._buf) - int(self._pos)),
                    "conceals": self.n_conceals, "drops": self.n_drops,
                    "underruns": self.n_underruns, "rate": 1.0 + self._integ}


class FarTap:
    """far 设备域 → 48k hop 域的完整抽头：FarSync 对齐 + 48k 重采样。

    AEC 行的 far 参考与 loopback 输入行共用此件：push() 喂设备域
    新到样本，pull() 恒返回恰好 480 个 48kHz 样本（永不断档）。
    far 已是 48k 时重采样器为空，直通零开销。
    """

    def __init__(self, far_sample_rate: int, hop_length: int = 480):
        import numpy as np
        self._np = np
        self.far_sample_rate = int(far_sample_rate or 48000)
        self._hop = max(1, int(hop_length))
        far_hop = max(1, int(self._hop * self.far_sample_rate / 48000))
        self.sync = FarSync(hop=far_hop)
        if self.far_sample_rate != 48000:
            from pvengine.dsp.resampler import Resampler
            self._resampler: object = Resampler()
            self._resampler.process(
                np.zeros(self._hop, dtype=np.float32),
                48000.0 / float(self.far_sample_rate))
        else:
            self._resampler = None

    def push(self, samples) -> None:
        self.sync.push(samples)

    def pull(self):
        """拉一帧 48k far 样本（恒 hop_length 个，numpy float32）。"""
        np = self._np
        far_need = max(1, int(self._hop * self.far_sample_rate / 48000))
        pulled = self.sync.pull(far_need)
        if self._resampler is not None:
            got = self._resampler.process(
                pulled, 48000.0 / float(self.far_sample_rate))
            return np.asarray(got[:self._hop], dtype=np.float32)
        return np.asarray(pulled[:self._hop], dtype=np.float32)

    def diag(self) -> dict:
        return self.sync.diag()
