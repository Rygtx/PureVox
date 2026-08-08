# PureVox

Real-time AI audio processing for your microphone — denoising / target speech extraction /
echo cancellation, for both the local microphone and remote network streaming.

## Docs

- 📖 [User manual (Chinese)](用户手册.html)
- 📋 [Changelog](CHANGELOG.md)
- 🇨🇳 [README 中文](README.md)

## Features

- 🎤 Real-time AI denoising (48 kHz, models loaded on demand)
- 🗣️ TSE target speech extraction (record a reference clip, then separate your voice from background)
- 🔊 AEC echo cancellation
- 🎛️ 31-band EQ
- 📊 AGC automatic gain control / VAD voice activity detection
- 📱 Remote microphone: phone browser / Android APK streams over LAN to the PC for processing
- 🖥️ Windows (WASAPI) and Linux (native PipeWire)

## Requirements

| Platform | Requirements |
|---|---|
| Windows | Windows 10/11, Python 3.8+ |
| Linux | Python 3.8+, PipeWire (native libpipewire audio; virtual mic is a null-sink) |

> **Windows 7**: Python 3.8 is the last Python supporting Win7; this project's source and its
> dependencies stay Python 3.8 compatible (model opsets 13/14/15, all ≤16; onnxruntime pinned
> to 1.11.1). Note the GUI stack PySide6 6.x (Qt6) itself requires Windows 10+, so full Win7
> desktop support is out of scope.

## Quick start

### Embedded Python 3.8 (recommended, independent of the system)

The project can bundle its own Python 3.8, fully isolated from the system Python
(e.g. 3.14). **Windows** downloads a prebuilt package (NuGet); **Linux** has no
prebuilt 3.8 downloadable, so the CPython source is vendored as a **git submodule**
`packages/cpython` (CPython@v3.8.20, shallow) and built once by the bootstrap
(out-of-tree, without dirtying the submodule). Everything lives under `packages/`.

```bash
# After cloning, fetch the submodule (CPython source):
git submodule update --init --depth 1 packages/cpython

# Linux (just run the bootstrap; it compiles)
./bootstrap_python38.sh                     # -> packages/python38 (self-contained) + deps
./py38 run_pyside6.py                       # run
./py38 setup.py build_ext --inplace --force # build libaimic.so + libpvpipe.so (pure C, gcc)

# Windows (PowerShell, NuGet prebuilt download)
powershell -ExecutionPolicy Bypass -File bootstrap_python38.ps1   # -> packages\python38w
# build_win.ps1 then automatically uses packages\python38w\python.exe
```

Alternatively, use a system Python 3.8+:

```bash
# On Windows append `-r requirements-win.txt`
pip install -r requirements.txt

python run_pyside6.py
```

### Linux

```bash
# System deps (e.g. AOSC)
sudo oma install -y gcc pkgconf pipewire libpipewire-0.3-devel

# Recommended: embedded 3.8 (see above)
./bootstrap_python38.sh
./py38 setup.py build_ext --inplace --force   # build pure C shared libs (libaimic.so + libpvpipe.so)
./py38 run_pyside6.py

# Or run directly with system python3:
pip install --user -r requirements.txt
python3 run_pyside6.py
```

Linux audio uses native PipeWire: the format is negotiated as F32 mono 48000 Hz, with
resampling and channel conversion handled by PipeWire. The virtual microphone is the
monitor of a mono null-sink named `purevox_out`; other apps can select
**"PureVox 虚拟麦克风"** (PureVox Virtual Mic) as their input device. The AEC far-end
(echo reference) is also captured natively via PipeWire (`stream.capture.sink` on the
speaker sink), with no PyAudio/PortAudio dependency.

### Windows remote-mic add-ons

The remote-microphone feature requires Opus decoding and the VB-CABLE virtual sound
card, neither of which is bundled:

