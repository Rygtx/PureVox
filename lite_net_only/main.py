# PureVox Lite Net Only — 入口（网络输入 → 降噪 → 本地输出）
# Copyright (C) 2024-2026 a2heng <752848283@qq.com>
# SPDX-License-Identifier: GPL-3.0-or-later
#
# 零复用主线，仅复用自包含库 (onnxruntime, numpy, pyaudio, websockets, av,
# cryptography, zeroconf)。运行即启动 WSS 服务，浏览器/Android 客户端推流，
# 协议与主线一致（JSON + base64 opus + ack）

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
                    import tkinter as tk
                    r = tk.Tk()
                    r.withdraw()
                    mb.showerror("PureVox Net Lite", "PureVox 已在运行（完整版或轻量版），不可同时启动。")
                    r.destroy()
                except Exception:
                    pass
                sys.exit(0)
            return mutex
        except Exception:
            return None
    else:
        import fcntl
        path = sys.os.path.join(sys.os.path.expanduser("~"), ".purevox", "purevox.lock")
        sys.os.makedirs(sys.os.path.dirname(path), exist_ok=True)
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
                script = sys.os.path.join(sys.os.path.dirname(__file__), "main.py")
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

def _die(msgbox_title, msg):
    try:
        import tkinter.messagebox as mb
        import tkinter as tk
        r = tk.Tk()
        r.withdraw()
        mb.showerror(msgbox_title, msg)
        r.destroy()
    except Exception:
        print(msg)
    sys.exit(1)

