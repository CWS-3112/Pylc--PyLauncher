"""局域网联机与跨网联机。

**局域网**：《我的世界》的「开放到局域网」会通过组播 `224.0.2.60:4445` 周期性广播
`[MOTD]世界名[/MOTD][AD]端口号[/AD]`，这里按同一协议监听即可发现同一网段内的房间，
再用 `--server <ip> --port <port>` 启动游戏直接进入。

**跨网**：没有公网 IP 时无法直连，因此提供两种可落地的方案：
* **直连**：对方有公网 IP / 端口映射时，直接填 `host:port`；
* **内置中继**：房主在本机跑一个 TCP 转发（把游戏端口映射到中继端口），
  其他人连中继地址即可进入同一个世界。联机码只是把 `host/port/token`
  打包成一段短字符串，方便口头或聊天工具传递。

中继只转发 TCP 字节流，不解析内容；连接需携带房主设置的口令（token），
避免被陌生人扫到后随意接入。
"""

from __future__ import annotations

import base64
import json
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .utils import LOG

LAN_MULTICAST_GROUP = "224.0.2.60"
LAN_MULTICAST_PORT = 4445
LAN_ANNOUNCE_TTL = 4

# 中继相关默认值
RELAY_DEFAULT_PORT = 35565
RELAY_BUFFER = 65536
RELAY_MAX_CLIENTS = 8


@dataclass
class LanWorld:
    """一个被发现的局域网房间。"""

    motd: str
    address: str
    port: int
    seen_at: float = field(default_factory=time.time)

    @property
    def age(self) -> float:
        return time.time() - self.seen_at

    @property
    def host_port(self) -> str:
        return f"{self.address}:{self.port}"


def local_ip() -> str:
    """本机在局域网里的 IP（用 UDP 探测，不会真的发包）。"""
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def _parse_announce(payload: bytes, address: str) -> Optional[LanWorld]:
    """解析 `[MOTD]...[/MOTD][AD]port[/AD]`。"""
    try:
        text = payload.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return None
    motd = ""
    port = 0
    if "[MOTD]" in text and "[/MOTD]" in text:
        motd = text.split("[MOTD]", 1)[1].split("[/MOTD]", 1)[0]
    if "[AD]" in text and "[/AD]" in text:
        try:
            port = int(text.split("[AD]", 1)[1].split("[/AD]", 1)[0])
        except ValueError:
            return None
    if not port:
        return None
    return LanWorld(motd=motd or "局域网世界", address=address, port=port)


class LanScanner:
    """监听局域网组播，收集可被进入的世界。"""

    def __init__(self, timeout: float = 1.0) -> None:
        self.timeout = timeout
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._worlds: Dict[str, LanWorld] = {}
        self._lock = threading.Lock()

    @property
    def worlds(self) -> List[LanWorld]:
        with self._lock:
            return sorted(self._worlds.values(), key=lambda w: w.seen_at, reverse=True)

    def start(self, on_found: Optional[Callable[[LanWorld], None]] = None) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(on_found,), daemon=True, name="lan-scanner"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None

    def _run(self, on_found) -> None:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("", LAN_MULTICAST_PORT))
            except OSError:
                # 端口被占用（多半是游戏自己开着），改用随机端口也能收到广播
                sock.bind(("", 0))
            try:
                sock.setsockopt(
                    socket.IPPROTO_IP,
                    socket.IP_ADD_MEMBERSHIP,
                    struct.pack("4sl", socket.inet_aton(LAN_MULTICAST_GROUP), socket.INADDR_ANY),
                )
            except OSError as exc:
                LOG.debug("加入局域网组播失败：%s", exc)
            sock.settimeout(self.timeout)

            while not self._stop.is_set():
                try:
                    data, addr = sock.recvfrom(2048)
                except socket.timeout:
                    continue
                except OSError:
                    break
                world = _parse_announce(data, addr[0] if addr else "")
                if world is None:
                    continue
                key = world.host_port
                with self._lock:
                    fresh = key not in self._worlds
                    self._worlds[key] = world
                if fresh and on_found:
                    try:
                        on_found(world)
                    except Exception:  # noqa: BLE001
                        pass
        except Exception as exc:  # noqa: BLE001
            LOG.debug("局域网扫描异常：%s", exc)
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    def prune(self, max_age: float = 60.0) -> None:
        """丢掉超过 `max_age` 秒没再广播的房间（房主已关闭）。"""
        with self._lock:
            for key in [k for k, w in self._worlds.items() if w.age > max_age]:
                self._worlds.pop(key, None)


