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
TSE 参考音频弹框（独立模块，避免膨胀 ui_pyside6）。

包含：
  - RecordButton         录音按钮（倒计时 + 进度动画）
  - TseReferenceDialog   弹框：录音（动画）+ 播放 + 文件信息

弹框内完成录音：未运行处理时自动启动临时会话采集参考，录完自动停。
打开方式（见 ui_pyside6）：
  1. TSE 插件启用且无参考音频时提示
  2. 节点行「参考音频…」按钮 → 弹框
"""

import os
import threading
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

# 录音/播放/参考音频处理（audio_processor 为音频引擎，可顶层导入）
from audio_processor import (
    load_tse_reference, _recorder, _samples_to_wav_bytes,
    RECORD_DURATION, CFG_REF_WAV_PATH, register_tse_audio_hook,
)

# ui_pyside6 只在运行期导入（其内部打开本模块是懒加载，避免循环依赖）
from ui_pyside6 import _state, start_processing, stop_processing


class RecordButton(QPushButton):
    """录音按钮：3 秒倒计时 → 录制进度动画。"""

    countdown_finished = Signal()

    def __init__(self, text="录音", parent=None):
        super().__init__(text, parent)
        self._recording = False
        self._progress = 0.0
        self._countdown = 0
        self._countdown_timer = None

    def paintEvent(self, event):
        if self._countdown > 0:
            from theme_colors import current_colors
            tc = current_colors()
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            w, h = self.width(), self.height()
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(tc.record_btn_bg))
            p.drawRoundedRect(0, 0, w, h, 6, 6)
            p.setPen(QColor(tc.record_btn_countdown_text))
            font = p.font()
            font.setPixelSize(h - 6)
            font.setBold(True)
            p.setFont(font)
            p.drawText(self.rect(), Qt.AlignCenter, str(self._countdown))
            p.end()
        elif self._recording:
            from theme_colors import current_colors
            tc = current_colors()
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            w, h = self.width(), self.height()
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(tc.record_btn_bg))
            p.drawRoundedRect(0, 0, w, h, 6, 6)
            p.setBrush(QColor(tc.record_btn_progress_fill))
            p.drawRoundedRect(0, 0, int(w * self._progress), h, 6, 6)
            p.setPen(Qt.white)
            p.drawText(self.rect(), Qt.AlignCenter, f"录音中 {int(self._progress * 100)}%")
            p.end()
        else:
            super().paintEvent(event)

    def start_countdown(self):
        self._countdown = 3
        self.setCursor(Qt.ArrowCursor)
        self.update()
        self._countdown_timer = QTimer()
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._tick_countdown)
        self._countdown_timer.start()

    def _tick_countdown(self):
        self._countdown -= 1
        self.update()
        if self._countdown <= 0:
            self._countdown_timer.stop()
            self._countdown_timer = None
            self.countdown_finished.emit()

    def cancel_countdown(self):
        if self._countdown_timer:
            self._countdown_timer.stop()
            self._countdown_timer = None
        self._countdown = 0
        self.setCursor(Qt.PointingHandCursor)
        self.update()

    def start_recording(self):
        self._countdown = 0
        self._recording = True
        self._progress = 0.0
        self.setCursor(Qt.ArrowCursor)
        self.update()

    def set_progress(self, progress):
        self._progress = max(0.0, min(1.0, progress))
        self.update()

    def stop_recording(self):
        self._recording = False
        self._progress = 0.0
        self.setCursor(Qt.PointingHandCursor)
        self.update()


class TseReferenceDialog(QDialog):
    """TSE 参考音频弹框：录音（动画） + 播放 + 文件信息。"""

    _record_done = Signal()   # 录音完成（工作线程 → 主线程收尾 UI）

    def __init__(self, config, logger, parent=None):
        super().__init__(parent)
        self._config = config
        self._log = logger
        self._record_timer = None
        self._record_start = 0.0
        self._recording = False
        self._playing = False
        self._play_stop_event = None
        self._auto_stop_after_record = False
        self._record_done.connect(self._on_record_done)

        self.setWindowTitle("TSE 参考音频")
        self.setModal(True)
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)

        hint = QLabel(
            "录制 10 秒参考语音（你的声音），TSE 据此提取目标说话人。\n"
            "保持安静环境，不要有背景噪声或他人的声音。")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._status = QLabel("")
        self._status.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._status)

        self._info = QLabel("")
        self._info.setWordWrap(True)
        self._info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self._info)

        row = QHBoxLayout()
        self._rec_btn = RecordButton("录音")
        self._rec_btn.setFixedHeight(26)
        self._rec_btn.setMinimumWidth(96)
        self._rec_btn.clicked.connect(self._on_record)
        self._rec_btn.countdown_finished.connect(self._start_actual_recording)
        self._rec_btn.setToolTip(
            "点击开始录制 10 秒参考语音。\n"
            "对着麦克风自然说话，不要有背景噪声和其他人声。")
        row.addWidget(self._rec_btn)

        self._play_btn = QPushButton("播放")
        self._play_btn.setFixedHeight(26)
        self._play_btn.setMinimumWidth(72)
        self._play_btn.clicked.connect(self._on_play)
        self._play_btn.setToolTip("播放已录制的 TSE 参考音频。")
        row.addWidget(self._play_btn)

        row.addStretch()
        btn_ok = QPushButton("关闭")
        btn_ok.setFixedHeight(26)
        btn_ok.clicked.connect(self.accept)
        row.addWidget(btn_ok)
        layout.addLayout(row)

        self.refresh()

    # ── 状态/文件信息 ──

    def has_reference(self) -> bool:
        wav = self._config.get(CFG_REF_WAV_PATH, "")
        return bool(wav and os.path.exists(wav))

    def refresh(self):
        wav = self._config.get(CFG_REF_WAV_PATH, "")
        if self.has_reference():
            self._status.setText("✓ 已录制参考音频")
            try:
                import wave as _wf
                with _wf.open(wav, 'rb') as wf:
                    dur = wf.getnframes() / max(1, wf.getframerate())
                size_kb = os.path.getsize(wav) / 1024
                rec_time = time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(wav)))
                self._info.setText(
                    f"文件: {os.path.basename(wav)}\n"
                    f"录制时间: {rec_time}\n"
                    f"时长: {dur:.1f}s · {size_kb:.0f} KB\n"
                    f"位置: {os.path.dirname(wav)}")
            except Exception:
                self._info.setText(f"文件: {wav}")
            self._play_btn.setEnabled(True)
        else:
            self._status.setText("尚未录制参考音频")
            self._info.setText("点击「录音」录制 10 秒参考语音。")
            self._play_btn.setEnabled(False)

    # ── 录音 ──

    def _on_record(self):
        if self._recording:
            return
        self._auto_stop_after_record = False

        # 未运行：自动启动临时会话采集参考音频，录完自动停
        if not _state.is_processing:
            try:
                start_processing(_state, self._log)
            except Exception as e:
                self._log.err(f"自动启动失败: {e}")
            if not _state.is_processing:
                self._log.err("录音需要音频处理可用（请检查节点链中的输入/输出）")
                return
            self._auto_stop_after_record = True

        th = _state.processing_thread
        if not th:
            return

        th.set_recording_hook(lambda s: _recorder.feed(list(s)))
        self._recording = True
        # 重新连接倒计时信号（_start_actual_recording 里会断开，保证可重复录制）
        try:
            self._rec_btn.countdown_finished.connect(self._start_actual_recording)
        except Exception:
            pass
        self._rec_btn.start_countdown()

    def _start_actual_recording(self):
        try:
            self._rec_btn.countdown_finished.disconnect(self._start_actual_recording)
        except Exception:
            pass
        _recorder.start()
        self._rec_btn.start_recording()
        self._record_start = time.time()
        self._record_timer = QTimer()
        self._record_timer.timeout.connect(self._update_progress)
        self._record_timer.start(50)
        threading.Thread(target=lambda: self._do_record(_state.processing_thread),
                         daemon=True).start()

    def _update_progress(self):
        elapsed = time.time() - self._record_start
        self._rec_btn.set_progress(min(1.0, elapsed / RECORD_DURATION))
        if elapsed >= RECORD_DURATION:
            if self._record_timer:
                self._record_timer.stop()

    def _do_record(self, th):
        raw = _recorder.wait_and_get()
        if raw:
            from user_paths import WAV_PATH, ensure_dirs
            ensure_dirs()
            wav = WAV_PATH
            wav_data = _samples_to_wav_bytes(raw)
            with open(wav, 'wb') as f:
                f.write(wav_data)
            self._config.set(CFG_REF_WAV_PATH, wav)
            self._config.save_config()
            if th:
                try:
                    load_tse_reference(th.processor, wav)
                    self._log.tse("参考音频已保存并加载")
                except Exception as e:
                    self._log.err(f"参考音频加载失败: {e}")
            # 参考已加载：重新挂 TSE 音频钩子，链中启用的 tse 插件立即生效
            if th:
                try:
                    register_tse_audio_hook(th, self._log)
                except Exception:
                    pass
        else:
            self._log.tse("录制失败")
        if th:
            try:
                th.set_recording_hook(None)
            except Exception:
                pass
        self._recording = False
        self._record_done.emit()   # 主线程收尾 UI（重置按钮动画 + 刷新信息）

    def _on_record_done(self):
        """录音完成（主线程）：重置按钮动画、停进度定时器、刷新文件信息。"""
        self._rec_btn.stop_recording()
        if self._record_timer:
            self._record_timer.stop()
            self._record_timer = None
        if self._auto_stop_after_record:
            self._stop_temp_and_refresh()
        else:
            self.refresh()

    def _stop_temp_and_refresh(self):
        try:
            if _state.is_processing:
                stop_processing(_state, self._log)
        except Exception as e:
            self._log.err(f"停止临时会话失败: {e}")
        self.refresh()

    # ── 播放 ──

    def _on_play(self):
        if self._playing:
            if self._play_stop_event:
                self._play_stop_event.set()
            return
        wav_path = self._config.get(CFG_REF_WAV_PATH, "")
        if not wav_path or not os.path.exists(wav_path):
            return
        self._playing = True
        self._play_stop_event = threading.Event()
        self._play_btn.setText("停止")
        self._log.tse("播放参考音频")
        threading.Thread(target=self._play_thread, args=(wav_path,), daemon=True).start()

    def _play_thread(self, wav_path):
        import wave as _wf
        try:
            import pyaudio
            pa = pyaudio.PyAudio()
            with _wf.open(wav_path, 'rb') as wf:
                stream = pa.open(
                    format=pa.get_format_from_width(wf.getsampwidth()),
                    channels=wf.getnchannels(),
                    rate=wf.getframerate(),
                    output=True)
                chunk = 4800
                total = wf.getnframes()
                pos = 0
                while pos < total:
                    if self._play_stop_event.is_set():
                        break
                    buf = wf.readframes(min(chunk, total - pos))
                    if not buf:
                        break
                    stream.write(buf)
                    pos += chunk
                stream.stop_stream()
                stream.close()
            pa.terminate()
            self._log.tse("播放完成" if not self._play_stop_event.is_set() else "播放已停止")
        except Exception as e:
            self._log.err(f"播放失败: {e}")
        finally:
            self._playing = False
            self._play_stop_event = None
            self._play_btn.setText("播放")
