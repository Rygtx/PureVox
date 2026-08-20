# PureVox Lite Denoise Only — 入口
# Copyright (C) 2024-2026 a2heng <752848283@qq.com>
# SPDX-License-Identifier: GPL-3.0-or-later
#
# 零复用主线，仅复用自包含库 (onnxruntime, numpy, pyaudio)
# 运行即启动，无启停

import os
import sys
import ctypes

# 单实例：与完整版共用互斥名 PureVox，避免同时运行
def ensure_single_instance():
    if sys.platform.startswith("win"):
        try:
            kernel32 = ctypes.windll.kernel32
            mutex = kernel32.CreateMutexW(None, 0, "PureVox")
            err = kernel32.GetLastError()
            # ERROR_ALREADY_EXISTS = 183
            if err == 183:
                try:
                    import tkinter.messagebox as mb
                    # 需先创建隐藏 root
                    import tkinter as tk
                    r = tk.Tk()
                    r.withdraw()
                    mb.showerror("PureVox Lite", "PureVox 已在运行（完整版或轻量版），不可同时启动。")
                    r.destroy()
                except Exception:
                    pass
                sys.exit(0)
            return mutex
        except Exception:
            return None
    else:
        # Linux: 文件锁
        import fcntl
        path = os.path.join(os.path.expanduser("~"), ".purevox", "purevox.lock")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fp = open(path, "w")
        try:
            fcntl.flock(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            print("PureVox 已在运行")
            sys.exit(0)
        return fp

def set_autostart(enable):
    if sys.platform.startswith("win"):
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE)
            name = "PureVox"
            if enable:
                exe = sys.executable
                # lite 入口
                script = os.path.join(os.path.dirname(__file__), "main.py")
                cmd = f'"{exe}" "{script}"'
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
        except Exception as e:
            print("autostart fail", e)

