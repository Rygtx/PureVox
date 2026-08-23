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

# --- Deps (explicit: pystray/pyaudio are required at runtime, not optional) ---
& $PY -m pip install -q onnxruntime==1.22.0 numpy pillow pystray pyinstaller
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
& $PY -m pip install -q pyaudio
if ($LASTEXITCODE -ne 0) { Write-Host "WARN: pyaudio install failed" }

# --- Syntax check + smoke import ---
& $PY -m compileall -q lite_mic
if ($LASTEXITCODE -ne 0) { throw "compileall failed" }
& $PY -c "import lite_mic.config, lite_mic.audio, lite_mic.engine, lite_mic.ui; print('import OK')"
if ($LASTEXITCODE -ne 0) { throw "smoke import failed" }

# --- Icon: pixel P from bundled font ---
& $PY -c "from PIL import Image, ImageDraw, ImageFont; import os; img=Image.new('RGBA',(256,256),(0,0,0,0)); d=ImageDraw.Draw(img); fp=os.path.join('lite_mic','fonts','ark-pixel-12px-monospaced-zh_cn.ttf'); pf=ImageFont.truetype(fp,180) if os.path.isfile(fp) else ImageFont.load_default(); bbox=d.textbbox((0,0),'P',font=pf,stroke_width=8); tw=bbox[2]-bbox[0]; th=bbox[3]-bbox[1]; d.text(((256-tw)//2,(256-th)//2-5),'P',fill='#6D4C41',font=pf,stroke_width=8,stroke_fill='#FFB74D'); img.save('assets/icons/lite_icon.ico', sizes=[(256,256),(128,128),(64,64),(32,32),(16,16)])"
if (-not (Test-Path "assets\icons\lite_icon.ico")) { throw "icon generation failed" }

# --- Version stamp module (window title) ---
Set-Content _build_version.py "BUILD_DATE = `"$ver`"" -Encoding UTF8

# --- PyInstaller onedir ---
# onedir: no per-launch extraction to %TEMP%\_MEI* (onefile = +121MB temp disk & slow start)
# Explicit collection is mandatory; excludes cut unused codec bulk (PIL._avif alone ~7.5MB).
& $PY -m PyInstaller --noconfirm --name PureVoxLite `
    --windowed `
    --icon assets\icons\lite_icon.ico `
    --collect-all onnxruntime `
    --collect-all numpy `
    --collect-all PIL `
    --collect-all pystray `
    --hidden-import=pyaudio `
    --exclude-module PIL._avif `
    --add-data "models\v9_fft2048_band256_epoch_261.onnx;models" `
    --add-data "lite_mic/fonts;fonts" `
    --add-data "_build_version.py;." `
    lite_mic/main.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

if (-not (Test-Path "dist\PureVoxLite\PureVoxLite.exe")) { throw "PureVoxLite.exe not built" }
$sz = [math]::Round((Get-ChildItem dist\PureVoxLite -Recurse -File | Measure-Object Length -Sum).Sum/1MB, 1)
Write-Host "==> Done: dist\PureVoxLite\  (${sz} MB)"

