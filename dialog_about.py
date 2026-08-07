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
关于对话框
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextBrowser, QWidget, QTabWidget, QApplication
)
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QFont, QDesktopServices

try:
    from _build_version import BUILD_DATE
except ImportError:
    BUILD_DATE = "开发版"

APP_NAME = "PureVox"
APP_VERSION = BUILD_DATE
APP_DESC = "AI 麦克风降噪工具"
APP_AUTHOR = "a2heng"
URLS = {
    "GitHub": "https://a2heng.github.io/",
    "哔哩哔哩": "https://space.bilibili.com/10850943",
    "声音测试工具": "https://a2heng.github.io/mic-test.html",
}
LIBS = [
    {"name": "PySide6", "ver": "6.x", "license": "LGPL v3", "url": "https://wiki.qt.io/Qt_for_Python", "desc": "GUI 框架"},
    {"name": "NumPy", "ver": "1.x", "license": "BSD 3-Clause", "url": "https://numpy.org/", "desc": "科学计算"},
    {"name": "PyAudio", "ver": "0.2.x", "license": "MIT", "url": "https://people.csail.mit.edu/hubert/pyaudio/", "desc": "音频 I/O"},
    {"name": "ONNX Runtime", "ver": "1.24.x", "license": "MIT", "url": "https://onnxruntime.ai/", "desc": "模型推理"},
    {"name": "PySide6", "ver": "6.x", "license": "LGPL", "url": "https://www.qt.io/qt-for-python", "desc": "Qt GUI 框架"},
]


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"关于 {APP_NAME}")
        self.setMinimumSize(520, 560)
        self.setModal(True)
        self._build()
        # DWM 标题栏跟随当前主题
        try:
            from theme_colors import is_dark_current
            import ctypes
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            dark = is_dark_current()
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                int(self.winId()), DWMWA_USE_IMMERSIVE_DARK_MODE,
                ctypes.byref(ctypes.c_int(1 if dark else 0)),
                ctypes.sizeof(ctypes.c_int))
        except Exception:
            pass

    def _build(self):
        pal = QApplication.instance().palette()
        text_color = pal.text().color().name()
        link_color = pal.highlight().color().name()
        hint_color = pal.placeholderText().color().name()
        base_color = pal.base().color().name()
        border_color = pal.mid().color().name()
        # 表头背景：用 base 和 text 混合，避免 alternateBase 不可靠
        from PySide6.QtGui import QColor
        header_bg = QColor(pal.base().color())
        header_bg = header_bg.lighter(115).name()

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        # ── 标题 ──
        header = QLabel(f"""
            <div style="text-align: center; font-family: 'Microsoft YaHei', sans-serif;">
                <div style="font-size: 20pt; font-weight: bold; color: {link_color};">{APP_NAME}</div>
                <div style="font-size: 11pt; color: {hint_color}; margin-top: 4px;">{APP_DESC}</div>
                <div style="font-size: 10pt; color: {link_color}; font-weight: bold; margin-top: 8px;">版本: {APP_VERSION}</div>
                <div style="font-size: 9pt; color: {hint_color}; margin-top: 4px;">作者: {APP_AUTHOR}</div>
                <div style="font-size: 9pt; color: {hint_color}; margin-top: 4px;">Copyright (C) 2024-2026 a2heng · GPL-3.0-or-later</div>
            </div>
        """)
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)

        # ── 链接（一行） ──
        links_layout = QHBoxLayout()
        links_layout.setSpacing(4)
        links_layout.setContentsMargins(0, 0, 0, 0)

        for name, url in URLS.items():
            btn = QPushButton(f" {name}")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    border: none;
                    color: {link_color};
                    padding: 4px 8px;
                    font-size: 9pt;
                }}
                QPushButton:hover {{
                    background: rgba(25, 118, 210, 0.1);
                    border-radius: 4px;
                }}
            """)
            btn.clicked.connect(lambda _, u=url: QDesktopServices.openUrl(QUrl(u)))
            links_layout.addWidget(btn)

        links_layout.addStretch()
        layout.addLayout(links_layout)

        # ── 选项卡 ──
        tabs = QTabWidget()

        # 使用说明选项卡
        help_tab = QWidget()
        hl = QVBoxLayout(help_tab)
        hl.setContentsMargins(12, 12, 12, 12)
        help_text = QTextBrowser()
        help_text.setOpenExternalLinks(True)
        help_text.setStyleSheet("QTextBrowser { border: none; }")
        help_text.setHtml(f"""
        <div style="line-height: 1.7; color: {text_color}; font-family: 'Microsoft YaHei', sans-serif;">
            <h3 style="color: {link_color}; margin-top: 0;">音频链路</h3>
            <p>物理麦克风 → PureVox 处理 → VB-Cable Input → VB-Cable Output → 其他应用选作麦克风</p>

            <h3 style="color: {link_color};">四种模式</h3>
            <table style="width: 100%; border-collapse: collapse; font-size: 9pt;">
                <tr style="background: {header_bg};">
                    <th style="padding: 4px 6px; text-align: left; border-bottom: 1px solid {border_color};">模式</th>
                    <th style="padding: 4px 6px; text-align: left; border-bottom: 1px solid {border_color};">说明</th>
                    <th style="padding: 4px 6px; text-align: left; border-bottom: 1px solid {border_color};">AGC/VAD</th>
                </tr>
                <tr><td style="padding: 4px 6px; border-bottom: 1px solid {border_color};"><b>直通</b></td>
                    <td style="padding: 4px 6px; border-bottom: 1px solid {border_color};">原始信号，仅做增益 + EQ</td>
                    <td style="padding: 4px 6px; border-bottom: 1px solid {border_color};">✅</td></tr>
                <tr><td style="padding: 4px 6px; border-bottom: 1px solid {border_color};"><b>降噪</b></td>
                    <td style="padding: 4px 6px; border-bottom: 1px solid {border_color};">AI 模型实时去除背景噪声（默认）</td>
                    <td style="padding: 4px 6px; border-bottom: 1px solid {border_color};">✅</td></tr>
                <tr><td style="padding: 4px 6px; border-bottom: 1px solid {border_color};"><b>AEC</b></td>
                    <td style="padding: 4px 6px; border-bottom: 1px solid {border_color};">回声消除 + 降噪，消除扬声器回声</td>
                    <td style="padding: 4px 6px; border-bottom: 1px solid {border_color};">✅</td></tr>
                <tr><td style="padding: 4px 6px; border-bottom: 1px solid {border_color};"><b>TSE</b></td>
                    <td style="padding: 4px 6px; border-bottom: 1px solid {border_color};">目标说话人提取，只保留指定人的声音（需先录音参考音频）</td>
                    <td style="padding: 4px 6px; border-bottom: 1px solid {border_color};">✅</td></tr>
            </table>
            <p style="font-size: 8pt; color: {hint_color};">* TSE 模式下可使用「录音」按钮录制 10 秒参考语音。</p>

            <h3 style="color: {link_color}; margin-top: 12px;">处理链路</h3>
            <p style="font-size: 9pt; line-height: 1.9;">
                <b>统一链路</b>：前增益(AGC) → EQ → clip → [AEC] → 降噪 → [TSE] → clip → VAD → [AGC测量] → 输出<br>
            </p>
            <p style="font-size: 8pt; color: {hint_color};">
                * 方括号表示该步骤仅在对应模式下启用。<br>
                * AGC：自动增益控制，根据输出 RMS 自动调整前增益（所有模式可用）。<br>
                * VAD：静音时自动关闭输出，有声音时恢复（所有模式可用）。<br>
                * EQ：均衡器，支持 8 个预设插槽切换。
            </p>

            <h3 style="color: {link_color};">增益</h3>
            <ul>
                <li><b>前增益</b>：降噪前放大/衰减信号（-30 ~ +30 dB），AGC 开启时自动调节</li>
                <li><b>AGC</b>：自动增益控制，根据输出音量自动调整前增益</li>
                <li><b>VAD</b>：静音时自动关闭输出，有声音时恢复</li>
            </ul>

            <h3 style="color: {link_color};">设备</h3>
            <ul>
                <li><b>前增益</b>：降噪前放大/衰减信号（-30 ~ +30 dB）</li>
                <li><b>后增益</b>：降噪后再次调整音量（-30 ~ +30 dB）</li>
                <li><b>AGC</b>：自动增益控制，根据输入音量自动调整前增益（仅直通/降噪/AEC模式）</li>
                <li><b>VAD</b>：静音时自动关闭输出，有声音时恢复（仅直通/降噪/AEC模式）</li>
            </ul>

            <h3 style="color: {link_color};">设备</h3>
            <ul>
                <li><b>接口</b>：推荐 WASAPI（低延迟）</li>
                <li><b>输入</b>：选择物理麦克风</li>
                <li><b>输出</b>：选择 VB-Cable Input（虚拟设备）</li>
                <li><b>监听</b>：勾选后可选择扬声器实时听到处理后的声音</li>
            </ul>

            <h3 style="color: {link_color};">均衡器</h3>
            <p>31 段参数均衡器（20Hz ~ 20kHz），支持 7 组手动预设和内置预设。通过菜单「设置 → 均衡器」打开。</p>

            <h3 style="color: {link_color};">菜单</h3>
            <ul>
                <li><b>设置</b>：快捷键开关、启动时自动运行、开机自启</li>
                <li><b>VB设置</b>：打开 VB-CABLE 控制面板</li>
                <li><b>系统声音</b>：打开 Windows 声音控制面板</li>
            </ul>

            <h3 style="color: {link_color};">快捷键与托盘</h3>
            <ul>
                <li><b>右 Alt + &gt;</b>：全局快捷键启动/停止音频处理</li>
                <li>窗口关闭或启动时自动运行会最小化到系统托盘</li>
                <li>单击托盘图标显示/隐藏窗口，右键可退出</li>
            </ul>

            <h3 style="color: {link_color};">数据目录</h3>
            <p>配置、日志、录音文件均保存在 <code>~/.purevox/</code>（如 <code>C:\\Users\\用户名\\.purevox\\</code>）</p>

            <h3 style="color: {link_color};">注意事项</h3>
            <ul>
                <li>需安装 <b>VB-Cable</b> 虚拟声卡，首次启动会自动引导安装</li>
                <li>其他应用的麦克风应选择 <b>CABLE Output</b>（不是 CABLE Input）</li>
                <li>系统默认播放设备不要设为 VB-Cable，否则听不到系统声音</li>
                <li>增益建议从 0dB 开始调整，过大会出现削峰失真</li>
                <li>TSE 模式需先切换到「录音」录制一段参考语音，再切回 TSE 启动</li>
            </ul>
        </div>
        """)
        hl.addWidget(help_text)
        tabs.addTab(help_tab, "使用说明")

        # 许可证选项卡
        license_tab = QWidget()
        ll = QVBoxLayout(license_tab)
        ll.setContentsMargins(12, 12, 12, 12)

        license_text = QTextBrowser()
        license_text.setOpenExternalLinks(True)
        license_text.setStyleSheet("QTextBrowser { border: none; }")
        license_text.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        lib_rows = ''.join(
            f'<tr><td style="padding: 4px 6px; border-bottom: 1px solid {border_color};">'
            f'<a href="{l["url"]}" style="color: {link_color}; text-decoration: none;">{l["name"]}</a></td>'
            f'<td style="padding: 4px 6px; border-bottom: 1px solid {border_color};">{l["license"]}</td>'
            f'<td style="padding: 4px 6px; border-bottom: 1px solid {border_color}; color: {hint_color};">{l["desc"]}</td></tr>'
            for l in LIBS
        )
        license_text.setHtml(f"""
        <div style="line-height: 1.6; color: {text_color}; font-family: 'Microsoft YaHei', sans-serif;">
            <h3 style="color: {link_color}; margin-top: 0;">许可条款</h3>
            <p>本软件采用<b>GPL-3.0 开源许可证</b>：</p>
            <p><b>1) GNU General Public License v3.0 (GPL-3.0)</b> — 开源许可。<br>
            您有权自由使用、修改与分发，但修改版须保持 GPL-3.0 并公开源码。</p>
            <p><b>2) 内置 AI 模型</b> — 单独授权。<br>
            模型不随 GPL 授权，禁止用于其他项目，仅可在 PureVox 内经作者授权使用。</p>
            <p style="color: {hint_color};">
                <a href="https://www.gnu.org/licenses/gpl-3.0.html" style="color: {link_color};">GPL-3.0 全文</a>
            </p>
            <p style="color: {hint_color}; font-size: 9pt;">
                内置 AI 模型归作者 a2heng 所有，不随 GPL 开源版本授权，不得提取用于其他项目或再分发。
            </p>
            <hr>
            <h3 style="color: {link_color};">第三方库</h3>
            <table style="width: 100%; border-collapse: collapse;">
                <tr style="background: {header_bg};">
                    <th style="padding: 6px; text-align: left; border-bottom: 2px solid {border_color}; color: {text_color};">库</th>
                    <th style="padding: 6px; text-align: left; border-bottom: 2px solid {border_color}; color: {text_color};">许可证</th>
                    <th style="padding: 6px; text-align: left; border-bottom: 2px solid {border_color}; color: {text_color};">说明</th>
                </tr>
                {lib_rows}
            </table>
            <p style="color: {hint_color}; font-size: 9pt; margin-top: 12px;">
                <b>PySide6 (LGPL v3):</b> 本软件动态链接 PySide6。
                <a href="https://wiki.qt.io/Qt_for_Python" style="color: {link_color};">获取源码</a> |
                <a href="https://www.gnu.org/licenses/lgpl-3.0.html" style="color: {link_color};">LGPL v3 全文</a>
            </p>
        </div>
        """)
        ll.addWidget(license_text)
        tabs.addTab(license_tab, "许可证")

        # 关于 Qt 选项卡
        qt_tab = QWidget()
        ql = QVBoxLayout(qt_tab)
        ql.setContentsMargins(12, 12, 12, 12)

        from PySide6.QtCore import qVersion
        from PySide6 import __version__ as pyside_ver
        qt_info = QTextBrowser()
        qt_info.setOpenExternalLinks(True)
        qt_info.setStyleSheet("QTextBrowser { border: none; }")
        qt_info.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        qt_info.setHtml(f"""
            <div style="color: {text_color}; font-family: 'Microsoft YaHei', sans-serif;">
                <h3 style="color: {link_color}; margin-top: 0;">LGPL v3 许可声明</h3>
                <p>本软件使用 <b>PySide6</b> (Qt for Python) 构建图形界面。</p>
                <p><b>Qt 版本:</b> {qVersion()}</p>
                <p><b>PySide6 版本:</b> {pyside_ver}</p>
                <hr>
                <p>PySide6 依据 <b>GNU Lesser General Public License v3 (LGPL v3)</b> 发布。
                本软件通过动态链接方式使用 PySide6，满足 LGPL v3 要求。</p>
                <p><b>您有权：</b></p>
                <ul>
                    <li>在 LGPL v3 许可下使用、修改和分发本软件</li>
                    <li>获取 PySide6 的源代码</li>
                    <li>将 PySide6 替换为其他兼容 LGPL 的版本</li>
                </ul>
                <p><b>相关链接：</b></p>
                <ul>
                    <li><a href="https://www.gnu.org/licenses/lgpl-3.0.html" style="color: {link_color};">LGPL v3 全文</a></li>
                    <li><a href="https://wiki.qt.io/Qt_for_Python" style="color: {link_color};">PySide6 源码</a></li>
                    <li><a href="https://www.qt.io/" style="color: {link_color};">Qt 官网</a></li>
                </ul>
                <hr>
                <p style="color: {hint_color}; font-size: 9pt;">
                    Qt is a registered trademark of The Qt Company Ltd.<br>
                    本软件与 The Qt Company 无关联。
                </p>
            </div>
        """)
        ql.addWidget(qt_info)
        tabs.addTab(qt_tab, "关于 Qt")

        layout.addWidget(tabs, 1)

        # ── 关闭按钮 ──
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.setMinimumWidth(80)
        accent = QColor(link_color)
        accent_hover = accent.darker(115).name()
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: {link_color};
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 24px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background: {accent_hover};
            }}
        """)
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)


def show_about_dialog(parent=None):
    AboutDialog(parent).exec()
