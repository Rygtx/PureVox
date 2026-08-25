#!/bin/bash
# PureVox — PySide6 瘦身脚本（pack_deb.sh / pack_appimage.sh 共用，单一实现路径）
#
# 应用只用 QtWidgets / QtCore / QtGui，砍掉 qml/3D/Charts 等冗余，
# 使捆绑的 PySide6 由 ~560M 降到 ~112M。依赖闭包为实测确认：
#   - libpyside6.abi3.so 硬依赖 libQt6Qml（与 Windows 同约束，勿删）
#   - libqxcb(platforms) 需要 libQt6OpenGL，缺失会解析到系统 Qt 版本冲突
#   - Qt/lib 保留 Core/Gui/Widgets/DBus/Network/Qml/XcbQpa/OpenGL/OpenGLWidgets
#
# 用法: slim_pyside6.sh <PySide6 绝对路径>   （对给定 site-packages/PySide6 就地裁剪）
set -euo pipefail

PYSIDE="${1:?用法: slim_pyside6.sh <PySide6 绝对路径>}"
[ -d "$PYSIDE" ] || { echo "错误: 找不到 PySide6 目录 $PYSIDE"; exit 1; }

echo "  - 瘦身 PySide6: $(du -sh "$PYSIDE" | cut -f1) -> 只剩 QtWidgets/QtCore/QtGui 依赖闭包"

# 1. 顶层只留必需 .abi3.so（QtCore/QtGui/QtWidgets + libpyside6.abi3）
for m in "$PYSIDE"/Qt*.abi3.so; do
    b="$(basename "$m" .abi3.so)"
    case "$b" in QtCore|QtGui|QtWidgets) ;; *) rm -f "$m" "${b}.pyi";; esac
done

# 2. Qt/lib 只留依赖闭包 + OpenGL（见头注释）
for l in "$PYSIDE"/Qt/lib/libQt6*.so.6; do
    b="$(basename "$l" | sed 's/^libQt6//;s/.so.6//')"
    case "$b" in Core|Gui|Widgets|DBus|Network|Qml|XcbQpa|OpenGL|OpenGLWidgets) ;;
        *) rm -f "$l" "${l%.so.6}.so";; esac
done

# 3. 删除 qml 运行库 + examples + 非必要 plugins
rm -rf "$PYSIDE/Qt/qml" "$PYSIDE/examples"
for d in assetimporters designer egldeviceintegrations geometryloaders \
          networkinformationbackends printsupport qmltooling renderers \
          renderplugins sceneparsers scxmldatamodel sqldrivers virtualkeyboard \
          wayland-decoration-client wayland-graphics-integration-client \
          wayland-graphics-integration-server wayland-shell-integration; do
    rm -rf "$PYSIDE/Qt/plugins/$d"
done

echo "  - 瘦身完成: $(du -sh "$PYSIDE" | cut -f1)"
