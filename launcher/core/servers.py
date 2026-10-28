"""服务器列表与状态查询（Server List Ping）。

* 服务器保存在配置里（`servers` 段），支持备注名、地址、版本与「一键进入」；
* `ping()` 实现 1.7+ 的握手协议，能在列表里直接看到 MOTD、在线人数与延迟，
  无需先启动游戏才知道服务器是否活着。
"""

from __future__ import annotations

import json
import socket
import struct
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .utils import LOG

DEFAULT_PORT = 25565
# 协议号只用于握手，服务端一般不看这个值，用 1.20.1 的 763 即可兼容绝大多数服务端
HANDSHAKE_PROTOCOL = 763


@dataclass
class ServerInfo:
    """一条服务器记录。"""

    name: str
    address: str            # 域名或 IP
    port: int = DEFAULT_PORT
    note: str = ""
    version_hint: str = ""
    last_used: str = ""
    favorite: bool = False

    @property
    def host_port(self) -> str:
        return f"{self.address}:{self.port}"

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "address": self.address,
            "port": self.port,
            "note": self.note,
            "version_hint": self.version_hint,
            "last_used": self.last_used,
            "favorite": self.favorite,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ServerInfo":
        port = data.get("port", DEFAULT_PORT)
        try:
            port = int(port)
        except (TypeError, ValueError):
            port = DEFAULT_PORT
        return cls(
            name=str(data.get("name") or data.get("address") or "服务器"),
            address=str(data.get("address") or ""),
            port=port,
            note=str(data.get("note") or ""),
            version_hint=str(data.get("version_hint") or ""),
            last_used=str(data.get("last_used") or ""),
            favorite=bool(data.get("favorite", False)),
        )


@dataclass
class ServerStatus:
    """Ping 的结果。"""

    online: bool
    latency_ms: int = 0
    motd: str = ""
    version: str = ""
    protocol: int = 0
    players_online: int = 0
    players_max: int = 0
    sample: List[str] = field(default_factory=list)
    error: str = ""

    @property
    def players_text(self) -> str:
        return f"{self.players_online}/{self.players_max}" if self.online else "-"


def split_address(text: str) -> Tuple[str, int]:
    """把 `host`、`host:port` 拆成 (host, port)；支持 IPv6 的 `[::1]:25565`。"""
    text = (text or "").strip()
    if not text:
        return "", DEFAULT_PORT
    if text.startswith("["):
        end = text.find("]")
        if end > 0:
            host = text[1:end]
            rest = text[end + 1:]
            if rest.startswith(":"):
                try:
                    return host, int(rest[1:])
                except ValueError:
                    return host, DEFAULT_PORT
            return host, DEFAULT_PORT
    if ":" in text:
        host, _, port = text.rpartition(":")
        try:
            return host, int(port)
        except ValueError:
            return text, DEFAULT_PORT
    return text, DEFAULT_PORT


def _write_varint(value: int) -> bytes:
    out = b""
    while True:
        temp = value & 0x7F
        value >>= 7
        if value:
            out += bytes([temp | 0x80])
        else:
            out += bytes([temp])
            return out


def _read_varint(sock: socket.socket, timeout: float = 5.0) -> Tuple[int, int]:
    """返回 (值, 已读字节数)；读不到就抛错。"""
    num = 0
    for i in range(5):
        data = sock.recv(1)
        if not data:
            raise ConnectionError("连接被关闭")
        byte = data[0]
        num |= (byte & 0x7F) << (7 * i)
        if not byte & 0x80:
            return num, i + 1
    raise ValueError("VarInt 过长")


