"""软件防火墙：所有对外网络请求与写盘行为都要先过这一层。

启动器会从多个远端拉取文件（Mojang / BMCLAPI / Modrinth / Fabric / Forge），
也会把用户从网上下载的压缩包写进游戏目录。没有约束时风险包括：

* 恶意或被劫持的接口返回一个 `file://` / 内网地址，导致 SSRF 式访问；
* 伪造的下载响应把文件写到游戏目录之外（路径穿越）；
* 超大响应耗尽磁盘；
* 明文 HTTP 被中间人篡改。

`NetGuard` 把这些约束集中到一处：`http.py` 与新增的模组库在发起请求前调用
`guard.check_url()`，写到磁盘前调用 `guard.check_download()`。
"""

from __future__ import annotations

import ipaddress
import socket
from pathlib import Path
from typing import Iterable, Optional, Tuple
from urllib.parse import urlparse

from .utils import LOG

# 允许访问的域名后缀（子域自动放行）
DEFAULT_ALLOWED_HOSTS = (
    # Mojang / Minecraft 官方
    "mojang.com",
    "minecraft.net",
    "minecraftservices.com",
    "xboxlive.com",
    "live.com",
    "microsoft.com",
    "microsoftonline.com",
    # 镜像
    "bangbang93.com",
    "bmclapi.com",
    # 模组与加载器
    "modrinth.com",
    "curseforge.com",
    "fabricmc.net",
    "minecraftforge.net",
    "neoforged.net",
    "maven.fabricmc.net",
    "files.minecraftforge.net",
    # 代码托管（部分库的镜像托管在这）
    "github.com",
    "githubusercontent.com",
)

# 单个下载的体积上限（默认 1GB，足够最大的整合包）
MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024


class NetGuardError(Exception):
    """被防火墙拒绝。"""


class NetGuard:
    """出站请求的白名单与约束。"""

    def __init__(
        self,
        allowed_hosts: Iterable[str] = DEFAULT_ALLOWED_HOSTS,
        max_bytes: int = MAX_DOWNLOAD_BYTES,
        allow_private: bool = False,
        extra_hosts: Iterable[str] = (),
    ) -> None:
        self.allowed = tuple(h.lower() for h in allowed_hosts)
        self.extra = {h.lower() for h in extra_hosts}
        self.max_bytes = max_bytes
        self.allow_private = allow_private
        # 预先解析白名单，避免被 DNS  rebinding 绕过的场景（尽力而为）
        self._host_cache: dict = {}

    # ---------------------------------------------------------- 配置

    def add_host(self, host: str) -> None:
        """放行用户手动添加的主机（例如自建镜像 / 私有模组源）。"""
        self.extra.add(host.lower())
        self._host_cache.pop(host.lower(), None)

    # ---------------------------------------------------------- URL 检查

    def check_url(self, url: str, resolve: bool = False) -> str:
        """校验 URL 是否允许访问；返回主机名。不允许时抛 `NetGuardError`。

        `resolve=True` 时额外做一次 DNS 解析，拦截指向内网/回环的地址（SSRF 防护）。
        """
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise NetGuardError(f"不允许的协议：{parsed.scheme or '(空)'}")
        if parsed.scheme == "http" and not self._is_private_host(parsed.hostname or ""):
            # 明文 HTTP 仅在访问内网自建服务时允许
            raise NetGuardError("出于安全考虑只允许 HTTPS，或明确放行的内网地址")
        host = (parsed.hostname or "").lower()
        if not host:
            raise NetGuardError("URL 缺少主机名")
        if not self.is_allowed(host):
            raise NetGuardError(
                f"目标域名不在白名单内：{host}\n"
                "如需使用第三方源，请在「设置 → 网络安全」中添加信任域名。"
            )
        if resolve and not self.allow_private:
            for ip in self._resolve(host):
                if self._is_private(ip):
                    raise NetGuardError(f"目标域名解析到内网地址，已拒绝：{host} -> {ip}")
        return host

    def is_allowed(self, host: str) -> bool:
        host = host.lower()
        if host in self.extra:
            return True
        return any(host == suffix or host.endswith("." + suffix) for suffix in self.allowed)

    def _resolve(self, host: str) -> list:
        cached = self._host_cache.get(host)
        if cached is not None:
            return cached
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror:
            self._host_cache[host] = []
            return []
        ips = [info[4][0] for info in infos]
        self._host_cache[host] = ips
        return ips

    @staticmethod
    def _is_private(ip: str) -> bool:
        try:
            return ipaddress.ip_address(ip).is_private
        except ValueError:
            return ip in ("localhost",)

    def _is_private_host(self, host: str) -> bool:
        if host in ("localhost",):
            return True
        for ip in self._resolve(host):
            if self._is_private(ip):
                return True
        return False

    # ---------------------------------------------------------- 下载约束

    def check_download(self, dest: Path, size: int = 0, base: Optional[Path] = None) -> Path:
        """校验下载目标：体积上限 + 不得逃出 `base` 目录。"""
        if size and size > self.max_bytes:
            raise NetGuardError(
                f"文件体积 {size} 超过上限 {self.max_bytes}，已拒绝下载以防磁盘被占满"
            )
        if base is not None:
            try:
                from .utils import safe_join

                root = Path(base).resolve()
                target = Path(dest).resolve()
                target.relative_to(root)
            except ValueError:
                raise NetGuardError(f"下载目标越界：{dest} 不在 {base} 内") from None
        return Path(dest)

    def describe(self) -> Tuple[int, int]:
        """（白名单数量，单文件体积上限）便于界面展示。"""
        return len(self.allowed) + len(self.extra), self.max_bytes


guard = NetGuard()
