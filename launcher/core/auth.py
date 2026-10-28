"""账户体系：离线登录 + 微软（正版）登录。

* **离线**：按 Mojang 的 `OfflinePlayer:` 规则生成确定性 UUID，完全本地，无需网络。
* **正版**：微软 OAuth2 设备码流程（Device Code Flow），完成后依次换取
  XBL → XSTS → Minecraft 令牌，最后拉取角色信息（UUID / 名称 / 皮肤）。

微软登录需要一个自行注册的 Azure 应用 `client_id`（设置页可填），
本启动器不在代码里内置任何第三方 client_id。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

import requests

from . import paths
from .constants import (
    MC_LOGIN_URL,
    MC_PROFILE_URL,
    MICROSOFT_DEVICE_CODE_URL,
    MICROSOFT_TOKEN_URL,
    XBOX_AUTH_URL,
    XSTS_AUTH_URL,
)
from .utils import LOG, ensure_dir

OFFLINE = "offline"
MICROSOFT = "microsoft"
YGGDRASIL = "yggdrasil"     # 第三方（外置）登录：Blessing Skin / 统一通行证 / LittleSkin …


# -------------------------------------------------------------------- 账户


@dataclass
class Account:
    username: str
    uuid: str
    type: str = OFFLINE
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0        # Unix 时间戳，0 = 不过期
    client_token: str = ""
    skin_url: str = ""
    cape_url: str = ""
    selected: bool = False
    extra: Dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------ 元数据

    @property
    def dashed_uuid(self) -> str:
        u = self.uuid.replace("-", "")
        if len(u) != 32:
            return self.uuid
        return f"{u[0:8]}-{u[8:12]}-{u[12:16]}-{u[16:20]}-{u[20:32]}"

    @property
    def is_expired(self) -> bool:
        if self.type != MICROSOFT or not self.expires_at:
            return False
        return time.time() >= self.expires_at - 60

    @property
    def type_label(self) -> str:
        if self.type == OFFLINE:
            return "离线"
        if self.type == YGGDRASIL:
            return "外置登录"
        return "微软正版"

    @property
    def server_url(self) -> str:
        """外置登录的认证服务器地址（离线/微软为空）。"""
        return str(self.extra.get("server_url", "") or "")

    @property
    def display(self) -> str:
        return f"{self.username}（{self.type_label}）"

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "Account":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


# -------------------------------------------------------------------- 离线


def offline_uuid(username: str) -> str:
    """离线模式 UUID：`UUID(MD5("OfflinePlayer:" + name))`，与官方端一致。"""
    seed = f"OfflinePlayer:{username}".encode("utf-8")
    return str(uuid.uuid3(uuid.NAMESPACE_DNS, seed.decode("utf-8"))).replace("-", "")


def create_offline_account(username: str) -> Account:
    name = (username or "").strip()
    if not (3 <= len(name) <= 16) or not all(c.isalnum() or c == "_" for c in name):
        raise ValueError("昵称需为 3–16 位字母、数字或下划线")
    return Account(
        username=name,
        uuid=offline_uuid(name),
        type=OFFLINE,
        access_token="0" * 32,
        client_token=uuid.uuid4().hex,
    )


# -------------------------------------------------------------------- 微软登录


class MicrosoftAuthError(RuntimeError):
    pass


@dataclass
class DeviceCodeSession:
    """设备码登录的一次会话。"""

    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int = 5
    created_at: float = field(default_factory=time.time)

    @property
    def expired(self) -> bool:
        return time.time() - self.created_at > self.expires_in


class MicrosoftAuthenticator:
    """微软设备码登录。所有网络交互集中在此，便于替换与测试。"""

    SCOPE = "XboxLive.signin offline_access"

    def __init__(self, client_id: str, timeout: int = 20) -> None:
        if not client_id:
            raise MicrosoftAuthError(
                "未配置 Azure 应用 client_id：请在「设置 → 账户」中填写后重试。"
            )
        self.client_id = client_id
        self.timeout = timeout
        self._s = requests.Session()
        self._s.headers.update({"User-Agent": "PyLauncher/1.0"})

    # ---------------------------------------------------------- 第一步

    def begin(self) -> DeviceCodeSession:
        resp = self._s.post(
            MICROSOFT_DEVICE_CODE_URL,
            data={"client_id": self.client_id, "scope": self.SCOPE},
            timeout=self.timeout,
        )
        if resp.status_code >= 400:
            raise MicrosoftAuthError(f"获取设备码失败（HTTP {resp.status_code}）：{resp.text[:200]}")
        data = resp.json()
        return DeviceCodeSession(
            device_code=data["device_code"],
            user_code=data["user_code"],
            verification_uri=data.get("verification_uri", "https://www.microsoft.com/link"),
            expires_in=int(data.get("expires_in", 900)),
            interval=int(data.get("interval", 5)),
        )

    # ---------------------------------------------------------- 轮询

    def poll(self, session_obj: DeviceCodeSession, stop: Optional[Callable[[], bool]] = None) -> Account:
        """阻塞轮询直到用户完成授权或超时。返回 Account。"""
        deadline = time.time() + session_obj.expires_in
        while time.time() < deadline:
            if stop and stop():
                raise MicrosoftAuthError("登录已取消")
            time.sleep(session_obj.interval)
            token = self._request_token(
                {
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": self.client_id,
                    "device_code": session_obj.device_code,
                }
            )
            if token is None:      # authorization_pending，继续等
                continue
            return self._finish(token)

        raise MicrosoftAuthError("设备码已过期，请重新发起登录")

    def _request_token(self, payload: Dict[str, str]) -> Optional[Dict]:
        resp = self._s.post(MICROSOFT_TOKEN_URL, data=payload, timeout=self.timeout)
        data = resp.json() if resp.content else {}
        if resp.status_code == 200 and data.get("access_token"):
            return data
        err = data.get("error", "")
        if err in ("authorization_pending", "slow_down"):
            return None
        raise MicrosoftAuthError(f"令牌获取失败：{data.get('error_description') or err or resp.text[:200]}")

    # ---------------------------------------------------------- 后续换取

    def _finish(self, ms_token: Dict) -> Account:
        access = ms_token["access_token"]
        refresh = ms_token.get("refresh_token", "")
        expires = time.time() + int(ms_token.get("expires_in", 86400))

        xbl = self._xbox_authenticate(access)
        xsts = self._xsts_authorize(xbl["token"])
        uhs = xsts.get("uhs") or xbl.get("uhs", "")
        mc = self._minecraft_login(uhs, xsts["token"])
        profile = self._minecraft_profile(mc["access_token"])

        return Account(
            username=profile["name"],
            uuid=profile["id"],
            type=MICROSOFT,
            access_token=mc["access_token"],
            refresh_token=refresh,
            expires_at=time.time() + int(mc.get("expires_in", 86400)),
            skin_url=profile.get("skin_url", ""),
            cape_url=profile.get("cape_url", ""),
            extra={"ms_expires_at": str(expires)},
        )

    def _xbox_authenticate(self, ms_access_token: str) -> Dict:
        payload = {
            "Properties": {
                "AuthMethod": "RPS",
                "SiteName": "user.auth.xboxlive.com",
                "RpsTicket": f"d={ms_access_token}",
            },
            "RelyingParty": "http://auth.xboxlive.com",
            "TokenType": "JWT",
        }
        r = self._s.post(XBOX_AUTH_URL, json=payload, timeout=self.timeout)
        if r.status_code != 200:
            raise MicrosoftAuthError(f"Xbox 身份验证失败（HTTP {r.status_code}）：{r.text[:200]}")
        data = r.json()
        return {
            "token": data["Token"],
            "uhs": data["DisplayClaims"]["xui"][0]["uhs"],
        }

    def _xsts_authorize(self, xbl_token: str) -> Dict:
        payload = {
            "Properties": {"SandboxId": "RETAIL", "UserTokens": [xbl_token]},
            "RelyingParty": "rp://api.minecraftservices.com/",
            "TokenType": "JWT",
        }
        r = self._s.post(XSTS_AUTH_URL, json=payload, timeout=self.timeout)
        if r.status_code != 200:
            code = r.status_code
            if code == 401:
                detail = (r.json().get("XErr") or "")
                if detail == "2148916233":
                    raise MicrosoftAuthError("该微软账号未创建 Xbox 档案，请先在 Xbox 官网注册")
                if detail == "2148916238":
                    raise MicrosoftAuthError("该账号为未成年账号，需加入家庭组后才能登录")
                raise MicrosoftAuthError(f"XSTS 授权失败：{detail}")
            raise MicrosoftAuthError(f"XSTS 授权失败（HTTP {code}）：{r.text[:200]}")
        data = r.json()
        return {
            "token": data["Token"],
            "uhs": data["DisplayClaims"]["xui"][0]["uhs"],
        }

    def _minecraft_login(self, uhs: str, xsts_token: str) -> Dict:
        r = self._s.post(
            MC_LOGIN_URL,
            json={"identityToken": f"XBL3.0 x={uhs};{xsts_token}"},
            timeout=self.timeout,
        )
        if r.status_code != 200:
            raise MicrosoftAuthError(f"Minecraft 登录失败（HTTP {r.status_code}）：{r.text[:200]}")
        return r.json()

    def _minecraft_profile(self, mc_token: str) -> Dict:
        r = self._s.get(
            MC_PROFILE_URL,
            headers={"Authorization": f"Bearer {mc_token}"},
            timeout=self.timeout,
        )
        if r.status_code == 404:
            raise MicrosoftAuthError("该微软账号尚未拥有《我的世界》Java 版，无法登录")
        if r.status_code != 200:
            raise MicrosoftAuthError(f"获取角色信息失败（HTTP {r.status_code}）")
        data = r.json()
        skins = data.get("skins") or []
        capes = data.get("capes") or []
        return {
            "id": data["id"],
            "name": data["name"],
            "skin_url": skins[0]["url"] if skins else "",
            "cape_url": capes[0]["url"] if capes else "",
        }

    # ---------------------------------------------------------- 刷新

    def refresh(self, account: Account) -> Account:
        if not account.refresh_token:
            raise MicrosoftAuthError("缺少刷新令牌，请重新登录")
        token = self._request_token(
            {
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "refresh_token": account.refresh_token,
            }
        )
        if not token:
            raise MicrosoftAuthError("刷新失败，请重新登录")
        refreshed = self._finish(token)
        refreshed.selected = account.selected
        refreshed.client_token = account.client_token
        return refreshed


# -------------------------------------------------------------------- 第三方（外置）登录


class YggdrasilError(Exception):
    """外置登录失败。"""


class YggdrasilAuthenticator:
    """Yggdrasil 协议客户端，用于接入第三方皮肤站 / 统一通行证。

    标准流程：
    * `POST <server>/authserver/authenticate` 用账号密码换 accessToken；
    * `POST /authserver/refresh` 续期；
    * `POST /authserver/validate` 校验令牌是否还有效。

    用户填的地址属于「自己主动信任的服务」，因此会把域名加入防火墙白名单，
    否则请求会被 `netguard` 拦下。
    """

    def __init__(self, server_url: str, timeout: float = 15.0) -> None:
        self.server_url = (server_url or "").rstrip("/")
        if not self.server_url:
            raise YggdrasilError("请先填写外置登录服务器地址")
        if not self.server_url.startswith(("http://", "https://")):
            self.server_url = "https://" + self.server_url
        self.timeout = timeout
        self._trust_host()

    # ---------------------------------------------------------- 内部

    def _trust_host(self) -> None:
        from urllib.parse import urlparse

        from .netguard import guard

        host = urlparse(self.server_url).hostname or ""
        if host and not guard.is_allowed(host):
            # http 仅在访问内网自建皮肤站时允许，这里同样放行
            guard.add_host(host)
            LOG.info("已将外置登录服务器加入白名单：%s", host)

    def _post(self, path: str, payload: Dict) -> Dict:
        import requests
        from urllib.parse import urlparse

        url = f"{self.server_url}{path}"
        from .netguard import guard

        host = urlparse(url).hostname or ""
        if host and host not in guard.extra and not guard.is_allowed(host):
            guard.add_host(host)
        try:
            resp = requests.post(
                url,
                json=payload,
                timeout=(10, self.timeout),
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "User-Agent": "PyLauncher/1.2",
                },
            )
        except Exception as exc:  # noqa: BLE001
            raise YggdrasilError(f"无法连接认证服务器：{exc}") from exc

        code = resp.status_code
        if code == 403 or code == 401:
            try:
                detail = resp.json()
            except Exception:  # noqa: BLE001
                detail = {}
            message = detail.get("errorMessage") or detail.get("message") or "账号或密码错误"
            raise YggdrasilError(message)
        if code >= 400:
            raise YggdrasilError(f"认证服务器返回 HTTP {code}")
        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise YggdrasilError("认证服务器返回的不是合法 JSON（地址可能填错）") from exc
        if isinstance(data, dict) and data.get("error"):
            raise YggdrasilError(str(data.get("errorMessage") or data["error"]))
        return data

    # ---------------------------------------------------------- 对外

    def login(self, username: str, password: str, client_token: str = "") -> Account:
        name = (username or "").strip()
        if not name:
            raise YggdrasilError("请填写账号")
        if not password:
            raise YggdrasilError("请填写密码")

        token = client_token or uuid.uuid4().hex
        payload = {
            "username": name,
            "password": password,
            "clientToken": token,
            "requestUser": True,
        }
        # 部分皮肤站只接受老式的 agent 字段
        try:
            data = self._post("/authserver/authenticate", payload)
        except YggdrasilError:
            data = self._post("/authserver/authenticate", {
                **payload,
                "agent": {"name": "Minecraft", "version": 1},
            })

        profiles = data.get("availableProfiles") or []
        selected = data.get("selectedProfile") or (profiles[0] if profiles else {})
        if not selected:
            raise YggdrasilError("该账号下没有可用角色，请先在皮肤站创建角色")
        acc_uuid = str(selected.get("id") or selected.get("uuid") or "").replace("-", "")
        if not acc_uuid:
            raise YggdrasilError("认证服务器返回的角色信息缺少 UUID")

        account = Account(
            username=str(selected.get("name") or name),
            uuid=acc_uuid,
            type=YGGDRASIL,
            access_token=str(data.get("accessToken") or ""),
            refresh_token=str((data.get("refreshToken") or "")),
            client_token=str(data.get("clientToken") or token),
            extra={
                "server_url": self.server_url,
                "login_name": name,
            },
        )
        if not account.access_token:
            raise YggdrasilError("认证服务器未返回 accessToken")
        LOG.info("外置登录成功：%s @ %s", account.username, self.server_url)
        return account

    def refresh(self, account: Account) -> Account:
        if account.type != YGGDRASIL:
            raise YggdrasilError("该账户不是外置登录账户")
        if not account.access_token:
            raise YggdrasilError("缺少令牌，请重新登录")
        payload = {
            "accessToken": account.access_token,
            "clientToken": account.client_token or uuid.uuid4().hex,
            "requestUser": True,
        }
        try:
            data = self._post("/authserver/refresh", payload)
        except YggdrasilError as exc:
            raise YggdrasilError(f"刷新失败：{exc}") from exc
        return Account(
            username=account.username,
            uuid=account.uuid,
            type=YGGDRASIL,
            access_token=str(data.get("accessToken") or account.access_token),
            refresh_token=str(data.get("refreshToken") or account.refresh_token),
            client_token=str(data.get("clientToken") or account.client_token),
            selected=account.selected,
            extra={**account.extra, "server_url": self.server_url},
        )

    def validate(self, account: Account) -> bool:
        """令牌是否仍然有效（无效需要重新登录）。"""
        if not account.access_token:
            return False
        try:
            self._post("/authserver/validate", {
                "accessToken": account.access_token,
                "clientToken": account.client_token,
            })
            return True
        except YggdrasilError:
            return False


def refresh_account(account: Account) -> Account:
    """按账户类型选择刷新方式（微软 / 外置登录）。"""
    if account.type == MICROSOFT:
        client_id = str(account.extra.get("client_id", "") or "")
        if not client_id:
            from .config import get_config

            client_id = str(get_config().get("microsoft.client_id", "") or "")
        if not client_id:
            raise RuntimeError("缺少 Azure client_id，无法刷新微软令牌")
        return MicrosoftAuthenticator(client_id).refresh(account)
    if account.type == YGGDRASIL:
        url = account.server_url
        if not url:
            from .config import get_config

            url = str(get_config().get("auth.yggdrasil_url", "") or "")
        if not url:
            raise RuntimeError("缺少外置登录服务器地址，无法刷新")
        return YggdrasilAuthenticator(url).refresh(account)
    raise RuntimeError("离线账户无需刷新")


# -------------------------------------------------------------------- 账户库


class AccountDatabase:
    """accounts.json 的读写与增删改。"""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else paths.accounts_file()
        ensure_dir(self._path.parent)
        self._accounts: List[Account] = []
        self.load()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def accounts(self) -> List[Account]:
        return self._accounts

    def load(self) -> None:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                self._accounts = [Account.from_dict(a) for a in raw.get("accounts", [])]
            except Exception as exc:  # noqa: BLE001
                LOG.warning("账户文件损坏，已备份：%s", exc)
                try:
                    self._path.replace(self._path.with_suffix(".json.bak"))
                except OSError:
                    pass
                self._accounts = []
        if not any(a.selected for a in self._accounts) and self._accounts:
            self._accounts[0].selected = True

    def save(self) -> None:
        data = {"accounts": [a.to_dict() for a in self._accounts]}
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    # ------------------------------------------------------------ 操作

    def add(self, account: Account) -> Account:
        # 同名同类型的账户视为同一个，直接更新
        for i, existing in enumerate(self._accounts):
            if existing.username == account.username and existing.type == account.type:
                account.selected = existing.selected
                self._accounts[i] = account
                self.save()
                return account
        account.selected = not self._accounts
        self._accounts.append(account)
        self.save()
        return account

    def remove(self, account: Account) -> None:
        was_selected = account.selected
        self._accounts = [a for a in self._accounts if a is not account]
        if was_selected and self._accounts:
            self._accounts[0].selected = True
        self.save()

    def select(self, account: Account) -> None:
        for a in self._accounts:
            a.selected = a is account
        self.save()

    @property
    def selected(self) -> Optional[Account]:
        for a in self._accounts:
            if a.selected:
                return a
        return self._accounts[0] if self._accounts else None

    def find(self, username: str) -> Optional[Account]:
        for a in self._accounts:
            if a.username == username:
                return a
        return None


accounts_db = AccountDatabase()
