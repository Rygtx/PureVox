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
日志模块 - 格式化对齐的日志输出

格式：
    [2026-05-31 19:30:45.123] [MSG ] 消息内容
    [2026-05-31 19:30:45.124] [ERR ] 错误信息
    [2026-05-31 19:30:45.125] [TSE ] TSE相关消息
    [2026-05-31 19:30:45.126] [AGC ] AGC相关消息

方括号宽度：3-4个字母，右对齐
时间戳：年-月-日 时:分:秒.毫秒，固定宽度
"""

import datetime
from typing import Callable, Optional


# 日志标签定义（3-4字母，右对齐）
TAG_MSG  = "MSG"
TAG_ERR  = "ERR"
TAG_WARN = "WARN"
TAG_INFO = "INFO"
TAG_TSE  = "TSE"
TAG_AGC  = "AGC"
TAG_EQ   = "EQ"
TAG_DEV  = "DEV"
TAG_SYS  = "SYS"
TAG_GAIN = "GAIN"
TAG_MODE = "MODE"


class Logger:
    """格式化日志器"""

    def __init__(self, callback: Optional[Callable[[str], None]] = None):
        self._callback = callback
        self._buffer = []
        self._flush_scheduled = False
        self._log_file = None
        self._init_file_log()

    def _init_file_log(self):
        """初始化文件日志输出。"""
        try:
            from user_paths import get_log_path
            log_path = get_log_path()
            self._log_file = open(log_path, 'a', encoding='utf-8')
        except Exception:
            self._log_file = None

    def set_callback(self, callback: Callable[[str], None]) -> None:
        self._callback = callback

    def _format_timestamp(self) -> str:
        """格式化时间戳：年-月-日 时:分:秒.毫秒"""
        now = datetime.datetime.now()
        return now.strftime('%Y-%m-%d %H:%M:%S') + f'.{now.microsecond // 1000:03d}'

    def _format_tag(self, tag: str) -> str:
        """格式化标签：固定4字符宽度，右对齐"""
        return f'{tag:>4s}'

    def _log(self, tag: str, message: str) -> None:
        """内部日志方法"""
        timestamp = self._format_timestamp()
        tag_str = self._format_tag(tag)
        line = f'[{timestamp}] [{tag_str}] {message}'

        # 输出到控制台
        print(line)

        # 写入文件
        if self._log_file:
            try:
                self._log_file.write(line + '\n')
                self._log_file.flush()
            except Exception:
                pass

        # 存入缓冲区
        self._buffer.append(line)

        # 触发回调
        if self._callback:
            self._callback(line)

    def msg(self, message: str) -> None:
        """普通消息"""
        self._log(TAG_MSG, message)

    def err(self, message: str) -> None:
        """错误消息"""
        self._log(TAG_ERR, message)

    def warn(self, message: str) -> None:
        """警告消息"""
        self._log(TAG_WARN, message)

    def info(self, message: str) -> None:
        """信息消息"""
        self._log(TAG_INFO, message)

    def tse(self, message: str) -> None:
        """TSE相关"""
        self._log(TAG_TSE, message)

    def agc(self, message: str) -> None:
        """AGC相关"""
        self._log(TAG_AGC, message)

    def eq(self, message: str) -> None:
        """EQ相关"""
        self._log(TAG_EQ, message)

    def dev(self, message: str) -> None:
        """设备相关"""
        self._log(TAG_DEV, message)

    def sys(self, message: str) -> None:
        """系统相关"""
        self._log(TAG_SYS, message)

    def gain(self, message: str) -> None:
        """增益相关"""
        self._log(TAG_GAIN, message)

    def mode(self, message: str) -> None:
        """模式相关"""
        self._log(TAG_MODE, message)

    def __call__(self, message: str) -> None:
        """兼容旧的 log() 调用方式"""
        # 自动识别标签
        if message.startswith('[TSE]'):
            self._log(TAG_TSE, message[5:].strip())
        elif message.startswith('[音频]'):
            self._log(TAG_MSG, message[4:].strip())
        elif message.startswith('[模型]'):
            self._log(TAG_INFO, message[4:].strip())
        elif message.startswith('[设备]'):
            self._log(TAG_DEV, message[4:].strip())
        elif message.startswith('[增益]'):
            self._log(TAG_GAIN, message[4:].strip())
        elif message.startswith('[降噪]'):
            self._log(TAG_MODE, message[4:].strip())
        elif message.startswith('[直通]'):
            self._log(TAG_MODE, message[4:].strip())
        elif message.startswith('[监听]'):
            self._log(TAG_DEV, message[4:].strip())
        elif message.startswith('[EQ]'):
            self._log(TAG_EQ, message[4:].strip())
        elif message.startswith('[系统]'):
            self._log(TAG_SYS, message[4:].strip())
        elif message.startswith('[AGC]'):
            self._log(TAG_AGC, message[5:].strip())
        else:
            self._log(TAG_MSG, message)


# 全局日志实例
_logger = Logger()


def get_logger() -> Logger:
    """获取全局日志实例"""
    return _logger


def log(message: str) -> None:
    """兼容旧的 log() 调用方式"""
    _logger(message)


# 便捷函数
def log_msg(message: str) -> None:
    _logger.msg(message)

def log_err(message: str) -> None:
    _logger.err(message)

def log_warn(message: str) -> None:
    _logger.warn(message)

def log_info(message: str) -> None:
    _logger.info(message)

def log_tse(message: str) -> None:
    _logger.tse(message)

def log_agc(message: str) -> None:
    _logger.agc(message)

def log_eq(message: str) -> None:
    _logger.eq(message)

def log_dev(message: str) -> None:
    _logger.dev(message)

def log_sys(message: str) -> None:
    _logger.sys(message)

def log_gain(message: str) -> None:
    _logger.gain(message)

def log_mode(message: str) -> None:
    _logger.mode(message)
