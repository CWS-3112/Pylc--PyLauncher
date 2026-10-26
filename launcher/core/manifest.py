"""远程版本清单与本地已安装版本的统一管理。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .constants import BMCLAPI, DEFAULT_MIRROR, MIRRORS, OFFICIAL
from .http import get_json_with_fallback
from .utils import LOG

# -------------------------------------------------------------------- 数据结构


@dataclass
class VersionSummary:
    """远程清单中的一条版本记录。"""

    id: str
    type: str = "release"          # release / snapshot / old_beta / old_alpha
    url: str = ""
    time: str = ""
    release_time: str = ""
    sha1: str = ""
    compliance_level: int = 0
    installed: bool = False        # 是否已存在于本地

    @property
    def is_snapshot(self) -> bool:
        return self.type != "release"


@dataclass
class LocalVersion:
    """本地 `versions/<id>/<id>.json` 描述的一个版本。"""

    id: str
    path: Path            # 版本目录
    json_path: Path
    jar_path: Path
    inherits_from: Optional[str] = None
    main_class: str = ""
    type: str = "release"
    loader: str = "vanilla"   # vanilla / forge / fabric / quilt / optifine
    asset_index: str = ""

    @property
    def jar_exists(self) -> bool:
        return self.jar_path.is_file()

    @property
    def playable(self) -> bool:
        return self.json_path.is_file() and self.jar_exists


# -------------------------------------------------------------------- 清单


class VersionManifest:
    """Mojang / BMCLAPI 版本清单。"""

    def __init__(self) -> None:
        self._versions: Dict[str, VersionSummary] = {}
        self._latest: Dict[str, str] = {}
        self._loaded = False

    # ------------------------------------------------------------ 拉取

    def refresh(self, mirror: str = DEFAULT_MIRROR, force: bool = False) -> None:
        if self._loaded and not force:
            return
        m = MIRRORS.get(mirror) or MIRRORS[DEFAULT_MIRROR]
        backup = MIRRORS[OFFICIAL if mirror != OFFICIAL else BMCLAPI]
        data, used = get_json_with_fallback([m.version_manifest, backup.version_manifest])
        self._versions.clear()
        for item in data.get("versions", []):
            self._versions[item["id"]] = VersionSummary(
                id=item["id"],
                type=item.get("type", "release"),
                url=item.get("url", ""),
                time=item.get("time", ""),
                release_time=item.get("releaseTime", ""),
                sha1=item.get("sha1", ""),
                compliance_level=item.get("complianceLevel", 0),
            )
        self._latest = data.get("latest", {})
        self._loaded = True
        LOG.info("版本清单已更新（%d 个版本，实际来源：%s）", len(self._versions), used)

    # ------------------------------------------------------------ 查询

    @property
    def loaded(self) -> bool:
        return self._loaded

    def all(self) -> List[VersionSummary]:
        return list(self._versions.values())

    def releases(self) -> List[VersionSummary]:
        return [v for v in self._versions.values() if v.type == "release"]

    def snapshots(self) -> List[VersionSummary]:
        return [v for v in self._versions.values() if v.type != "release"]

    def get(self, version_id: str) -> Optional[VersionSummary]:
        return self._versions.get(version_id)

    @property
    def latest_release(self) -> Optional[str]:
        return self._latest.get("release")

    @property
    def latest_snapshot(self) -> Optional[str]:
        return self._latest.get("snapshot")

    def mark_installed(self, installed_ids: List[str]) -> None:
        for vid in installed_ids:
            if vid in self._versions:
                self._versions[vid].installed = True
        # 本地存在的自定义（Forge/Fabric）版本不在远程清单里，也纳入
        for vid in installed_ids:
            if vid not in self._versions:
                self._versions[vid] = VersionSummary(id=vid, type="custom", installed=True)


manifest = VersionManifest()


# -------------------------------------------------------------------- 本地


def _detect_loader(version_id: str, data: dict) -> str:
    vid = version_id.lower()
    if "forge" in vid:
        return "forge"
    if "fabric" in vid:
        return "fabric"
    if "quilt" in vid:
        return "quilt"
    if "optifine" in vid:
        return "optifine"
    # 通过 mainClass / arguments 进一步判断
    main_class = (data.get("mainClass") or "").lower()
    if "fml" in main_class or "forge" in main_class:
        return "forge"
    if "fabric" in main_class or "knot" in main_class:
        return "fabric"
    return "vanilla"


def load_local_version(path: Path) -> LocalVersion:
    data = json.loads(path.read_text(encoding="utf-8"))
    vid = data.get("id") or path.stem
    return LocalVersion(
        id=vid,
        path=path.parent,
        json_path=path,
        jar_path=path.with_suffix(".jar"),
        inherits_from=data.get("inheritsFrom"),
        main_class=data.get("mainClass", ""),
        type=data.get("type", "release"),
        loader=_detect_loader(vid, data),
        asset_index=(data.get("assetIndex") or {}).get("id", ""),
    )


def _looks_like_version_json(data: object, stem: str) -> bool:
    """粗略判断一份 JSON 是不是 Mojang 版本描述。

    版本目录里经常混着 `usercache.json`、`modrinth.index.json` 这类运行时数据，
    旧实现把它们一律当成版本，导致列表冒出一堆无法启动的假条目。
    """
    if not isinstance(data, dict):
        return False
    if data.get("inheritsFrom"):
        return True
    if data.get("mainClass") or data.get("minecraftArguments"):
        return True
    return bool(data.get("libraries")) and bool(data.get("id"))


def scan_local_versions(game_dir: Path) -> List[LocalVersion]:
    """扫描 `game_dir/versions/<name>/<name>.json`。

    只认「文件名与目录同名」的标准版本文件；其它 JSON 仅在内容确实是版本
    描述且 `id` 与文件名一致时才采纳，避免把 `usercache.json` 之类的
    运行时文件误当成可启动版本。
    """
    root = game_dir / "versions"
    if not root.is_dir():
        return []
    out: List[LocalVersion] = []
    for sub in sorted(root.iterdir()):
        if not sub.is_dir():
            continue
        for js in sorted(sub.glob("*.json")):
            canonical = (js.stem == sub.name)
            try:
                if not canonical:
                    # 非标准命名：先廉价地确认内容像是版本描述再解析
                    head = js.read_text(encoding="utf-8", errors="ignore")[:4096]
                    if '"mainClass"' not in head and '"inheritsFrom"' not in head \
                            and '"minecraftArguments"' not in head and '"libraries"' not in head:
                        continue
                    try:
                        probe = json.loads(js.read_text(encoding="utf-8", errors="ignore"))
                    except Exception:  # noqa: BLE001
                        continue
                    if not _looks_like_version_json(probe, js.stem):
                        continue
                    if isinstance(probe, dict) and probe.get("id") and probe["id"] != js.stem:
                        # 内容与文件名不符，极可能是被拷进来的资源数据
                        LOG.debug("跳过疑似非版本 JSON：%s", js)
                        continue
                out.append(load_local_version(js))
            except Exception as exc:  # noqa: BLE001
                LOG.debug("无法解析版本文件 %s：%s", js, exc)
    return out


def read_version_json(game_dir: Path, version_id: str) -> dict:
    from . import paths as P

    js = P.version_json(game_dir, version_id)
    return json.loads(js.read_text(encoding="utf-8"))


def resolve_version_chain(game_dir: Path, version_id: str) -> List[dict]:
    """按 `inheritsFrom` 展开版本链，顺序为「子 → 父 → 祖父」。"""
    from . import paths as P

    chain: List[dict] = []
    current: Optional[str] = version_id
    seen = set()
    while current and current not in seen:
        seen.add(current)
        js = P.version_json(game_dir, current)
        if not js.is_file():
            break
        data = json.loads(js.read_text(encoding="utf-8"))
        chain.append(data)
        current = data.get("inheritsFrom")
    return chain
