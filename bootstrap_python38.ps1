# PureVox — 内嵌 Python 3.8 引导脚本（Windows）
# 从 NuGet 下载预编译的完整版 Python 3.8（含开发头/链接库，可编译 aimic.pyd 并用
# PyInstaller 打包）解到 packages\python38w\，独立于系统安装的 Python（可能为 3.14）。
# 幂等：已存在则跳过下载，仅补齐 pip 依赖。
#
# 用法: powershell -ExecutionPolicy Bypass -File bootstrap_python38.ps1
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$dest = Join-Path $root "packages"
$pyDir = Join-Path $dest "python38w"
$srcPy = Join-Path $pyDir "tools\python.exe"
$py = Join-Path $pyDir "python.exe"
$pyVer = "3.8.10"   # NuGet python 包最后一个 3.8

# 1. 下载并解包完整 Python 3.8（NuGet 免管理员；tools\ 里的才是真正根，解包后平铺）
if (-not (Test-Path $py)) {
    Write-Host "==> 下载 Python $pyVer (NuGet)..."
    $nupkg = Join-Path $dest "python.$pyVer.nupkg.tmp"
    Invoke-WebRequest -Uri "https://api.nuget.org/v3-flatcontainer/python/$pyVer/python.$pyVer.nupkg" -OutFile $nupkg
    $tmp = Join-Path $dest "python-$pyVer.tmp"
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    Expand-Archive -Path $nupkg -DestinationPath $tmp -Force
    Remove-Item $nupkg
    # 平铺：把 tmp\tools\* 上移为 pyDir\*
    if (Test-Path $pyDir) { Remove-Item $pyDir -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $pyDir | Out-Null
    Move-Item -Path (Join-Path $tmp "tools\*") -Destination $pyDir -Force
    Remove-Item $tmp -Recurse -Force
}

# 2) 引导 pip（NuGet 包自带 pip/ensurepip）
& $py -m ensurepip --upgrade
if ($LASTEXITCODE -ne 0) { throw "ensurepip 失败" }
& $py -m pip install --upgrade pip setuptools wheel
& $py -m pip install -r (Join-Path $root "requirements.txt") -r (Join-Path $root "requirements-win.txt")

Write-Host "PureVox 内嵌 Python 3.8 就绪: $py"
Write-Host "用 build_win.ps1 打包（会自动使用 packages\python38w\python.exe）"