# -------------------------------------------------------------------- 联机码


def make_join_code(token: str, host: str, port: int) -> str:
    """把连接信息打包成便于口述的联机码（Base32，无易混字符）。"""
    payload = json.dumps({"t": token, "h": host, "p": port}, separators=(",", ":"))
    raw = base64.b32encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
    return "-".join(raw[i:i + 4] for i in range(0, len(raw), 4))


def parse_join_code(code: str) -> Tuple[str, str, int]:
    """解析联机码；格式不对时抛 ValueError。"""
    raw = "".join(ch for ch in (code or "").upper() if ch.isalnum())
    if not raw:
        raise ValueError("联机码为空")
    pad = "=" * ((8 - len(raw) % 8) % 8)
    try:
        data = json.loads(base64.b32decode(raw + pad).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ValueError("联机码格式不正确") from exc
    token = str(data.get("t") or "")
    host = str(data.get("h") or "")
    try:
        port = int(data.get("p") or 0)
    except (TypeError, ValueError):
        port = 0
    if not host or not port:
        raise ValueError("联机码缺少地址或端口")
    return token, host, port


# -------------------------------------------------------------------- 中继


class RelayServer:
    """把本地游戏端口通过中继端口暴露给异地好友。

    每个连接先读一行 `token`，不匹配立即断开，避免中继被当作公开代理滥用。
    """

    def __init__(self, listen_port: int, target_host: str, target_port: int, token: str) -> None:
        self.listen_port = listen_port
        self.target_host = target_host
        self.target_port = target_port
        self.token = token
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._server: Optional[socket.socket] = None
        self.clients = 0
        self.error = ""

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        try:
            self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server.bind(("", self.listen_port))
            self._server.listen(RELAY_MAX_CLIENTS)
        except OSError as exc:
            self.error = str(exc)
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._accept_loop, daemon=True, name="relay")
        self._thread.start()
        LOG.info("联机中继已启动：0.0.0.0:%s -> %s:%s", self.listen_port, self.target_host, self.target_port)
        return True

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._server:
                self._server.close()
        except OSError:
            pass
        self._server = None

    def _accept_loop(self) -> None:
        while not self._stop.is_set() and self._server is not None:
            try:
                client, _addr = self._server.accept()
            except OSError:
                break
            if self.clients >= RELAY_MAX_CLIENTS:
                try:
                    client.close()
                except OSError:
                    pass
                continue
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    def _serve(self, client: socket.socket) -> None:
        self.clients += 1
        upstream = None
        try:
            client.settimeout(10)
            line = b""
            while b"\n" not in line and len(line) < 512:
                chunk = client.recv(1)
                if not chunk:
                    raise ConnectionError("客户端断开")
                line += chunk
            if line.decode("utf-8", errors="ignore").strip() != self.token:
                LOG.warning("中继收到错误口令，已拒绝连接")
                return
            upstream = socket.create_connection((self.target_host, self.target_port), timeout=10)
            client.settimeout(None)
            upstream.settimeout(None)
            self._pump(client, upstream)
        except Exception as exc:  # noqa: BLE001
            LOG.debug("中继连接结束：%s", exc)
        finally:
            for sock in (client, upstream):
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
            self.clients -= 1

    @staticmethod
    def _pump(a: socket.socket, b: socket.socket) -> None:
        """双向转发字节流。"""
        def pipe(src, dst):
            try:
                while True:
                    data = src.recv(RELAY_BUFFER)
                    if not data:
                        break
                    dst.sendall(data)
            except OSError:
                pass
            finally:
                try:
                    dst.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

        t = threading.Thread(target=pipe, args=(a, b), daemon=True)
        t.start()
        pipe(b, a)
        t.join(timeout=5)


# -------------------------------------------------------------------- 启动参数


def join_arguments(host: str, port: int) -> List[str]:
    """生成「直接进入服务器」的游戏参数。"""
    return ["--server", host, "--port", str(port)]


__all__ = [
    "LAN_MULTICAST_PORT",
    "LanScanner",
    "LanWorld",
    "RELAY_DEFAULT_PORT",
    "RelayServer",
    "join_arguments",
    "local_ip",
    "make_join_code",
    "parse_join_code",
]
