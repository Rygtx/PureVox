# PureVox Lite Net Only — 网络输入（WSS 服务器 + Opus 解码 + TUN 规避）
# Copyright (C) 2024-2026 a2heng <752848283@qq.com>
# SPDX-License-Identifier: GPL-3.0-or-later
#
# 协议与主线 PureVox 一致：
#   客户端 JSON {"type":"audio","data":"<base64 opus>","seq":N,"timestamp":T}
#   服务器 ACK {"type":"ack","seq":N}
# 帧大小 960 样本 (20ms @48kHz 单声道)

import asyncio
import base64
import json
import os
import socket
import ssl
import struct
import threading
import ctypes
import ctypes.wintypes as wt

SAMPLE_RATE = 48000
FRAME_SIZE = 960  # 20ms

CERT_DIR = os.path.join(os.path.expanduser("~"), ".purevox")
CERT_PATH = os.path.join(CERT_DIR, "net_lite_cert.pem")
KEY_PATH = os.path.join(CERT_DIR, "net_lite_key.pem")

# ---------------------------------------------------------------------------
# 本机 IP 枚举：列出全部 Up 状态网卡的 IPv4 端点（物理口在前、TUN/VPN 在后）。
# TUN/VPN 虚拟网卡不再从列表剔除（用户可在下拉里显式选择），只在
# auto_lan_ip 自动选择时规避——避免 VPN 抢占默认路由时选到连不通的地址。
# ---------------------------------------------------------------------------
_TUN_IF_TYPES = {53, 131}  # PPP, TunnelEncapsulation
_TUN_NAME_HINTS = (
    "tun", "tap", "wintun", "wireguard", "vpn", "clash", "mihomo",
    "v2ray", "sing-box", "singbox", "zerotier", "tailscale", "openvpn",
    "hamachi", "ppp", "nordlynx", "proton", "tailscale", "loon", "shadowsocks",
)

def _is_tun_name(name):
    n = (name or "").lower().replace("-", "").replace("_", "").replace(" ", "")
    return any(h in n for h in _TUN_NAME_HINTS)

def list_lan_ips():
    """返回 [(ipv4, if_name), ...]，全部 Up 网卡（剔除回环/链路本地），
    物理口在前、隧道/虚拟口在后"""
    out = []  # (ip, name, iftype)
    if sys_platform_win():
        for ip, name, iftype, oper in _win_adapters():
            if oper != 1:  # IfOperStatusUp：未连接的网卡没有可用端点
                continue
            out.append((ip, name, iftype))
    else:
        try:
            import fcntl
            import array
            # POSIX: SIOCGIFCONF 枚举接口名+地址
            buf = array.array("B", b"\0" * 8192)
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            addr, ln = buf.buffer_info()
            ifreq = struct.pack("iL", len(buf.buffer_info()[1] * 8), addr)
            fcntl.ioctl(s.fileno(), 0x8912, ifreq)  # SIOCGIFCONF
            size = struct.unpack("iL", ifreq)[0]
            data = buf.tobytes()
            for i in range(0, size, 40):
                name = data[i:i+16].split(b"\0")[0].decode("utf-8", "replace")
                ip = ".".join(str(b) for b in data[i+20:i+24])
                out.append((ip, name, None))
        except Exception:
            pass

    # 过滤回环/链路本地；排序：非 TUN 名 > 接口类型物理口(6=以太网/71=Wi-Fi) > IP。
    # 隧道类型（PPP/TunnelEncapsulation 等）与虚拟口即使名字不带 TUN 特征也沉底，
    # 避免通用名隧道口（如"以太网 2"）抢占自动选择
    def rank(item):
        ip, name, iftype = item
        tun = _is_tun_name(name) or (iftype is not None and iftype in _TUN_IF_TYPES)
        phys = 0 if iftype in (6, 71) else 1
        return (1 if tun else 0, phys, ip)

    out = [(ip, n, t) for ip, n, t in out if ip and not ip.startswith(("127.", "169.254."))]
    seen = set()
    uniq = []
    for ip, n, _t in sorted(out, key=rank):
        if ip not in seen:
            seen.add(ip)
            uniq.append((ip, n))
    return uniq