1. `opus.dll` — from [DSharpPlus VoiceNext Natives](https://github.com/DSharpPlus/DSharpPlus/raw/master/docs/natives/vnext_natives_win32_x64.zip); rename `libopus.dll` → `opus.dll` and place it in `server/` (or system PATH)
2. **VB-CABLE** — download `VBCABLE_Setup_x64.exe` from [vb-audio.com/Cable](https://vb-audio.com/Cable/) into the project root or `tools/`; the app will guide the silent install

## Packaging

### Windows (PyInstaller EXE)

```powershell
powershell -ExecutionPolicy Bypass -File build_win.ps1   # produces dist/PureVox_<date>.exe (self-extracting)
```

The script runs the full flow: build `aimic.dll` (mingw gcc) → PyInstaller → tcl/tk + unused PySide6 module cleanup →
7z self-extracting EXE. Windows CI (`.github/workflows/windows.yml`) runs the same flow and uploads the EXE.

### Linux (deb)

```bash
bash pack_deb.sh    # produces dist/purevox_<version>_amd64.deb (source + .so + models + html)
```

### Android APK

```bash
cd android
./gradlew assembleDebug    # output: android/app/build/outputs/apk/debug/app-debug.apk
```

Requires JDK 17, Android SDK platform 34, NDK r27. On first build put the Opus source at
`android/opus-src/` ([xiph/opus v1.5.2](https://github.com/xiph/opus/archive/refs/tags/v1.5.2.zip)).

## Remote microphone

Phone / browser → WSS(Opus) → PC server → AI processing chain → speaker / virtual mic

```
Phone → https://<PC_IP>:59123 (mDNS broadcast _purevox._tcp.local.) → denoise → output
```

- Browser: phone and PC on the same LAN, visit `https://<PC_IP>:59123`, trust the
  self-signed cert, tap the mic button to stream
- APK: opens and auto-discovers LAN servers, connects and streams automatically
- Client message: `{"type":"audio","data":"<base64 opus>","seq":N}`; server replies `{"type":"ack","seq":N}`
- Frame size 960 samples (20 ms @48 kHz), aligned with the Opus encoders

## Project layout

```
run_pyside6.py            # entry point (single-instance lock)
ui_pyside6.py             # main UI (PySide6): panels, device selection, mode switching
audio_processor.py        # core audio engine + TSE reference recording utilities
aimic.c + aimic.py         # C audio core → libaimic.so (gcc) + ctypes binding
pipewire_client.c + pvpipe.py  # native PipeWire bridge → libpvpipe.so (Linux, pure C + ctypes)
pvplatform/               # platform abstraction: audio/ (enum, SpeakerCapture), system/ (single-instance, virtual mic)
config_manager.py         # JSON config (migrates legacy keys)
model_config.py           # ONNX model filename constants
dialog_about.py           # About dialog
dialog_eq.py              # EQ dialog
dialog_tse_reference.py   # TSE reference-recording dialog
server/                   # remote-mic HTTPS/WSS server (aiohttp + Opus + mDNS + TLS)
html/                     # browser streaming front-end (AudioWorklet + Opus WASM)
android/                  # Android client (Kotlin + OkHttp + Opus JNI)
pack_deb.sh               # Linux deb packaging
setup.py                  # C++ extension build
```

## Tech stack

| Component | Technology |
|---|---|
| Desktop GUI | Python + PySide6 |
| Audio processing | Pure C shared libs (gcc) + ONNX Runtime C API (all spectrum/FFT in C; Python only does ctypes data marshalling) |
| Linux audio | Native PipeWire (libpipewire); PortAudio/JACK deprecated |
| Windows audio | WASAPI full-duplex |
| Server | Python aiohttp + zeroconf + cryptography |
| Audio codec | Opus (PC: opuslib, APK: NDK build, Web: WASM) |
| Android | Kotlin + OkHttp + NsdManager + AudioRecord |

## License

- **Source code**: [GPL-3.0](LICENSE) (GNU General Public License v3.0 or later)
- **Built-in AI models**: NOT covered by the GPL. They are the property of a2heng and may
  only be used with PureVox under authorization — see [MODEL-LICENSE.md](MODEL-LICENSE.md)

Author's MIT-licensed model repos (earlier versions, freely usable):

- <https://github.com/a2heng/lightweight-denoise-48k>
- <https://github.com/a2heng/lightweight-aec-48k>

Third-party components (PySide6, ONNX Runtime, PortAudio, Opus, etc.) are under their own
licenses; see [LICENSE-THIRD-PARTY.txt](LICENSE-THIRD-PARTY.txt).

## Contact

- GitHub: <https://a2heng.github.io/>
- Bilibili: <https://space.bilibili.com/10850943>