def ping(address: str, port: int = DEFAULT_PORT, timeout: float = 5.0) -> ServerStatus:
    """查询服务器状态；任何失败都返回 `online=False` 的 `ServerStatus` 而不是抛出。"""
    host = address
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        return ServerStatus(False, error=f"无法解析地址：{exc}")

    last_error = ""
    for family, kind, proto, _canon, addr in infos[:3]:
        start = time.time()
        sock = None
        try:
            sock = socket.socket(family, kind, proto)
            sock.settimeout(timeout)
            sock.connect(addr)

            payload = _write_varint(HANDSHAKE_PROTOCOL)
            payload += _write_varint(len(host)) + host.encode("utf-8")
            payload += struct.pack(">H", port)
            payload += _write_varint(1)          # next state: status
            packet = _write_varint(len(payload)) + payload
            sock.sendall(packet + _write_varint(0) + b"\x00")

            length, _ = _read_varint(sock, timeout)
            raw = b""
            while len(raw) < length:
                chunk = sock.recv(min(4096, length - len(raw)))
                if not chunk:
                    break
                raw += chunk
            latency = int((time.time() - start) * 1000)

            # 外层长度已在上面读掉，这里剩下的是：包ID(0) + JSON 长度 + JSON
            packet_id, n = _read_varint_from(raw, 0)
            idx = n
            json_len, n = _read_varint_from(raw, idx)
            idx += n
            if packet_id != 0:
                raise ValueError(f"意外的响应包类型：{packet_id}")
            body = raw[idx:idx + json_len].decode("utf-8", errors="replace")
            data = json.loads(body)
            return _status_from_json(data, latency)
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
    return ServerStatus(False, error=last_error or "无法连接")


def _read_varint_from(raw: bytes, idx: int) -> Tuple[int, int]:
    num = 0
    for i in range(5):
        if idx + i >= len(raw):
            raise ValueError("数据不完整")
        byte = raw[idx + i]
        num |= (byte & 0x7F) << (7 * i)
        if not byte & 0x80:
            return num, i + 1
    raise ValueError("VarInt 过长")


def _status_from_json(data: dict, latency: int) -> ServerStatus:
    motd = ""
    desc = data.get("description")
    if isinstance(desc, str):
        motd = desc
    elif isinstance(desc, dict):
        motd = str(desc.get("text") or "")
        for extra in desc.get("extra") or []:
            if isinstance(extra, dict) and extra.get("text"):
                motd += str(extra["text"])
            elif isinstance(extra, str):
                motd += extra
    version = str((data.get("version") or {}).get("name") or "")
    protocol = int((data.get("version") or {}).get("protocol") or 0)
    players = data.get("players") or {}
    sample: List[str] = []
    for item in players.get("sample") or []:
        if isinstance(item, dict) and item.get("name"):
            sample.append(str(item["name"]))
    return ServerStatus(
        online=True,
        latency_ms=latency,
        motd=motd.replace("\n", " ").strip()[:120],
        version=version,
        protocol=protocol,
        players_online=int(players.get("online") or 0),
        players_max=int(players.get("max") or 0),
        sample=sample[:8],
    )


# -------------------------------------------------------------------- 存储


def load_servers(config) -> List[ServerInfo]:
    """从配置里读取服务器列表。"""
    raw = config.get("servers", []) if hasattr(config, "get") else []
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if isinstance(item, dict):
            try:
                out.append(ServerInfo.from_dict(item))
            except Exception as exc:  # noqa: BLE001
                LOG.debug("跳过损坏的服务器记录：%s", exc)
    return out


def save_servers(config, servers: List[ServerInfo]) -> None:
    config.set("servers", [s.to_dict() for s in servers])


def add_server(config, info: ServerInfo) -> List[ServerInfo]:
    servers = load_servers(config)
    servers = [s for s in servers if s.host_port != info.host_port]
    servers.insert(0, info)
    save_servers(config, servers)
    return servers


def remove_server(config, host_port: str) -> List[ServerInfo]:
    servers = [s for s in load_servers(config) if s.host_port != host_port]
    save_servers(config, servers)
    return servers


def touch_server(config, host_port: str) -> None:
    """记录最近进入时间。"""
    servers = load_servers(config)
    stamp = time.strftime("%Y-%m-%d %H:%M")
    for s in servers:
        if s.host_port == host_port:
            s.last_used = stamp
    save_servers(config, servers)


__all__ = [
    "DEFAULT_PORT",
    "ServerInfo",
    "ServerStatus",
    "add_server",
    "load_servers",
    "ping",
    "remove_server",
    "save_servers",
    "split_address",
    "touch_server",
]