def best_lan_ip(ips=None):
    """自动选择最优网卡 IP：首个非 TUN/VPN 物理口（列表已按物理口优先排序）；
    全是虚拟口时取首项，再兜底 UDP 出口路由（可能被 TUN 抢走，仅最后手段）"""
    pairs = ips if ips is not None else list_lan_ips()
    for ip, name in pairs:
        if not _is_tun_name(name):
            return ip
    if pairs:
        return pairs[0][0]
    # 兜底：UDP 出口路由（可能被 TUN 抢走，仅最后手段）
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    return "127.0.0.1"

def sys_platform_win():
    import sys
    return sys.platform.startswith("win")

def html_dir():
    """浏览器页面目录：开发态 = 仓库根 html/；打包后 = _internal/html"""
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(os.path.dirname(here), "html"),
                 os.path.join(here, "html")):
        if os.path.isdir(cand):
            return cand
    return None

# Windows GetAdaptersAddresses (AF_INET, 无额外依赖)
def _win_adapters():
    class SOCKADDR(ctypes.Structure):
        _fields_ = [("sa_family", wt.USHORT), ("sa_data", ctypes.c_byte * 14)]
    class SOCKET_ADDRESS(ctypes.Structure):
        _fields_ = [("lpSockaddr", ctypes.c_void_p), ("iSockaddrLength", ctypes.c_int)]
    class IP_ADAPTER_UNICAST_ADDRESS(ctypes.Structure):
        class _u(ctypes.Union):
            _fields_ = [("Alignment", ctypes.c_ulonglong)]
        _fields_ = [("_u", _u),
                    ("Next", ctypes.c_void_p),
                    ("Address", SOCKET_ADDRESS),
                    ("PrefixOrigin", ctypes.c_int), ("SuffixOrigin", ctypes.c_int),
                    ("DadState", ctypes.c_int), ("ValidLifetime", wt.ULONG),
                    ("PreferredLifetime", wt.ULONG), ("LeaseLifetime", wt.ULONG),
                    ("OnLinkPrefixLength", ctypes.c_ubyte)]
    class IP_ADAPTER_ADDRESSES(ctypes.Structure):
        class _u(ctypes.Union):
            # Alignment 联合体内含 Length + IfIndex（勿再单独声明 IfIndex）
            _fields_ = [("Alignment", ctypes.c_ulonglong)]
        _fields_ = [("_u", _u),
                    ("Next", ctypes.c_void_p),
                    ("AdapterName", ctypes.c_char_p), ("FirstUnicastAddress", ctypes.c_void_p),
                    ("FirstAnycastAddress", ctypes.c_void_p), ("FirstMulticastAddress", ctypes.c_void_p),
                    ("FirstDnsServerAddress", ctypes.c_void_p), ("DnsSuffix", ctypes.c_wchar_p),
                    ("Description", ctypes.c_wchar_p), ("FriendlyName", ctypes.c_wchar_p),
                    ("PhysicalAddress", ctypes.c_byte * 8), ("PhysicalAddressLength", wt.ULONG),
                    ("Flags", wt.DWORD), ("Mtu", wt.ULONG), ("IfType", wt.DWORD),
                    ("OperStatus", ctypes.c_int)]
    GAA_FLAG_SKIP_ANYCAST = 2
    GAA_FLAG_SKIP_MULTICAST = 4
    AF_INET = 2
    out = []
    try:
        iphlpapi = ctypes.windll.iphlpapi
        iphlpapi.GetAdaptersAddresses.argtypes = [
            wt.ULONG, wt.ULONG, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(wt.ULONG)]
        iphlpapi.GetAdaptersAddresses.restype = wt.ULONG
        size = wt.ULONG(16384)
        buf = None
        for _ in range(3):
            buf = ctypes.create_string_buffer(size.value)
            r = iphlpapi.GetAdaptersAddresses(
                AF_INET, GAA_FLAG_SKIP_ANYCAST | GAA_FLAG_SKIP_MULTICAST,
                None, ctypes.byref(buf), ctypes.byref(size))
            if r == 0:
                break
            if r != 111:  # ERROR_BUFFER_OVERFLOW
                return out
        else:
            return out
        addr = ctypes.cast(ctypes.byref(buf), ctypes.POINTER(IP_ADAPTER_ADDRESSES))
        while addr:
            a = addr.contents
            uni = a.FirstUnicastAddress
            while uni:
                u = ctypes.cast(uni, ctypes.POINTER(IP_ADAPTER_UNICAST_ADDRESS)).contents
                if u.Address.lpSockaddr:
                    sa = ctypes.cast(u.Address.lpSockaddr, ctypes.POINTER(SOCKADDR)).contents
                    if sa.sa_family == AF_INET:
                        # sockaddr_in: family(2B) + port(2B) + addr(4B)，sa_data 从 port 起
                        ip = socket.inet_ntoa(bytes(b & 0xFF for b in sa.sa_data[2:6]))
                        out.append((ip, a.FriendlyName or "", a.IfType, a.OperStatus))
                uni = u.Next
            if not a.Next:
                break
            addr = ctypes.cast(a.Next, ctypes.POINTER(IP_ADAPTER_ADDRESSES))
        return out
    except Exception:
        return out


