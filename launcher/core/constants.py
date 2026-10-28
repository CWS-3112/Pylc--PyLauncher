"""全局常量与下载源镜像定义。

所有网络地址集中在此，便于切换官方源 / 国内镜像。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

# ---------------------------------------------------------------- 启动器信息

LAUNCHER_NAME = "PyLauncher"
LAUNCHER_VERSION = "1.0.0"
LAUNCHER_BRAND = "pylauncher"

# ---------------------------------------------------------------- 下载源

# 官方源
OFFICIAL = "official"
# BMCLAPI（bangbang93）国内镜像
BMCLAPI = "bmclapi"

MOJANG_VERSION_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
MOJANG_LIBRARIES = "https://libraries.minecraft.net/"
MOJANG_RESOURCES = "https://resources.download.minecraft.net/"

BMCLAPI_ROOT = "https://bmclapi2.bangbang93.com"
BMCLAPI_VERSION_MANIFEST = BMCLAPI_ROOT + "/mc/game/version_manifest_v2.json"
BMCLAPI_LIBRARIES = BMCLAPI_ROOT + "/maven/"
BMCLAPI_RESOURCES = BMCLAPI_ROOT + "/assets/"

FABRIC_META = "https://meta.fabricmc.net/v2"
FABRIC_MAVEN = "https://maven.fabricmc.net/"
BMCLAPI_FABRIC_META = BMCLAPI_ROOT + "/fabric-meta/v2"

FORGE_MAVEN = "https://maven.minecraftforge.net/net/minecraftforge/forge/"
BMCLAPI_FORGE = BMCLAPI_ROOT + "/forge"

# 微软登录：设备码与令牌端点（需要自建 Azure 应用 client_id）
MICROSOFT_CLIENT_ID_SETTING = "microsoft.client_id"
MICROSOFT_DEVICE_CODE_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
MICROSOFT_TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
XBOX_AUTH_URL = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_AUTH_URL = "https://xsts.auth.xboxlive.com/xsts/authorize"
MC_LOGIN_URL = "https://api.minecraftservices.com/authentication/login_with_xbox"
MC_PROFILE_URL = "https://api.minecraftservices.com/minecraft/profile"

# ---------------------------------------------------------------- 版本类型


@dataclass(frozen=True)
class Mirror:
    """一组下载源。"""

    key: str
    name: str
    version_manifest: str
    libraries: str
    resources: str
    fabric_meta: str
    forge: str


MIRRORS: Dict[str, Mirror] = {
    OFFICIAL: Mirror(
        key=OFFICIAL,
        name="官方源 (Mojang / Fabric / Forge)",
        version_manifest=MOJANG_VERSION_MANIFEST,
        libraries=MOJANG_LIBRARIES,
        resources=MOJANG_RESOURCES,
        fabric_meta=FABRIC_META,
        forge=FORGE_MAVEN,
    ),
    BMCLAPI: Mirror(
        key=BMCLAPI,
        name="国内镜像 (BMCLAPI)",
        version_manifest=BMCLAPI_VERSION_MANIFEST,
        libraries=BMCLAPI_LIBRARIES,
        resources=BMCLAPI_RESOURCES,
        fabric_meta=BMCLAPI_FABRIC_META,
        forge=BMCLAPI_FORGE,
    ),
}

DEFAULT_MIRROR = BMCLAPI

# ---------------------------------------------------------------- 其它

# 建议的最低 Java 大版本（1.18+ 需要 Java 17）
JAVA_REQUIREMENTS = [
    (18, 17),   # 1.18 起需要 Java 17
    (21, 21),   # 1.21 起建议 Java 21（1.20.5 起强制 21）
]

HTTP_TIMEOUT = 30
MAX_DOWNLOAD_WORKERS = 8
