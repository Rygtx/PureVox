# PureVox — Windows 打包脚本（PyInstaller EXE + 7z 自解压）
# 产出: dist/PureVox_<yyyy-MM-dd-HHmm>.exe
# 用法: powershell -ExecutionPolicy Bypass -File build_win.ps1
$ErrorActionPreference = "Stop"

$date = Get-Date -Format 'yyyy-MM-dd-HHmm'

# 1. 编译 C++ 扩展 (aimic.pyd)
python setup.py build_ext --inplace --force

# 2. 生成版本号（窗口标题显示构建日期）
Set-Content _build_version.py "BUILD_DATE = `"$date`"" -Encoding UTF8 -NoNewline

# 3. PyInstaller 打包
#    lazy-import 的模块必须 hidden-import（函数内 from xx import 不会被静态发现）
pyinstaller --clean --name PureVox --noconsole --icon=audio_icon_on.ico `
    --hidden-import=pyaudio `
    --hidden-import=audio_processor `
    --hidden-import=dialog_about `
    --hidden-import=dialog_eq `
    --hidden-import=dialog_tse_reference `
    --hidden-import=spectrum_histogram `
    --hidden-import=wav_io `
    --hidden-import=vbcable_installer `
    --hidden-import=pvplatform.audio.pwpipe_client `
    --add-data="*.onnx;." `
    --add-data="audio_icon_on.ico;." `
    --add-data="audio_icon_off.ico;." `
    --add-data="aimic*.pyd;." `
    --add-data="html\*.html;html\" `
    --add-data="html\css\*.css;html\css\" `
    --add-data="html\js\*.js;html\js\" `
    --add-data="html\wasm\*;html\wasm\" `
    --add-data="server\*.py;server\" `
    --add-data="server\opus.dll;server\" `
    --add-data="packages\onnxruntime-win-x64-1.24.4\lib\onnxruntime.dll;." `
    --add-data="packages\onnxruntime-win-x64-1.24.4\lib\onnxruntime_providers_shared.dll;." `
    --exclude-module=pandas,scipy,matplotlib,unittest,tensorflow,torch,PIL `
    -y run_pyside6.py

# 4. 清理无用文件（~46MB: tcl/tk + 未用的 PySide6 模块）
Remove-Item dist\PureVox\_internal\tcl86t.dll, dist\PureVox\_internal\tk86t.dll, dist\PureVox\_internal\_tkinter.pyd -Force -ErrorAction SilentlyContinue
Remove-Item dist\PureVox\_internal\_tcl_data, dist\PureVox\_internal\_tk_data, dist\PureVox\_internal\tcl8 -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item dist\PureVox\_internal\PySide6\opengl32sw.dll -Force -ErrorAction SilentlyContinue
Remove-Item dist\PureVox\_internal\PySide6\Qt6Quick.dll, dist\PureVox\_internal\PySide6\Qt6Qml.dll, dist\PureVox\_internal\PySide6\Qt6Pdf.dll, dist\PureVox\_internal\PySide6\Qt6DataVisualization.dll -Force -ErrorAction SilentlyContinue
Remove-Item dist\PureVox\_internal\PySide6\QtOpenGL.pyd, dist\PureVox\_internal\PySide6\QtQuick.pyd, dist\PureVox\_internal\PySide6\QtQml.pyd, dist\PureVox\_internal\PySide6\QtPdf.pyd -Force -ErrorAction SilentlyContinue

# 5. 打包为自解压 EXE（7z.sfx + 配置 + 7z 包）
Push-Location dist
..\packages\7z\7z.exe a "PureVox_$date.7z" "PureVox" | Out-Null
& cmd /c "copy /b ..\packages\7z\7z.sfx + ..\sfx_config.txt + PureVox_$date.7z PureVox_$date.exe >nul"
Remove-Item "PureVox_$date.7z"
Pop-Location

# 6. 复制文档到输出目录
Copy-Item 用户手册.html, CHANGELOG.md dist\ -Force

Write-Host "==> 完成: dist/PureVox_$date.exe"