# ---------------------------------------------------------------------------
# 自签证书（cryptography 生成一次，~/.purevox 复用）
# ---------------------------------------------------------------------------
def cert_covers_current_ips():
    """现有证书 SAN 是否已覆盖全部当前网卡 IP（切换网络后需重签）"""
    if not (os.path.isfile(CERT_PATH) and os.path.isfile(KEY_PATH)):
        return False
    try:
        import ipaddress
        from cryptography import x509
        with open(CERT_PATH, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
        try:
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        except x509.ExtensionNotFound:
            return False
        # 显式遍历（新版本 cryptography 的 get_values_for_type 对 IPv4 实测返回空）
        have = {str(g.value) for g in san
                if isinstance(g, x509.IPAddress) and isinstance(g.value, ipaddress.IPv4Address)}
        need = {ip for ip, _n in list_lan_ips()}
        return need.issubset(have)
    except Exception:
        return False


def ensure_tls_cert():
    """证书缺失或 SAN 未覆盖当前网卡 IP 时（重新）生成，否则复用"""
    if cert_covers_current_ips():
        return True, ""
    try:
        import datetime
        import ipaddress
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "PureVox Net Lite")])
        san = [x509.DNSName("localhost")]
        for ip, _n in list_lan_ips():
            try:
                san.append(x509.IPAddress(ipaddress.IPv4Address(ip)))
            except Exception:
                pass
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder()
                .subject_name(subject).issuer_name(subject)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(days=1))
                .not_valid_after(now + datetime.timedelta(days=3650))
                .add_extension(x509.SubjectAlternativeName(san), critical=False)
                .sign(key, hashes.SHA256()))
        os.makedirs(CERT_DIR, exist_ok=True)
        with open(KEY_PATH, "wb") as f:
            f.write(key.private_bytes(serialization.Encoding.PEM,
                                      serialization.PrivateFormat.TraditionalOpenSSL,
                                      serialization.NoEncryption()))
        with open(CERT_PATH, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
        return True, ""
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# Opus 解码（PyAV）
# ---------------------------------------------------------------------------
class NetOpusDecoder:
    def __init__(self):
        self._dec = None
        self._av = None
        self._rs = None
        try:
            import av
            self._av = av
            try:
                self._dec = av.CodecContext.create("libopus", "r")
            except Exception:
                self._dec = av.CodecContext.create("opus", "r")
            # 统一重采样到 f32/单声道/48k：不依赖解码器输出的声道布局与格式
            self._rs = av.AudioResampler(format="fltp", layout="mono", rate=SAMPLE_RATE)
        except Exception:
            self._dec = None

    def decode_f32_mono(self, opus_bytes):
        """返回 np.float32 mono 任意长度（正常 960）；失败返回 None"""
        if self._dec is None or not opus_bytes:
            return None
        try:
            pkt = self._av.Packet(opus_bytes)
            frames = self._dec.decode(pkt)
            # 解码器输出可能是 s16 整型（直接 astype 成 float 会幅值爆炸=纯削波噪声），
            # 一律经重采样器统一到 f32/单声道/48k；resample 只收单帧
            if self._rs is not None and frames:
                frames = [of for fr in frames for of in self._rs.resample(fr)]
        except Exception:
            return None
        chunks = []
        for fr in frames or []:
            try:
                arr = fr.to_ndarray()  # fltp: (ch, n) float32
            except Exception:
                continue
            if arr.ndim == 2:
                arr = arr.mean(axis=0)
            chunks.append(arr.reshape(-1).astype("float32"))
        if not chunks:
            return None
        import numpy as np
        return np.concatenate(chunks) if len(chunks) > 1 else chunks[0]


# ---------------------------------------------------------------------------
# SPSC 环形缓冲（生产者=网络解码线程，消费者=PortAudio 回调）
# 满丢新（同主线网络模式输出环），欠载由消费端静音补齐并重新预填充
# ---------------------------------------------------------------------------
class JitterRing:
    def __init__(self, capacity=SAMPLE_RATE * 2, prefill=FRAME_SIZE * 5):
        # 预填充 100ms：TCP 到达是突发的，预填充太小会频繁欠载（爆音/断续）
        import numpy as np
        self._np = np
        self.capacity = capacity
        self.prefill = prefill
        self.buf = np.zeros(capacity, dtype=np.float32)
        self.w = 0  # 写计数（单调递增）
        self.r = 0  # 读计数
        self.primed = False

    def available(self):
        return self.w - self.r

    def write(self, samples):
        np = self._np
        n = len(samples)
        free = self.capacity - self.available()
        if n > free:
            return False  # 满丢新
        pos = self.w % self.capacity
        first = min(n, self.capacity - pos)
        self.buf[pos:pos+first] = samples[:first]
        if n > first:
            self.buf[:n-first] = samples[first:]
        self.w += n
        if not self.primed and self.available() >= self.prefill:
            self.primed = True
        return True

    def read(self, n):
        """读 n 个样本；不足则补零、清预填充门（欠载重填）"""
        np = self._np
        have = min(n, self.available())
        if self.primed and have < n:
            self.primed = False
        if not self.primed:
            return np.zeros(n, dtype=np.float32)
        pos = self.r % self.capacity
        first = min(have, self.capacity - pos)
        out = np.empty(n, dtype=np.float32)
        out[:have] = self.buf[pos:pos+first]
        if have > first:
            out[:have][first:] = self.buf[:have-first]
        out[have:] = 0.0
        self.r += have
        return out


# ---------------------------------------------------------------------------
# WSS 服务器（websockets 库，asyncio 后台线程）
# ---------------------------------------------------------------------------
class NetServer:
    def __init__(self, ring, port, process_fn=None, on_state=None):
        self.ring = ring
        self.port = int(port)
        # 可选处理钩子：f32 mono [1024] -> f32 mono [1024]（降噪引擎）
        self.process_fn = process_fn
        self.hop = 1024
        self.on_state = on_state  # on_state(clients, note)
        # 活跃连接表：ws -> 最后一次收到音频的单调时间。
        # 客户端断网/重载页面会残留半开连接（等 ping 超时才关闭），
        # 按「10 秒内有消息」计数才与真实使用人数一致
        self._conns = {}
        self._last_clients = 0
        self._loop = None
        self._server = None
        self._ctx = None
        self._thread = None
        self._stop_evt = threading.Event()

    @property
    def clients(self):
        import time as _t
        now = _t.monotonic()
        return sum(1 for ts in list(self._conns.values()) if now - ts < 10.0)

    def start(self):
        ok, err = ensure_tls_cert()
        if not ok:
            raise RuntimeError(f"证书生成失败: {err}")
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def restart(self):
        """手动重启 WSS 服务（UI 重启按钮）：停旧监听 → 清连接状态 → 重开"""
        self.stop()
        self._conns.clear()
        self._last_clients = 0
        self._stop_evt.clear()
        self.start()

    def stop(self):
        self._stop_evt.set()
        if self._loop and self._server:
            async def _close():
                self._server.close()
                await self._server.wait_closed()
            try:
                fut = asyncio.run_coroutine_threadsafe(_close(), self._loop)
                fut.result(timeout=3)
            except Exception:
                pass

    def reload_cert(self):
        """重签证书后热加载（load_cert_chain 可重复调用替换证书链，
        只影响新握手，已有连接不断）。可在任意线程调用。"""
        if not self._loop or not self._ctx:
            return
        try:
            self._loop.call_soon_threadsafe(self._ctx.load_cert_chain, CERT_PATH, KEY_PATH)
        except Exception:
            pass

    def _run(self):
        try:
            asyncio.run(self._main())
        except Exception as e:
            if self.on_state:
                try:
                    self.on_state(-1, str(e))
                except Exception:
                    pass

    async def _main(self):
        import websockets
        from websockets.http11 import Response
        self._loop = asyncio.get_running_loop()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(CERT_PATH, KEY_PATH)
        self._ctx = ctx

        # ---- 同端口 HTTP：/api/status + html/ 静态页（浏览器客户端同源接入）----
        _CTYPES = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".wasm": "application/wasm",
            ".png": "image/png", ".ico": "image/x-icon",
        }

        def _response(connection, status, body, ctype):
            # 必须经 connection.respond 构造（直接 new Response 不会发送）；
            # Headers 无 remove 且 __setitem__ 是追加语义，改头前须先 del 防重复
            r = connection.respond(status, "")
            r.body = body
            for k in ("Content-Type", "Content-Length"):
                try:
                    del r.headers[k]
                except KeyError:
                    pass
            r.headers["Content-Type"] = ctype
            r.headers["Content-Length"] = str(len(body))
            return r

        def _serve_file(connection, path):
            root = html_dir()
            if not root:
                return _response(connection, 404, b"no page", "text/plain")
            rel = "index.html" if path in ("/", "") else path.lstrip("/")
            full = os.path.normpath(os.path.join(root, rel))
            if not full.startswith(os.path.normpath(root)) or not os.path.isfile(full):
                return _response(connection, 404, b"not found", "text/plain")
            ext = os.path.splitext(full)[1].lower()
            with open(full, "rb") as f:
                return _response(connection, 200, f.read(), _CTYPES.get(ext, "application/octet-stream"))

        async def process_request(connection, request):
            path = request.path.split("?")[0]
            if path == "/api/status":
                return _response(connection, 200,
                                 json.dumps({"sample_rate": SAMPLE_RATE,
                                             "active_clients": self.clients}).encode(),
                                 "application/json")
            if request.headers.get("Upgrade", "").lower() == "websocket":
                return None  # 交给 WS 握手（任意路径，含 /ws/audio）
            if path in ("/", "/index.html", "/js/app.js", "/js/ws-client.js", "/js/audio-capture.js",
                        "/js/opus-encoder.js", "/js/discovery.js", "/css/style.css",
                        "/wasm/libopus-encoder.wasm.min.wasm", "/wasm/libopus-encoder.wasm.min.js",
                        "/wasm/opus-encoder-worker.js"):
                return _serve_file(connection, path)
            return None  # 其余路径仍允许 WS 握手（兼容旧客户端直连根路径）

        async def handler(ws):
            import time as _t
            self._conns[ws] = _t.monotonic()
            self._notify("")
            dec = NetOpusDecoder()
            import numpy as np
            acc = np.zeros(0, dtype=np.float32)
            try:
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    if msg.get("type") != "audio":
                        continue
                    self._conns[ws] = _t.monotonic()
                    seq = msg.get("seq", 0)
                    pcm = dec.decode_f32_mono(base64.b64decode(msg.get("data", "")))
                    if pcm is not None and len(pcm):
                        if self.process_fn is None:
                            self.ring.write(pcm)
                        else:
                            # 引擎按 1024 hop 处理：累积网络帧（960）后逐 hop 切
                            acc = np.concatenate([acc, pcm])
                            while acc.shape[0] >= self.hop:
                                out = self.process_fn(acc[:self.hop])
                                if out is not None and len(out):
                                    self.ring.write(out)
                                acc = acc[self.hop:]
                    try:
                        await ws.send(json.dumps({"type": "ack", "seq": seq}))
                    except Exception:
                        break
            except Exception:
                pass
            finally:
                self._conns.pop(ws, None)
                self._notify("")

        self._server = await websockets.serve(
            handler, "0.0.0.0", self.port, ssl=ctx,
            process_request=process_request,
            max_size=1 << 20, ping_interval=20, ping_timeout=20)
        self._notify("")
        # 周期清理僵尸连接并刷新客户端计数（半开连接等 ping 超时才关闭）
        while not self._stop_evt.is_set():
            await asyncio.sleep(1.0)
            import time as _t
            now = _t.monotonic()
            stale = [w for w, ts in self._conns.items() if now - ts > 12.0]
            for w in stale:
                self._conns.pop(w, None)
                try:
                    await w.close(code=1001)
                except Exception:
                    pass
            if len(self._conns) != self._last_clients:
                self._last_clients = len(self._conns)
                self._notify("")

    def _notify(self, note):
        if self.on_state:
            try:
                self.on_state(self.clients, note)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# mDNS 广播（zeroconf，_purevox._tcp.local. 与主线协议一致）
