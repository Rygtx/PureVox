#!/bin/bash
# PureVox - AppImage packaging script (universal Linux)
# Output: dist/PureVox-Linux-x64-<yyyy-MM-dd-HHmm>-release.AppImage
# Bundles the embedded Python 3.8 (packages/python38) + app + bundled onnxruntime.
# Requires: gcc, pkg-config, libpipewire-0.3-devel, python3 (build), wget (appimagetool)
set -e
cd "$(dirname "$0")"

# Timestamp reuses the 7z rule (yyyy-MM-dd-HHmm)
DATE="$(date +%Y-%m-%d-%H%M)"
APPIMG_FILE="PureVox-Linux-x64-${DATE}-release.AppImage"
DIST="dist"
STAGE="${TMPDIR:-/tmp}/purevox_appimage"
APPDIR="$STAGE/AppDir"
APP_NAME="purevox"

echo "==> ensure embedded Python 3.8 (packages/python38)"
if [ ! -x "packages/python38/bin/python3" ]; then
    ./bootstrap_python38.sh
fi

echo "==> build pure C shared libraries"
python3 setup.py build_ext --inplace --force >/dev/null
[ -f "libaimic.so" ] && [ -f "libpvpipe.so" ] || { echo "missing .so"; exit 1; }

echo "==> prepare AppDir"
rm -rf "$STAGE"
mkdir -p "$APPDIR/usr/lib/purevox" "$APPDIR/usr/bin"

echo "==> copy sources/models/lib"
for f in \
    audio_processor.py config_manager.py dialog_about.py dialog_eq.py logger.py \
    model_config.py run_pyside6.py spectrum_histogram.py theme_colors.py \
    dialog_tse_reference.py ui_pyside6.py user_paths.py wav_io.py \
    aimic.py pvpipe.py \
    aec9_ep0544.onnx tse15_stream_ep_0673.onnx v9_fft2048_band256_epoch_261.onnx \
    audio_icon_off.ico audio_icon_on.ico; do
    cp "$f" "$APPDIR/usr/lib/purevox/"
done
cp libaimic.so libpvpipe.so "$APPDIR/usr/lib/purevox/"
cp packages/onnxruntime-linux-x64-1.11.1/lib/libonnxruntime.so* "$APPDIR/usr/lib/purevox/"
cp -r html "$APPDIR/usr/lib/purevox/"
mkdir -p "$APPDIR/usr/lib/purevox/server"
cp server/*.py "$APPDIR/usr/lib/purevox/server/"
cp -r pvplatform "$APPDIR/usr/lib/purevox/"
find "$APPDIR/usr/lib/purevox" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

echo "==> bundle embedded Python 3.8"
cp -a packages/python38 "$APPDIR/usr/python38"

echo "==> desktop entry (AppImage needs it inside AppDir)"
mkdir -p "$APPDIR/usr/share/applications"
cat > "$APPDIR/usr/share/applications/$APP_NAME.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=PureVox
Exec=purevox
Icon=purevox
Terminal=false
Categories=AudioVideo;Audio;Utility;
EOF
mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"
magick audio_icon_on.ico[0] -resize 256x256 \
    "$APPDIR/usr/share/icons/hicolor/256x256/apps/purevox.png" 2>/dev/null \
 || python3 -c "
from PIL import Image
im = Image.open('audio_icon_on.ico')
im = im.convert('RGBA').resize((256,256), Image.LANCZOS)
im.save('$APPDIR/usr/share/icons/hicolor/256x256/apps/purevox.png')
" 2>/dev/null || true

echo "==> AppRun launcher"
cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
export PYTHONHOME="$HERE/usr/python38"
export LD_LIBRARY_PATH="$HERE/usr/lib/purevox${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PATH="$HERE/usr/python38/bin:$PATH"
cd "$HERE/usr/lib/purevox" || exit 1
exec "$HERE/usr/python38/bin/python3" run_pyside6.py "$@"
EOF
chmod +x "$APPDIR/AppRun"

echo "==> appimagetool"
TOOL="$STAGE/appimagetool"
[ -x "$TOOL" ] || wget -q -O "$TOOL" \
  "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
chmod +x "$TOOL"
mkdir -p "$DIST"
rm -f "$DIST/$APPIMG_FILE"
# CI 容器无 FUSE，AppImage 工具需用 --appimage-extract-and-run（本地有 FUSE 时参数无害）
"$TOOL" --appimage-extract-and-run "$APPDIR" "$DIST/$APPIMG_FILE" >/dev/null
echo "==> done: $DIST/$APPIMG_FILE"
