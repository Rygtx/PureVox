"""
频谱直方图组件 — 128段 Mel 实时输入/输出频谱重叠对比
降噪输出为基准，多=灰(噪声已消除)，少=亮(增强)。
"""

import traceback
from typing import List, Optional

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QRectF, QTimer
from PySide6.QtGui import QPainter, QPen, QColor, QFont

import aimic

NUM_BANDS = aimic.SPECTRUM_NUM_BANDS
FFT_SIZE = 2048

# Spectrum bar colors are computed in paintEvent (theme-aware)

# 频率刻度 — 硬编码像素偏移（bar_w=3, gap=1, 从 L 起算）
# (px_offset_from_L, label)
TICK_POSITIONS = [
    (0, "20"),
    (12, "100"),
    (32, "200"),
    (76, "500"),
    (128, "1k"),
    (200, "2k"),
    (312, "5k"),
    (412, "10k"),
    (511, "20k"),
]


def _log_err(msg: str) -> None:
    """安全地将错误写入日志（不依赖 logger 模块以避免循环导入）"""
    try:
        from logger import get_logger
        get_logger().err(msg)
    except Exception:
        print(f"[SPECTRUM_ERR] {msg}")


def compute_spectrum_bands(samples: List[float]) -> List[float]:
    try:
        return list(aimic.compute_spectrum(samples))
    except Exception:
        _log_err(f"compute_spectrum C++ 调用失败: {traceback.format_exc()}")
        return [-90.0] * NUM_BANDS


class SpectrumWidget(QWidget):
    DB_MIN = -90.0
    DB_MAX = -20.0
    DB_RANGE = DB_MAX - DB_MIN
    MIN_SAMPLES = FFT_SIZE // 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self._input_bands: List[float] = [self.DB_MIN] * NUM_BANDS
        self._output_bands: List[float] = [self.DB_MIN] * NUM_BANDS
        self._smoothed_in: List[float] = [self.DB_MIN] * NUM_BANDS
        self._smoothed_out: List[float] = [self.DB_MIN] * NUM_BANDS
        self._input_accum: List[float] = []
        self._output_accum: List[float] = []
        self._pending_input: Optional[List[float]] = None  # 缓存输入帧，等待输出帧对齐
        self._pending_update = False
        self.setMinimumWidth(280)
        self.setMinimumHeight(60)

    def changeEvent(self, event):
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.PaletteChange:
            self.update()
        super().changeEvent(event)

    def update_spectrum(self, input_samples: Optional[List[float]],
                        output_samples: Optional[List[float]]):
        try:
            updated = False

            # 处理输入帧
            if input_samples:
                self._input_accum.extend(input_samples)
                if len(self._input_accum) > FFT_SIZE * 2:
                    self._input_accum = self._input_accum[-FFT_SIZE:]
                if len(self._input_accum) >= FFT_SIZE:
                    self._input_bands = compute_spectrum_bands(self._input_accum[-FFT_SIZE:])
                    self._input_accum = self._input_accum[-self.MIN_SAMPLES:]
                    updated = True

            # 处理输出帧
            if output_samples:
                self._output_accum.extend(output_samples)
                if len(self._output_accum) > FFT_SIZE * 2:
                    self._output_accum = self._output_accum[-FFT_SIZE:]
                if len(self._output_accum) >= FFT_SIZE:
                    self._output_bands = compute_spectrum_bands(self._output_accum[-FFT_SIZE:])
                    self._output_accum = self._output_accum[-self.MIN_SAMPLES:]
                    updated = True

            # 平滑处理并更新显示
            if updated:
                alpha = 0.3
                n_bands = min(NUM_BANDS, len(self._input_bands), len(self._output_bands),
                              len(self._smoothed_in), len(self._smoothed_out))
                for i in range(n_bands):
                    self._smoothed_in[i] += alpha * (self._input_bands[i] - self._smoothed_in[i])
                    self._smoothed_out[i] += alpha * (self._output_bands[i] - self._smoothed_out[i])
                if not self._pending_update:
                    self._pending_update = True
                    QTimer.singleShot(16, self._do_update)
        except Exception:
            _log_err(f"update_spectrum 异常: {traceback.format_exc()}")

    def _do_update(self):
        self._pending_update = False
        if self.isVisible():
            self.update()

    def paintEvent(self, event):
        try:
            w, h = self.width(), self.height()
            if w < 40 or h < 20:
                return

            pal = self.palette()
            bg = pal.base().color()
            from theme_colors import current_colors
            tc = current_colors()
            grid = QColor(tc.spec_grid)
            text_c = QColor(tc.spec_text_c)
            c_out = QColor(tc.spec_bar_out)
            c_more = QColor(tc.spec_bar_more)
            c_less = QColor(tc.spec_bar_less)

            p = QPainter(self)
            p.fillRect(0, 0, w, h, bg)

            L, R, T, B = 28, 12, 18, 16
            gw, gh = w - L - R, h - T - B
            if gw < 20 or gh < 10:
                p.end()
                return

            # dB刻度线（横向）
            p.setFont(QFont("Microsoft YaHei", 6))
            p.setPen(QPen(grid, 0.5))
            for db in (-90, -80, -70, -60, -50, -40, -30, -20):
                y = int(T + gh * (1.0 - (db - self.DB_MIN) / self.DB_RANGE))
                p.drawLine(L, y, L + gw, y)

            # 频率刻度线（按可用宽度等比缩放，兼容任意面板宽度）
            sx = gw / 512.0
            p.setFont(QFont("Microsoft YaHei", 5))
            p.setPen(QPen(grid, 0.5))
            for px_off, label in TICK_POSITIONS:
                x = L + px_off * sx
                p.drawLine(int(x), T, int(x), T + gh)
            p.setPen(text_c)
            for px_off, label in TICK_POSITIONS:
                x = L + px_off * sx
                if label == "20":
                    p.drawText(QRectF(x - 20, T + gh + 1, 20, 12), Qt.AlignRight | Qt.AlignVCenter, label)
                else:
                    p.drawText(QRectF(x - 12, T + gh + 1, 24, 12), Qt.AlignCenter, label)

            # 绘制频谱条 — 步长随可用宽度自适应（128 根铺满 gw）
            step = max(2.0, gw / NUM_BANDS)
            bar_w = max(1.0, step - 1.0)
            p.setPen(Qt.NoPen)

            for i in range(NUM_BANDS):
                in_db = max(self.DB_MIN, self._smoothed_in[i])
                out_db = max(self.DB_MIN, self._smoothed_out[i])
                in_h = (in_db - self.DB_MIN) / self.DB_RANGE * gh
                out_h = (out_db - self.DB_MIN) / self.DB_RANGE * gh

                if out_h < 1 and in_h < 1:
                    continue

                bx = L + i * step
                y_out = T + gh - out_h
                y_in = T + gh - in_h

                if out_h > 1:
                    p.setBrush(c_out)
                    p.drawRect(QRectF(bx, y_out, bar_w, out_h))

                if in_db > out_db and in_h > 1:
                    p.setBrush(c_more)
                    p.drawRect(QRectF(bx, y_in, bar_w, in_h - out_h))
                elif in_db < out_db and out_h > 1:
                    p.setBrush(c_less)
                    p.drawRect(QRectF(bx, y_out, bar_w, out_h - in_h))

            p.end()
        except Exception:
            _log_err(f"paintEvent 异常: {traceback.format_exc()}")