def main():
    # 提权子进程：仅安装防火墙规则后立即退出
    # 必须在单实例互斥之前处理（主程序已持锁，UAC 子进程不能再抢）
    if len(sys.argv) >= 2 and sys.argv[1] == "--fw-install":
        import os
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import fw as _fw
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 8765
        ip = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None
        _fw._apply(port, ip)
        sys.exit(0)

    ensure_single_instance()
    import os
    sys.path.insert(0, os.path.dirname(__file__))
    from config import load, save
    import audio
    import engine
    import net as netmod

    cfg = load()
    outs = audio.list_output_devices()

    # 默认输出回退
    def _out_map(lst):
        return {n: i for n, i in lst}
    out_map = _out_map(outs)
    def resolve_out(name):
        if not name:
            vals = list(out_map.values())
            return vals[0] if vals else -1
        if name in out_map:
            return out_map[name]
        for k, v in out_map.items():
            if k.endswith(name) or name.endswith(k):
                return v
        for k, v in out_map.items():
            if name in k or k in name:
                return v
        vals = list(out_map.values())
        return vals[0] if vals else -1

    # 模型常驻
    model_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "v9_fft2048_band256_epoch_261.onnx")
    if not os.path.isfile(model_path):
        model_path = os.path.join(os.path.dirname(__file__), "v9_fft2048_band256_epoch_261.onnx")
    try:
        eng = engine.LiteDenoiseEngine(model_path)
    except Exception as e:
        _die("模型加载失败", str(e))

    # 网络解码环形缓冲 + 增益（闭包持有，UI/流共享）
    ring = netmod.JitterRing()
    gains = {"pre": audio.db_to_linear(cfg.get("pre_gain_db", 0.0)),
             "post": audio.db_to_linear(cfg.get("post_gain_db", 0.0))}

    def process_fn(chunk):
        # 网络 hop(960) 累积切出的 1024：前增益 → 引擎
        x = chunk * gains["pre"]
        return eng.process(x)

    stream = None
    def start_stream():
        nonlocal stream
        if stream:
            try:
                stream.stop()
            except Exception:
                pass
            stream = None
        out_idx = resolve_out(cfg.get("output_device", ""))
        if out_idx < 0 and not outs:
            _die("无可用输出设备", "未检测到 WASAPI 输出设备。")
            return
        stream = audio.LiteNetStream(out_idx, ring, post_db=cfg.get("post_gain_db", 0.0))
        try:
            stream.start()
        except Exception as e:
            emsg = str(e)
            try:
                import tkinter.messagebox as mb
                mb.showerror("音频启动失败", emsg)
            except Exception:
                print(emsg)
            stream = None

    # 启动即运行
    start_stream()

    # 网络服务状态回调（net 线程 → Tk 主线程）
    from ui import LiteUI
    ui_holder = {}
    def on_net_state(clients, note):
        ui = ui_holder.get("ui")
        if ui:
            ui.set_server_state(clients, note)

    port = int(cfg.get("port", 8765))
    server = netmod.NetServer(ring, port, process_fn=process_fn, on_state=on_net_state)
    mdns = netmod.MdnsPublisher(port)
    try:
        server.start()
    except Exception as e:
        _die("网络服务启动失败", str(e))
        return
    # 默认广播网卡：配置保存值 > 首个物理网卡
    networks = netmod.list_lan_ips()
    mdns.addr = cfg.get("net_ip") if cfg.get("net_ip") in [i for i, _n in networks] else (networks[0][0] if networks else None)
    mdns.start()
    # 防火墙（免管理员路径）：WSS 监听后系统会自动弹「Windows 安全中心警报」，
    # 用户点「允许访问」即由系统生成规则；这里只轮询检测并在 UI 上提示状态
    import threading as _th
    import time as _time
    import fw as fwmod

    def start_fw_watch():
        def _watch():
            ui = ui_holder.get("ui")
            deadline = _time.time() + 180
            hint1 = "请在弹出的 Windows 安全中心警报中勾选并允许"
            hint2 = "仍未放行：控制面板→防火墙→允许应用通过防火墙"
            while _time.time() < deadline:
                if fwmod.rules_present(port, mdns.addr):
                    u = ui_holder.get("ui")
                    if u:
                        try:
                            u.root.after(0, lambda: u.set_fw_hint(None))
                        except Exception:
                            pass
                    return
                u = ui_holder.get("ui")
                if u:
                    h = hint1 if _time.time() < deadline - 60 else hint2
                    try:
                        u.root.after(0, lambda hh=h: u.set_fw_hint(hh))
                    except Exception:
                        pass
                _time.sleep(5)
        _th.Thread(target=_watch, daemon=True).start()

    if not fwmod.rules_present(port, mdns.addr):
        start_fw_watch()

    def on_fw_manual():
        # 手动申请：UAC 提权安装最小规则（与自动等待系统警报互补）
        def _run():
            ok, err = fwmod.manual_install(port, mdns.addr)
            u = ui_holder.get("ui")
            if u:
                msg = None if ok else f"手动申请失败：{err}"
                try:
                    u.root.after(0, lambda: u.set_fw_hint(msg))
                except Exception:
                    pass
        _th.Thread(target=_run, daemon=True).start()

    def on_gain(which, val):
        iv = int(val)
        if which == "pre":
            cfg["pre_gain_db"] = iv
            gains["pre"] = audio.db_to_linear(iv)
        else:
            cfg["post_gain_db"] = iv
            gains["post"] = audio.db_to_linear(iv)
            if stream:
                stream.set_post_gain(iv)
        save(cfg)
        ui = ui_holder.get("ui")
        try:
            if ui:
                if which == "pre":
                    ui.pre_var.set(str(iv))
                else:
                    ui.post_var.set(str(iv))
        except Exception:
            pass

    def on_output(out_name):
        cfg["output_device"] = out_name
        save(cfg)
        start_stream()

    def on_network(ip):
        # 切换网卡：保存选择 + mDNS 换接口重注册 + 防火墙重新检测（IP 变了规则不匹配）
        cfg["net_ip"] = ip
        save(cfg)
        try:
            mdns.restart(ip)
            if not fwmod.rules_present(port, ip):
                start_fw_watch()
            else:
                ui_holder.get("ui") and ui_holder["ui"].set_fw_hint(None)
        except Exception:
            pass

    def on_autostart(enable):
        cfg["autostart"] = bool(enable)
        save(cfg)
        set_autostart(enable)

    def _hide_window():
        ui = ui_holder.get("ui")
        try:
            ui.root.withdraw()
        except Exception:
            pass
    def _show_window():
        ui = ui_holder.get("ui")
        try:
            ui.root.deiconify()
            ui.root.lift()
            ui.root.focus_force()
        except Exception:
            pass
    def _do_close():
        _hide_window()

    ui = LiteUI(cfg, outs, on_gain, on_output, on_autostart, on_close=_do_close, on_minimize=_do_close,
                networks=networks, on_network=on_network)
    ui.on_fw_manual = on_fw_manual
    ui.set_server_state(server.clients, "")
    ui_holder["ui"] = ui

    # 系统托盘（pystray，与 lite_denoise_only 同构）
    tray = None
    has_tray = False
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
                try:
                    from PIL import ImageFont
                    font_path = os.path.join(os.path.dirname(__file__), "fonts", "ark-pixel-12px-monospaced-zh_cn.ttf")
                    if os.path.isfile(font_path):
                        pf = ImageFont.truetype(font_path, 56)
                        bbox = d.textbbox((0, 0), "P", font=pf, stroke_width=3)
                        tw = bbox[2] - bbox[0]
                        th = bbox[3] - bbox[1]
                        d.text(((64 - tw) // 2, (64 - th) // 2 - 2), "P", fill="#6D4C41", font=pf, stroke_width=3, stroke_fill="#FFB74D")
                    else:
                        raise FileNotFoundError
                except Exception:
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
            def _exit(icon, item):
                try:
                    if icon:
                        icon.stop()
                except Exception:
                    pass
                try:
                    mdns.stop()
                    server.stop()
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

            def _tk(fn):
                def _run():
                    try:
                        ui.root.after(0, fn)
                    except Exception:
                        fn()
                return _run

            def _auto_checked(item):
                return bool(cfg.get("auto_zoom", True))

            zoom_items = [pystray.MenuItem(
                "自动（按分辨率）",
                lambda icon, item: _tk(ui.set_auto_zoom)(),
                checked=_auto_checked,
            )]

            def _pct_item(p):
                def _checked(item):
                    return (not cfg.get("auto_zoom", True)) and getattr(ui, "_zoom", 100) == p
                return pystray.MenuItem(
                    f"{p}%",
                    lambda icon, item: _tk(lambda: ui.set_zoom(p))(),
                    checked=_checked,
                )

            for _p in (85, 95, 100, 110, 125, 145, 175):
                zoom_items.append(_pct_item(_p))
            zoom_menu = pystray.Menu(*zoom_items)
            menu = pystray.Menu(
                pystray.MenuItem("显示主界面", _show, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("缩放比例", zoom_menu),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出", _exit),
            )
            tray = pystray.Icon("PureVox Net Lite", icon_img, "PureVox Net Lite — 运行中", menu)
            import threading as _th
            _th.Thread(target=tray.run, daemon=True).start()

            def on_close():
                _hide_window()
            ui.root.protocol("WM_DELETE_WINDOW", on_close)
            ui.root.bind("<Unmap>", lambda e: on_close() if ui.root.state() == "iconic" else None)
        except Exception as e:
            import traceback
            print("tray run fail:", e, type(e))
            traceback.print_exc()
            has_tray = False
            tray = None

    if not has_tray:
        def on_close_exit():
            try:
                mdns.stop()
                server.stop()
            except Exception:
                pass
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
