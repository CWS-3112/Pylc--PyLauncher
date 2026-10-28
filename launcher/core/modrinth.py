"""Modrinth 官方 API 客户端：让用户在启动器里直接搜索并下载模组 / 光影 / 材质包。

使用公开接口 `https://api.modrinth.com/v2`，不需要 API Key；
所有请求都会经过 `core.netguard` 的域名白名单。

只做三件事，保持核心层无 GUI 依赖：

* `search(query, kind, loader, version)`  —— 搜索资源；
* `versions(project_id, loaders, game_versions)` —— 取兼容版本列表；
* `install(...)` —— 把选定版本下载到指定目录（mods / shaderpacks / resourcepacks）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .utils import LOG, human_size

API_ROOT = "https://api.modrinth.com/v2"

# 启动器里的分类 -> Modrinth 的项目类型
# 注意光影在启动器里叫 shaderpack（沿用游戏目录名），而 Modrinth 的项目类型是 shader，
# 不做这层映射的话 facet 会变成无效值，搜索结果永远为空。
PROJECT_TYPES = {
    "mod": "mod",
    "shader": "shader",
    "shaderpack": "shader",
    "resourcepack": "resourcepack",
    "resourcepacks": "resourcepack",
    "datapack": "datapack",
}

KIND_LABELS = {
    "mod": "模组",
    "shader": "光影",
    "resourcepack": "材质包",
    "datapack": "数据包",
}


@dataclass
class ModrinthHit:
    """搜索结果里的一条。"""

    project_id: str
    slug: str
    title: str
    description: str
    kind: str
    downloads: int = 0
    follows: int = 0
    icon_url: str = ""
    categories: List[str] = field(default_factory=list)
    client_side: str = ""
    server_side: str = ""
    latest_version: str = ""

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)

    @property
    def page_url(self) -> str:
        return f"https://modrinth.com/{self.kind}/{self.slug}"


@dataclass
class ModrinthFile:
    """可下载的具体文件。"""

    version_id: str
    version_number: str
    name: str
    url: str
    size: int
    sha1: str = ""
    game_versions: List[str] = field(default_factory=list)
    loaders: List[str] = field(default_factory=list)
    primary: bool = False
    released: str = ""

    @property
    def size_text(self) -> str:
        return human_size(self.size) if self.size else "-"


class ModrinthError(Exception):
    """Modrinth 接口调用失败。"""


def _get(path: str, params: Optional[Dict] = None, timeout: int = 20):
    """调用 Modrinth 接口（已过防火墙白名单）。"""
    from .http import manifest_session

    url = f"{API_ROOT}{path}"
    try:
        return manifest_session.get_json(url, params=params or {}, timeout=(10, timeout))
    except Exception as exc:  # noqa: BLE001
        raise ModrinthError(f"Modrinth 接口请求失败：{exc}") from exc


def search(
    query: str = "",
    kind: str = "mod",
    loader: str = "",
    game_version: str = "",
    limit: int = 20,
    offset: int = 0,
) -> List[ModrinthHit]:
    """搜索资源；`kind` 取 mod / shader / resourcepack / datapack。"""
    facets: List[List[str]] = []
    project_type = PROJECT_TYPES.get(str(kind).lower(), str(kind).lower())
    facets.append([f"project_type:{project_type}"])
    if loader:
        facets.append([f"categories:{loader}"])
    if game_version:
        facets.append([f"versions:{game_version}"])

    params = {
        "query": query or "",
        "limit": max(1, min(limit, 100)),
        "offset": max(0, offset),
        "index": "relevance",
    }
    if facets:
        import json

        params["facets"] = json.dumps(facets, separators=(",", ":"))

    data = _get("/search", params)
    hits: List[ModrinthHit] = []
    for item in (data or {}).get("hits", []) if isinstance(data, dict) else []:
        if not isinstance(item, dict):
            continue
        hits.append(
            ModrinthHit(
                project_id=str(item.get("project_id") or item.get("slug") or ""),
                slug=str(item.get("slug") or ""),
                title=str(item.get("title") or item.get("slug") or "未命名"),
                description=str(item.get("description") or "")[:200],
                kind=project_type,
                downloads=int(item.get("downloads") or 0),
                follows=int(item.get("follows") or 0),
                icon_url=str(item.get("icon_url") or ""),
                categories=[str(c) for c in (item.get("categories") or []) if c],
                client_side=str(item.get("client_side") or ""),
                server_side=str(item.get("server_side") or ""),
                latest_version=str(item.get("latest_version") or ""),
            )
        )
    if not hits:
        LOG.debug("Modrinth 搜索无结果：query=%s kind=%s", query, kind)
    return hits


def versions(
    project_id: str,
    loaders: Sequence[str] = (),
    game_version: str = "",
) -> List[Dict]:
    """取项目的版本列表（原样返回 dict，界面自行挑选）。"""
    params: Dict = {}
    if loaders:
        import json

        params["loaders"] = json.dumps(list(loaders), separators=(",", ":"))
    if game_version:
        import json

        params["game_versions"] = json.dumps([game_version], separators=(",", ":"))
    data = _get(f"/project/{project_id}/version", params)
    return [v for v in data if isinstance(v, dict)] if isinstance(data, list) else []


def pick_file(
    version_data: Dict,
    prefer_primary: bool = True,
) -> Optional[ModrinthFile]:
    """从一个版本里挑出主文件（Modrinth 允许一个版本带多个文件）。"""
    files = [f for f in (version_data.get("files") or []) if isinstance(f, dict)]
    if not files:
        return None
    if prefer_primary:
        primary = [f for f in files if f.get("primary")]
        if primary:
            files = primary
    item = files[0]
    url = str(item.get("url") or "")
    if not url:
        return None
    return ModrinthFile(
        version_id=str(version_data.get("id") or ""),
        version_number=str(version_data.get("version_number") or ""),
        name=str(item.get("filename") or "download"),
        url=url,
        size=int(item.get("size") or 0),
        sha1=str((item.get("hashes") or {}).get("sha1") or ""),
        game_versions=[str(v) for v in (version_data.get("game_versions") or [])],
        loaders=[str(v) for v in (version_data.get("loaders") or [])],
        primary=bool(item.get("primary")),
        released=str(version_data.get("date_published") or ""),
    )


def best_version(
    project_id: str,
    loaders: Sequence[str] = (),
    game_version: str = "",
) -> Optional[ModrinthFile]:
    """取「最新且兼容」的一个文件；找不到就返回 None。"""
    try:
        items = versions(project_id, loaders=loaders, game_version=game_version)
    except ModrinthError:
        raise
    if not items and game_version:
        # 严格匹配不到时放宽到不限游戏版本，由用户自行判断
        try:
            items = versions(project_id, loaders=loaders)
        except ModrinthError:
            return None
    for item in items:
        file = pick_file(item)
        if file:
            return file
    return None


def download_file(file: ModrinthFile, dest_dir: Path, base: Optional[Path] = None) -> Path:
    """把文件下载到 `dest_dir`；返回落盘路径。"""
    from .http import session
    from .netguard import guard

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    # 文件名同样要过防火墙的路径约束，防止接口返回 `../../evil.jar`
    from .utils import safe_join

    target = safe_join(dest_dir, file.name)
    guard.check_download(target, size=file.size, base=base or dest_dir)
    session.download(file.url, target, file.sha1, file.size)
    LOG.info("已从 Modrinth 下载：%s", target)
    return target


__all__ = [
    "API_ROOT",
    "KIND_LABELS",
    "ModrinthError",
    "ModrinthFile",
    "ModrinthHit",
    "best_version",
    "download_file",
    "pick_file",
    "search",
    "versions",
]
