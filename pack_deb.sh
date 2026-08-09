#!/bin/bash
# PureVox — Linux deb 打包脚本
# 产出: dist/purevox_<version>-<rev>_amd64.deb
#
# 布局（与既有版本一致）:
#   /opt/purevox/          全部源码+.so+模型+html+pvplatform
#   /usr/bin/purevox       启动脚本 → cd /opt/purevox && python3 run_pyside6.py
#   /usr/share/applications/purevox.desktop
#   /usr/share/icons/hicolor/256x256/apps/purevox.png
#
# Depends: python-3, pyside6, zeroconf, aiohttp, cryptography, opus,
#          pipewire（libpipewire 原生音频）；onnxruntime 捆绑预编译 1.11.1
# opuslib 系统包缺则 pip install --user（写进 Recommends）

set -euo pipefail
cd "$(dirname "$0")"

VERSION="1.0.6"
REV="1"
ARCH="amd64"
PKG_NAME="purevox"
# 时间戳复用 7z 规则（yyyy-MM-dd-HHmm），仅文件名带，control 内 Version 保持 VERSION-REV
DATE="$(date +%Y-%m-%d-%H%M)"
PKG_FILE="${PKG_NAME}_${VERSION}-${REV}_${ARCH}_${DATE}.deb"
DIST="dist"
STAGE="${TMPDIR:-/tmp}/purevox_deb_build"
ROOT="$STAGE/root"
CONTROL="$ROOT/DEBIAN/control"

echo "==> 构建纯 C 共享库 (libaimic.so + libpvpipe.so)"
python3 setup.py build_ext --inplace --force >/dev/null
[ -f "libaimic.so" ] || { echo "缺少 libaimic.so"; exit 1; }
[ -f "libpvpipe.so" ] || { echo "缺少 libpvpipe.so"; exit 1; }

echo "==> 准备打包目录 $STAGE"
rm -rf "$STAGE"
mkdir -p "$ROOT/opt/purevox" \
         "$ROOT/usr/bin" \
         "$ROOT/usr/share/applications" \
         "$ROOT/usr/share/icons/hicolor/256x256/apps" \
         "$ROOT/DEBIAN"

echo "==> 拷贝源码/模型/图标"
for f in \
    audio_processor.py config_manager.py dialog_about.py dialog_eq.py logger.py \
    model_config.py run_pyside6.py spectrum_histogram.py theme_colors.py \
    dialog_tse_reference.py ui_pyside6.py user_paths.py wav_io.py \
    aimic.py pvpipe.py \
    aec9_ep0544.onnx tse15_stream_ep_0673.onnx v9_fft2048_band256_epoch_261.onnx \
    audio_icon_off.ico audio_icon_on.ico; do
    cp "$f" "$ROOT/opt/purevox/"
done
cp "libaimic.so" "$ROOT/opt/purevox/"
cp "libpvpipe.so" "$ROOT/opt/purevox/"

echo "==> 拷贝捆绑的 onnxruntime 1.11.1 动态库（aimic 链接 libonnxruntime.so.1.11.1）"
cp packages/onnxruntime-linux-x64-1.11.1/lib/libonnxruntime.so* "$ROOT/opt/purevox/"

echo "==> 拷贝 html/"
cp -r html "$ROOT/opt/purevox/"

echo "==> 拷贝 server/（剔除 Windows opus.dll）"
mkdir -p "$ROOT/opt/purevox/server"
cp server/*.py "$ROOT/opt/purevox/server/"

echo "==> 拷贝 pvplatform/"
cp -r pvplatform "$ROOT/opt/purevox/"
find "$ROOT/opt/purevox" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

echo "==> /usr/bin/purevox 启动脚本"
cat > "$ROOT/usr/bin/purevox" <<'EOF'
#!/bin/sh
# PureVox — AI 麦克风降噪
# /opt/purevox 下的 libaimic.so 链捆绑的预编译 onnxruntime（libonnxruntime.so*），提前注入 LD_LIBRARY_PATH
export LD_LIBRARY_PATH="/opt/purevox${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
cd /opt/purevox || exit 1
exec /usr/bin/python3 /opt/purevox/run_pyside6.py "$@"
EOF
chmod +x "$ROOT/usr/bin/purevox"

echo "==> desktop 文件"
cat > "$ROOT/usr/share/applications/purevox.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=PureVox
Name[zh_CN]=PureVox
GenericName=AI Mic Noise Reduction
GenericName[zh_CN]=AI 麦克风降噪
Comment=Real-time AI microphone noise reduction
Comment[zh_CN]=实时 AI 麦克风降噪
Exec=/usr/bin/purevox
Icon=purevox
Terminal=false
Categories=AudioVideo;Audio;Utility;
Keywords=mic;noise;denoise;audio;PureVox;
StartupNotify=false
EOF

echo "==> 图标 (ico → png)"
magick audio_icon_on.ico[0] -resize 256x256 \
    "$ROOT/usr/share/icons/hicolor/256x256/apps/purevox.png" 2>/dev/null \
 || python3 -c "
from PIL import Image
im = Image.open('audio_icon_on.ico')
im = im.convert('RGBA').resize((256, 256), Image.LANCZOS)
im.save('$ROOT/usr/share/icons/hicolor/256x256/apps/purevox.png')
"

echo "==> DEBIAN/control"
mkdir -p "$ROOT/DEBIAN"
cat > "$CONTROL" <<EOF
Package: purevox
Version: $VERSION-$REV
Section: sound
Priority: optional
Architecture: $ARCH
Maintainer: a2heng <752848283@qq.com>
Depends: python-3 (>= 3.13), pyside6, zeroconf, aiohttp, cryptography, opus, pipewire
Recommends: opuslib
Description: PureVox — Real-time AI microphone noise reduction
 Real-time AI audio denoising / target speech extraction / echo cancellation
 for the local microphone, with remote network streaming support.
 PureVox 实时 AI 麦克风降噪/目标提取/回声消除。
 .
 Python 依赖（opuslib）若系统包管理器未提供，
 请用用户级安装: pip install --user opuslib
 .
 Linux 音频基于原生 PipeWire（libpipewire），格式协商 F32 单声道 48000Hz，
 重采样与声道转换由 PipeWire 负责。虚拟麦克风为单声道 null-sink
 purevox_out 的 monitor，其它应用可选 "PureVox 虚拟麦克风" 作为输入设备。
EOF

echo "==> 构建 $PKG_FILE"
mkdir -p "$DIST"
rm -f "$DIST/$PKG_FILE"
dpkg-deb --build --root-owner-group "$ROOT" "$DIST/$PKG_FILE" >/dev/null
echo "==> 完成: $DIST/$PKG_FILE"
dpkg-deb --info "$DIST/$PKG_FILE" | head -14 || true
