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

"""纯媒体播放会话（无设备输入的本地媒体：文件/音效板/桌面声音）。

唯一实现：miniaudio PlaybackDevice（自包含 C，WASAPI/Pulse 直开）拉模型——
播放库回调生成器向 media_read 拉混合帧，设备时钟即节拍；无泵线程、
无手搓环形缓冲、不触碰 AudioThread/主体传输层。

多路输出：首设备为主时钟，其回调把每帧混合结果副本投递到其余设备的
有界队列（满丢最旧，欠载补静音，深度 ~200ms），从设备各自回调消费。
输出设备按名称精确匹配 miniaudio 枚举；未选或未匹配 = 系统默认输出
（Linux 走 pipewire-pulse 时 sink name 即 pw-dump 的 node.name，
与 plan.outputs 同源）。格式恒 F32 立体声 48kHz（miniaudio 负责与
设备混音格式的转换）。
"""

import queue

import numpy as np

_TARGET_SR = 48000
_HOP = 480              # 混合粒度（10ms @48kHz，与 pvengine.context.HOP_LENGTH 一致）
_CHANNELS = 2            # 立体声交错输出
_BUFFER_MS = 60          # 设备周期：延迟/抗抖动平衡
_QUEUE_N = 20            # 扇出队列深度（≈200ms）


class MediaSession:
    """read_frame(n) → 单声道 float 帧；写全部输出设备（多路扇出）。"""

    def __init__(self, read_frame, out_names):
        self._read = read_frame
        self._names = [str(n) for n in (out_names or []) if n]
        self._devs = None          # Devices 上下文（设备 id 生命周期随它）
        self._devices = []         # [PlaybackDevice]
        self._queues = []          # 从设备扇出队列
        self._err = None

    @property
    def error(self):
        return self._err

    def start(self) -> bool:
        try:
            import miniaudio
            self._devs = miniaudio.Devices()
            plays = self._devs.get_playbacks()
            ids = [self._match(plays, n) for n in self._names]
            ids = [i for i in ids if i is not None]
            if not ids:
                ids = [None]       # 未选/未匹配输出 = 系统默认输出
            self._queues = [queue.Queue(maxsize=_QUEUE_N)
                            for _ in ids[1:]]
            for i, dev_id in enumerate(ids):
                gen = (self._master_gen() if i == 0
                       else self._slave_gen(self._queues[i - 1]))
                next(gen)          # 生成器须先启动再交给设备
                dev = miniaudio.PlaybackDevice(
                    output_format=miniaudio.SampleFormat.FLOAT32,
                    nchannels=_CHANNELS, sample_rate=_TARGET_SR,
                    buffersize_msec=_BUFFER_MS, device_id=dev_id)
                dev.start(gen)
                self._devices.append(dev)
            return True
        except Exception as e:
            self._err = f"媒体输出设备打开失败: {e}"
            self.stop()
            return False

    @staticmethod
    def _match(plays, name):
        want = name.strip().casefold()
        for d in plays:
            if str(d.get("name", "")).strip().casefold() == want:
                return d["id"]
        return None

    def _pull_stereo_bytes(self) -> bytes:
        """拉一帧混合结果（media_read → F32 mono）→ 立体声交错 bytes，
        同时向扇出队列投递副本（满丢最旧控延迟）。欠载补零帧。"""
        try:
            frame = self._read(_HOP)
        except Exception:
            frame = None
        x = np.asarray(frame, dtype=np.float32) \
            if frame is not None and len(frame) else None
        if x is None or len(x) < _HOP:
            x = np.zeros(_HOP, dtype=np.float32)
        data = np.repeat(x[:_HOP], _CHANNELS).tobytes()
        for q in self._queues:
            try:
                if q.full():
                    q.get_nowait()
                q.put_nowait(data)
            except Exception:
                pass
        return data

    def _master_gen(self):
        """主设备回调生成器：按需拉帧凑满请求数（产出必须精确）。"""
        fb = 4 * _CHANNELS
        buf = bytearray()
        need = yield b""
        while True:
            while len(buf) < need * fb:
                buf += self._pull_stereo_bytes()
            out = bytes(buf[:need * fb])
            del buf[:need * fb]
            need = yield out

    def _slave_gen(self, q: queue.Queue):
        """从设备回调生成器：消费扇出队列；欠载补静音。"""
        fb = 4 * _CHANNELS
        buf = bytearray()
        need = yield b""
        while True:
            while len(buf) < need * fb:
                try:
                    buf += q.get_nowait()
                except queue.Empty:
                    buf += bytes(_HOP * fb)
            out = bytes(buf[:need * fb])
            del buf[:need * fb]
            need = yield out

    def stop(self):
        for d in self._devices:
            try:
                d.close()
            except Exception:
                pass
        self._devices = []
        self._queues = []
        self._devs = None