def main():
    mutex = ensure_single_instance()
    # 独立配置
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.dirname(__file__))
    from config import load, save
    import audio
    import engine

    cfg = load()
    ins, outs = audio.list_devices()

    # 默认设备回退
    if not cfg.get("input_device") and ins:
        cfg["input_device"] = ins[0][0]
    if not cfg.get("output_device") and outs:
        cfg["output_device"] = outs[0][0]

    # 模型常驻
    model_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "v9_fft2048_band256_epoch_261.onnx")
    if not os.path.isfile(model_path):
        # 兼容 lite 目录内
        model_path = os.path.join(os.path.dirname(__file__), "v9_fft2048_band256_epoch_261.onnx")
    try:
        eng = engine.LiteDenoiseEngine(model_path)
    except Exception as e:
        try:
            import tkinter.messagebox as mb
            import tkinter as tk
            r = tk.Tk(); r.withdraw()
            mb.showerror("模型加载失败", str(e))
            r.destroy()
        except Exception:
            print(e)
        sys.exit(1)

    # 解析设备索引：兼容旧配置（无 [API] 前缀）按后缀匹配，支持 (disp,idx,props) 或 (disp,idx)
    def _to_map(lst):
        mp = {}
        for item in lst:
            if len(item) == 3:
                n,i,_ = item
                mp[n]=i
            else:
                n,i = item
                mp[n]=i
        return mp
    def _idx_list(lst):
        out=[]
        for item in lst:
            if len(item)==3:
                _,i,_=item
                out.append(i)
            else:
                _,i=item
                out.append(i)
        return out
    in_map = _to_map(ins)
    out_map = _to_map(outs)
    def resolve(name, mp, lst):
        if not name:
            vals = list(mp.values())
            return vals[0] if vals else -1
        if name in mp:
            return mp[name]
        for k, v in mp.items():
            if k.endswith(name) or name.endswith(k):
                return v
        for k, v in mp.items():
            if name in k or k in name:
                return v
        vals = list(mp.values())
        return vals[0] if vals else -1
    in_idx = resolve(cfg.get("input_device", ""), in_map, ins)
    out_idx = resolve(cfg.get("output_device", ""), out_map, outs)
    valid_in = set(_idx_list(ins))
    valid_out = set(_idx_list(outs))
    if in_idx not in valid_in and ins:
        in_idx = _idx_list(ins)[0]
    if out_idx not in valid_out and outs:
        out_idx = _idx_list(outs)[0]

    stream = None
    def start_stream():
        nonlocal stream
        # 同 API 校验：WASAPI/MME 混用即非法，仅提示不自动改输出
        ok, msg = audio.check_api_match(in_idx, out_idx)
        if not ok:
            try:
                import tkinter.messagebox as mb
                mb.showerror("组合非法", msg + "\n请将输入与输出设为同一 API。")
            except Exception:
                print(msg)
            return
        if stream:
            try:
                stream.stop()
            except Exception:
                pass
        stream = audio.LiteAudioStream(in_idx, out_idx, eng, cfg.get("pre_gain_db", 0.0), cfg.get("post_gain_db", 0.0))
        try:
            stream.start()
        except Exception as e:
            # 组合非法细化提示
            emsg = str(e)
            if "Invalid" in emsg or "非法" in emsg or "Unanticipated" in emsg:
                emsg = emsg + "\n提示：WASAPI 与 MME 不能混用，请将输入/输出设为同一 API。"
            try:
                import tkinter.messagebox as mb
                mb.showerror("音频启动失败", emsg)
            except Exception:
                print(emsg)
            # 不退出，允许改设备

    # 托盘/主窗口引用占位（供回调闭包捕获，避免 locals 误判）
    tray_win = None
    ui = None
    # UI 回调（主窗口与托盘双向同步，比例档位）
    def on_gain(which, val):
        iv = int(val)
        if which == "pre":
            cfg["pre_gain_db"] = iv
        else:
            cfg["post_gain_db"] = iv
        save(cfg)
        if stream:
            stream.set_gains(cfg["pre_gain_db"], cfg["post_gain_db"])
        # 同步托盘显示（BeautifiedTray）
        try:
            if tray_win:
                tray_win.sync_from_cfg()
        except Exception:
            pass
        try:
            if ui:
                # 同步主窗口数字框
                if which == "pre":
                    ui.pre_var.set(str(iv))
                else:
                    ui.post_var.set(str(iv))
        except Exception:
            pass

    def on_device(in_name, out_name):
        nonlocal in_idx, out_idx
        cfg["input_device"] = in_name
        cfg["output_device"] = out_name
        save(cfg)
        # 精确或后缀匹配
        if in_name in in_map:
            in_idx = in_map[in_name]
        else:
            for k, v in in_map.items():
                if k.endswith(in_name) or in_name.endswith(k):
                    in_idx = v
                    break
        if out_name in out_map:
            out_idx = out_map[out_name]
        else:
            for k, v in out_map.items():
                if k.endswith(out_name) or out_name.endswith(k):
                    out_idx = v
                    break
        start_stream()

    def on_autostart(enable):
        cfg["autostart"] = bool(enable)
        save(cfg)
        set_autostart(enable)

    # 启动即运行
    start_stream()

    # Tk UI 黑底白字（无系统标题栏，自绘）
    from ui import LiteUI

    def _hide_window():
        try:
            ui.root.withdraw()
        except Exception:
            pass
    def _show_window():
        try:
            ui.root.deiconify()
            ui.root.lift()
            ui.root.focus_force()
        except Exception:
            pass
    def _do_close():
        _hide_window()
    def _do_minimize():
        _hide_window()

    ui = LiteUI(cfg, ins, outs, on_gain, on_device, on_autostart, on_close=_do_close, on_minimize=_do_minimize)

    # 系统托盘（右键在系统底部显示，像素图标，含缩放切换与完整功能）
    tray = None
    has_tray = False
    # 统一缩放入口：委托给 LiteUI.set_zoom（内部已持久化 cfg），此处再同步一次 save 确保一致
    def _set_zoom(percent):
        try:
            percent = int(percent)
            # LiteUI.set_zoom 负责 scaling + geometry + 持久化
            ui.set_zoom(percent)
            # 双重保障：同步 cfg 并显式保存（ui.set_zoom 已保存过）
            cfg["zoom"] = ui._zoom
            try:
                save(cfg)
            except Exception:
                pass
        except Exception as e:
            print("zoom fail", e)

    # 优先尝试 pystray 系统托盘
    try:
        from PIL import Image, ImageDraw
        import pystray
        has_tray = True
    except Exception as e:
        has_tray = False
        print(f"tray init fail: {e}")

    if has_tray:
        try:
            def _make_icon():
                img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
                d = ImageDraw.Draw(img)
                # 仅像素大写 P，无边框背景，带边缘色
                try:
                    from PIL import ImageFont
                    import os
                    font_path = os.path.join(os.path.dirname(__file__), "fonts", "ark-pixel-12px-monospaced-zh_cn.ttf")
                    if os.path.isfile(font_path):
                        pf = ImageFont.truetype(font_path, 56)
                        bbox = d.textbbox((0, 0), "P", font=pf, stroke_width=3)
                        tw = bbox[2] - bbox[0]
                        th = bbox[3] - bbox[1]
                        x = (64 - tw) // 2
                        y = (64 - th) // 2 - 2
                        d.text((x, y), "P", fill="#6D4C41", font=pf, stroke_width=3, stroke_fill="#FFB74D")
                    else:
                        raise FileNotFoundError
                except Exception:
                    # 回退：手绘大像素 P，带边缘
                    px, py = 16, 8
                    s = 7
                    pat = [
                        [1,1,1,1],
                        [1,0,0,1],
                        [1,0,0,1],
                        [1,1,1,1],
                        [1,0,0,0],
                        [1,0,0,0],
                        [1,0,0,0],
                    ]
                    # 先画边缘
                    for dr in [(-1,0),(1,0),(0,-1),(0,1),(-1,-1),(1,-1),(-1,1),(1,1)]:
                        for r, row in enumerate(pat):
                            for c, v in enumerate(row):
                                if v:
                                    x0 = px + c*s + dr[0]
                                    y0 = py + r*s + dr[1]
                                    d.rectangle([x0, y0, x0+s-1, y0+s-1], fill="#FFB74D")
                    for r, row in enumerate(pat):
                        for c, v in enumerate(row):
                            if v:
                                x0 = px + c*s
                                y0 = py + r*s
                                d.rectangle([x0, y0, x0+s-1, y0+s-1], fill="#6D4C41")
                return img
            icon_img = _make_icon()

            def _show(icon, item):
                _show_window()
            def _sound(icon, item):
                ui._open_sound()
            def _vb(icon, item):
                ui._open_vb()
            def _autostart(icon, item):
                nv = not bool(cfg.get("autostart", False))
                on_autostart(nv)
                try:
                    ui.autostart_var.set(nv)
                except Exception:
                    pass
            def _exit(icon, item):
                try:
                    if icon:
                        icon.stop()
                except Exception:
                    pass
                try:
                    if stream:
                        stream.stop()
                except Exception:
                    pass
                try:
                    ui.root.destroy()
                except Exception:
                    pass
                os._exit(0)

            zoom_menu = pystray.Menu(
                pystray.MenuItem("100%", lambda icon, item: _set_zoom(100), checked=lambda item: getattr(ui, "_zoom", 100) == 100),
                pystray.MenuItem("125%", lambda icon, item: _set_zoom(125), checked=lambda item: getattr(ui, "_zoom", 100) == 125),
                pystray.MenuItem("150%", lambda icon, item: _set_zoom(150), checked=lambda item: getattr(ui, "_zoom", 100) == 150),
                pystray.MenuItem("175%", lambda icon, item: _set_zoom(175), checked=lambda item: getattr(ui, "_zoom", 100) == 175),
                pystray.MenuItem("200%", lambda icon, item: _set_zoom(200), checked=lambda item: getattr(ui, "_zoom", 100) == 200),
            )
            menu = pystray.Menu(
                pystray.MenuItem("显示主界面", _show, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("缩放比例", zoom_menu),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出", _exit),
            )
            tray = pystray.Icon("PureVox Lite", icon_img, "PureVox Lite — 运行中", menu)
            import threading as _th
            _th.Thread(target=tray.run, daemon=True).start()

            def on_close():
                _hide_window()
            ui.root.protocol("WM_DELETE_WINDOW", on_close)
            # 仅当窗口被最小化（iconic）时才隐藏，避免 withdraw 触发误隐藏
            ui.root.bind("<Unmap>", lambda e: on_close() if ui.root.state() == "iconic" else None)
        except Exception as e:
            import traceback
            print("tray run fail:", e, type(e))
            traceback.print_exc()
            has_tray = False
            tray = None

    # 托盘兜底：pystray 不可用时用纯 Tk 的 BeautifiedTray（无外部依赖，保证“托盘还是没有”不再出现）
    if not has_tray:
        try:
            from tray import BeautifiedTray
            # BeautifiedTray 自身是置顶小窗，置于右下角，提供增益快捷与显示/退出
            tray_win = BeautifiedTray(ui.root, cfg, on_gain, _show_window, lambda: (ui.root.destroy(), os._exit(0)))
            # 此时关闭主窗口应隐藏到托盘小窗，而不是直接退出
            def on_close_hide():
                _hide_window()
                try:
                    tray_win.deiconify()
                    tray_win.lift()
                except Exception:
                    pass
            ui.root.protocol("WM_DELETE_WINDOW", on_close_hide)
            has_tray = True
        except Exception as e:
            print(f"beautified tray fail: {e}")
            tray_win = None
            def on_close_exit():
                try:
                    if stream:
                        stream.stop()
                except Exception:
                    pass
                ui.root.destroy()
                sys.exit(0)
            ui.root.protocol("WM_DELETE_WINDOW", on_close_exit)

    ui.run()
    try:
        if tray:
            tray.stop()
    except Exception:
        pass

if __name__ == "__main__":
    main()
