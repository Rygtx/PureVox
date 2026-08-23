#!/bin/sh
# PureVox — 内嵌 Python 3.12 引导脚本（Linux）
# 下载 CPython 3.12.11 官方源码包（一次性，可缓存），编译出自包含的
# packages/python312/（独立于系统 Python）。幂等：已生成则跳过构建，
# 仅补齐依赖。不再依赖 git 子模块——源码包按需拉取。
#
# 用法: ./bootstrap_python312.sh
# 环境变量:
#   PUREVOX_CPYTHON_TARBALL  预置的源码包路径（离线/内网用）
set -e

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PREFIX="$APP_DIR/packages/python312"
BUILD_DIR="$APP_DIR/.py312-src"
PY_MAJOR=3.12
PY_BIN="$PREFIX/bin/python$PY_MAJOR"

PY_VER=3.12.11
TARBALL_NAME="Python-${PY_VER}.tgz"
TARBALL_URL="https://www.python.org/ftp/python/${PY_VER}/${TARBALL_NAME}"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/purevox"
TARBALL="${PUREVOX_CPYTHON_TARBALL:-$CACHE_DIR/$TARBALL_NAME}"

# 内嵌解释器自带 libpython3.12.so，需让子进程找到它（独立于系统环境）
export LD_LIBRARY_PATH="$PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

# 1. 获取 CPython 源码包（一次性；已缓存则直接复用）
if [ ! -f "$TARBALL" ]; then
    echo "==> 下载 CPython ${PY_VER} 源码包 ..."
    mkdir -p "$(dirname "$TARBALL")"
    if command -v curl >/dev/null 2>&1; then
        curl -fL -o "$TARBALL.part" "$TARBALL_URL"
    else
        wget -O "$TARBALL.part" "$TARBALL_URL"
    fi
    mv "$TARBALL.part" "$TARBALL"
fi

# 2. 编译内嵌 Python（若未生成；可断点续编：已有 Makefile 就直接 make）
if [ ! -x "$PY_BIN" ]; then
    SRC="$BUILD_DIR/Python-${PY_VER}"
    echo "==> 从源码编译 Python ${PY_VER} 到 $PREFIX ..."
    rm -rf "$SRC"
    mkdir -p "$BUILD_DIR"
    tar -xzf "$TARBALL" -C "$BUILD_DIR"
    cd "$SRC"
    ./configure --prefix="$PREFIX" --enable-shared --with-ensurepip=install
    make -j"$(nproc)"
    make install
    cd "$APP_DIR"
    echo "==> 内嵌 Python 编译完成"
fi

# 3. 安装依赖（缺什么装什么）
"$PY_BIN" -m pip install --upgrade pip setuptools wheel
"$PY_BIN" -m pip install -r "$APP_DIR/requirements.txt"

cat <<EOF

PureVox 内嵌 Python 就绪（独立于系统环境，位于 packages/python312）。
用下面的启动器运行（自动带 LD_LIBRARY_PATH）：
  ./py312 run_pyside6.py
  ./py312 -m pip install <pkg>
EOF