# ---------------------------------------------------------------------------
class MdnsPublisher:
    def __init__(self, port):
        self.port = int(port)
        self.addr = None  # 指定广播的网卡 IP；None = 全部物理网卡
        self._zc = None
        self._info = None

    def start(self):
        try:
            import zeroconf
            from zeroconf import ServiceInfo
        except Exception:
            return False
        try:
            ips = [self.addr] if self.addr else [a for a, n in list_lan_ips() if not _is_tun_name(n)]
            addrs = [socket.inet_aton(a) for a in ips if a]
            if not addrs:
                return False
            hostname = socket.gethostname().split(".")[0] or "purevox"
            # Zeroconf.interfaces 只认字符串/整数形式的 IP（传 inet_aton 字节会导致
            # 注册静默失效，本地浏览都发现不了）；ServiceInfo.addresses 才是 packed bytes
            self._zc = zeroconf.Zeroconf(interfaces=[a for a in ips if a])
            self._info = ServiceInfo(
                "_purevox._tcp.local.",
                f"PureVox Net Lite ({hostname})._purevox._tcp.local.",
                addresses=addrs,
                port=self.port,
                properties={"path": "/", "tls": "wss"},
                server=f"{hostname}.local.",
            )
            self._zc.register_service(self._info, ttl=None, allow_name_change=True)
            return True
        except Exception:
            self.stop()
            self._zc = None
            return False

    def restart(self, addr=None):
        """换网卡重注册（UI 下拉切换时调用）"""
        self.addr = addr
        self.stop()
        return self.start()

    def stop(self):
        try:
            if self._zc and self._info:
                self._zc.unregister_service(self._info)
        except Exception:
            pass
        try:
            if self._zc:
                self._zc.close()
        except Exception:
            pass
        self._zc = None
        self._info = None
