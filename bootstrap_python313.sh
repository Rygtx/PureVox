#!/bin/sh
# PureVox — 内嵌 Python 3.13 引导脚本（Linux）
# 从 git 子模块 packages/cpython（CPython@v3.13.7）一次性编译出自包含的
# packages/python313/（独立于系统 Python）。幂等：已生成则跳过构建，
# 仅补齐依赖。编译在 .py313-src/build 下进行，不污染子模块工作区。
#
# 用法: ./bootstrap_python313.sh
set -e

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
SUBMOD="$APP_DIR/packages/cpython"
PREFIX="$APP_DIR/packages/python313"
BUILD_DIR="$APP_DIR/.py313-src/build"
PY_MAJOR=3.13
PY_BIN="$PREFIX/bin/python$PY_MAJOR"

# 内嵌解释器自带 libpython3.13.so，需让子进程找到它（独立于系统环境）
export LD_LIBRARY_PATH="$PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

# 1. 初始化子模块（若仓库尚未拉取子模块）
if [ ! -f "$SUBMOD/configure" ]; then
    echo "==> 初始化 git 子模块 packages/cpython ..."
    git -C "$APP_DIR" submodule update --init --depth 1 packages/cpython
fi

# 2. 编译内嵌 Python（若未生成；可断点续编：已有 build 目录就直接 make）
if [ ! -x "$PY_BIN" ]; then
    echo "==> 从子模块编译 Python 3.13.7 到 $PREFIX ..."
    mkdir -p "$BUILD_DIR"
    cd "$BUILD_DIR"
    if [ ! -f Makefile ]; then
        "$SUBMOD/configure" --prefix="$PREFIX" --enable-shared --with-ensurepip=install
    fi
    make -j"$(nproc)"
    make install
    cd "$APP_DIR"
    echo "==> 内嵌 Python 编译完成"
fi

# 3. 安装依赖（缺什么装什么）
"$PY_BIN" -m pip install --upgrade pip setuptools wheel
"$PY_BIN" -m pip install -r "$APP_DIR/requirements.txt"

cat <<EOF

PureVox 内嵌 Python 就绪（独立于系统环境，位于 packages/python313）。
用下面的启动器运行 / 构建（自动带 LD_LIBRARY_PATH）：
  ./py313 run_pyside6.py
  ./py313 setup.py build_ext --inplace --force
  ./py313 -m pip install <pkg>
EOF
