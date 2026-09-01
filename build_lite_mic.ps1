# PureVox Lite - local/CI build script (pure python, no gcc)
# Single source of truth for the working PyInstaller recipe.
# Usage: powershell -ExecutionPolicy Bypass -File build_lite_mic.ps1
$ErrorActionPreference = "Stop"

# --- Version stamp: tag lite-v<yyyy.MM.dd.HHmm> -> ver; fallback to local time ---
$ref = $env:GITHUB_REF_NAME
if ($ref -and $ref.StartsWith("lite-v")) { $ver = $ref.Substring(6) }
elseif ($ref -and $ref.StartsWith("lite")) { $ver = ($ref.Substring(4) -replace "^[-_]*","") }
elseif ($ref -and $ref.StartsWith("v")) { $ver = $ref.Substring(1) }
else {
    $ver = (Get-Date -Format 'yyyy-MM-dd-HHmm') -replace '-', '.'
}
if (-not $ver) { $ver = (Get-Date -Format 'yyyy-MM-dd-HHmm') -replace '-', '.' }
Write-Host "Lite version: $ver"

# --- Python resolution: embedded python312w > python on PATH ---
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$embeddedPy = Join-Path $scriptDir "packages\python312w\python.exe"
if (Test-Path $embeddedPy) { $PY = @($embeddedPy) }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $PY = @("python") }
elseif (Get-Command py -ErrorAction SilentlyContinue) { $PY = @("py", "-3") }
else { throw "no python found" }
& $PY --version

# --- Deps (single source of truth = requirements.txt; platform diff via env markers) ---
& $PY -m pip install -q -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# --- Syntax check + smoke import ---
& $PY -m compileall -q lite_mic
if ($LASTEXITCODE -ne 0) { throw "compileall failed" }
& $PY -c "import lite_mic.config, lite_mic.audio, lite_mic.engine, lite_mic.ui; print('import OK')"
if ($LASTEXITCODE -ne 0) { throw "smoke import failed" }

# --- Icon: committed asset (dev/build share the same file) ---
if (-not (Test-Path "assets\icons\lite_tray.ico")) { throw "assets\icons\lite_tray.ico missing" }

# --- Version stamp module (window title) ---
Set-Content _build_version.py "BUILD_DATE = `"$ver`"" -Encoding UTF8

# --- PyInstaller onedir ---
# onedir: no per-launch extraction to %TEMP%\_MEI* (onefile = +121MB temp disk & slow start)
# Explicit collection is mandatory; excludes cut unused codec bulk (PIL._avif alone ~7.5MB).
& $PY -m PyInstaller --noconfirm --name PureVoxLite `
    --windowed `
    --icon assets\icons\lite_tray.ico `
    --collect-all onnxruntime `
    --collect-all numpy `
    --collect-all PIL `
    --hidden-import=pyaudio `
    --exclude-module PIL._avif `
    --add-data "models\purevox_denoise_202609_ep0106.onnx;models" `
    --add-data "assets\fonts\*.ttf;assets/fonts" `
    --add-data "assets\icons\lite_tray.ico;assets\icons" `
    --add-data "assets\icons\lite_tray.png;assets\icons" `
    --add-data "_build_version.py;." `
    lite_mic/main.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

if (-not (Test-Path "dist\PureVoxLite\PureVoxLite.exe")) { throw "PureVoxLite.exe not built" }
$sz = [math]::Round((Get-ChildItem dist\PureVoxLite -Recurse -File | Measure-Object Length -Sum).Sum/1MB, 1)
Write-Host "==> Done: dist\PureVoxLite\  (${sz} MB)"

