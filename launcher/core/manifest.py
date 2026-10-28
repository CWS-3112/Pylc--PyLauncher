"""远程版本清单与本地已安装版本的统一管理。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .constants import BMCLAPI, DEFAULT_MIRROR, MIRRORS, OFFICIAL
from .http import fetch_first_json, get_json_with_fallback, manifest_session
from .utils import LOG, ensure_dir

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
    """Mojang / BMCLAPI 版本清单。

    两条关键韧性设计（历史上「版本列表获取不到」的直接原因）：

    * **联网失败时回退到本地缓存**——清单以前只在内存里，重启即丢，
      断网或被墙时就永远是一片空白；
    * **校验响应有效性**——镜像源经常返回 200 但内容为空/格式不对，
      以前照样记为「加载成功」，界面于是显示 0 条，用户无从判断是没数据还是坏了。
    """

    def __init__(self) -> None:
        self._versions: Dict[str, VersionSummary] = {}
        self._latest: Dict[str, str] = {}
        self._loaded = False
        self._stale = False          # True 表示当前数据来自本地缓存
        self._source = ""            # 实际来源：源 key 或 "cache"
        self._fetched_at = 0.0

    # ------------------------------------------------------------ 拉取

    def refresh(self, mirror: str = DEFAULT_MIRROR, force: bool = False) -> None:
        """拉取清单；失败时自动回退到本地缓存（若有）。"""
        if self._loaded and not force:
            return
        m = MIRRORS.get(mirror) or MIRRORS[DEFAULT_MIRROR]
        backup = MIRRORS[OFFICIAL if mirror != OFFICIAL else BMCLAPI]
        try:
            # 并发取源：只等最快成功的那个，网络差时也不至于几十秒没反应
            data, used = fetch_first_json(
                [m.version_manifest, backup.version_manifest], manifest_session
            )
            self._apply(data, used)
            self._save_cache(data)
            return
        except Exception as exc:  # noqa: BLE001
            cached = self._load_cache()
            if cached is not None:
                self._apply(cached, "cache")
                self._stale = True
                LOG.warning("版本清单获取失败，已回退到本地缓存：%s", exc)
                return
            self._stale = False
            raise

    def _apply(self, data: dict, source: str) -> None:
        """解析并装载清单数据；格式不对会被当成失败，好让上层继续换源。"""
        versions = data.get("versions") if isinstance(data, dict) else None
        if not isinstance(versions, list) or not versions:
            raise ValueError("版本清单为空或格式不正确（缺少 versions 列表）")

        self._versions.clear()
        for item in versions:
            if not isinstance(item, dict):
                continue
            vid = item.get("id")
            if not vid:
                # 镜像偶尔会混入脏记录，一条都不能让整份清单作废
                LOG.debug("版本清单记录缺少 id，已跳过：%s", str(item)[:80])
                continue
            try:
                level = int(item.get("complianceLevel") or 0)
            except (TypeError, ValueError):
                level = 0
            self._versions[vid] = VersionSummary(
                id=vid,
                type=item.get("type") or "release",
                url=item.get("url") or "",
                time=item.get("time") or "",
                release_time=item.get("releaseTime") or "",
                sha1=item.get("sha1") or "",
                compliance_level=level,
            )
        self._latest = data.get("latest") or {}
        self._loaded = True
        self._source = source
        self._fetched_at = time.time()
        LOG.info("版本清单已更新（%d 个版本，来源：%s）", len(self._versions), source)

    # ------------------------------------------------------------ 本地缓存

    def _cache_file(self) -> Path:
        from . import paths as P

        return ensure_dir(P.launcher_root() / "cache") / "version_manifest.json"

    def _save_cache(self, data: dict) -> None:
        try:
            payload = {
                "fetched_at": time.time(),
                "data": data,
            }
            self._cache_file().write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            LOG.debug("写入版本清单缓存失败：%s", exc)

    def load_cached(self, max_age_days: float = 30.0) -> bool:
        """先把磁盘缓存装进内存，让界面**立刻**有内容可显示。

        返回是否成功；数据会被标记为 `stale`，随后后台联网刷新覆盖。
        这是「打开版本列表先白屏几秒、网络差时干脆加载不出来」的直接解法：
        宁可先给一份旧数据，也不要让用户对着空白等。
        """
        data = self._load_cache()
        if not data:
            return False
        if self._fetched_at and time.time() - self._fetched_at > max_age_days * 86400:
            return False
        try:
            self._apply(data, "cache")
            self._stale = True
            return True
        except Exception as exc:  # noqa: BLE001 - 缓存损坏就当没有
            LOG.debug("缓存清单不可用：%s", exc)
            return False

    @property
    def cache_exists(self) -> bool:
        return self._cache_file().is_file()

    def _load_cache(self) -> Optional[dict]:
        path = self._cache_file()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        self._fetched_at = float(payload.get("fetched_at") or 0)
        data = payload.get("data")
        return data if isinstance(data, dict) else None

    # ------------------------------------------------------------ 查询

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def stale(self) -> bool:
        """当前数据是否来自本地缓存（联网失败时的降级结果）。"""
        return self._stale

    @property
    def source(self) -> str:
        return self._source

    @property
    def fetched_at(self) -> float:
        return self._fetched_at

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


# 扫描结果缓存：切换页面 / 刷新都会扫一遍，用目录指纹（子项数与最新修改时间）
# 判断是否可以直接复用，避免每次都做一遍磁盘遍历。
_SCAN_CACHE: Dict[str, tuple] = {}
_SCAN_TTL = 5.0


def _dir_fingerprint(root: Path) -> str:
    try:
        entries = list(root.iterdir())
    except OSError:
        return "missing"
    newest = 0.0
    for entry in entries:
        try:
            newest = max(newest, entry.stat().st_mtime)
        except OSError:
            continue
    return f"{len(entries)}:{newest:.3f}"


def scan_local_versions(game_dir: Path, use_cache: bool = True) -> List[LocalVersion]:
    """扫描 `game_dir/versions/<name>/<name>.json`。

    只认「文件名与目录同名」的标准版本文件；其它 JSON 仅在内容确实是版本
    描述且 `id` 与文件名一致时才采纳，避免把 `usercache.json` 之类的
    运行时文件误当成可启动版本。
    """
    root = game_dir / "versions"
    if not root.is_dir():
        return []

    key = str(root).lower()
    if use_cache:
        import time as _t

        fingerprint = _dir_fingerprint(root)
        hit = _SCAN_CACHE.get(key)
        if hit and hit[0] == fingerprint and _t.time() - hit[2] < _SCAN_TTL:
            return list(hit[1])
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

    if use_cache:
        import time as _t

        _SCAN_CACHE[key] = (_dir_fingerprint(root), tuple(out), _t.time())
    return out


def invalidate_scan_cache() -> None:
    """安装 / 删除版本后调用，让下一次扫描重新读盘。"""
    _SCAN_CACHE.clear()


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
