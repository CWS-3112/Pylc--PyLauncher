"""版本安装：JSON、客户端 JAR、依赖库、Natives、资源索引与资源对象。

设计为「先收集任务，再统一并发下载」，这样 UI 可以预先知道总量并展示真实进度。

    tasks, natives = collect_install_tasks(game_dir, "1.20.4", mirror)
    manager.run(tasks, on_progress=...)
    apply_natives(natives)
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from . import paths as P
from .constants import (
    BMCLAPI,
    BMCLAPI_ROOT,
    DEFAULT_MIRROR,
    MIRRORS,
    OFFICIAL,
    Mirror,
)
from .http import DownloadTask, download_with_fallback, get_json_with_fallback
from .manifest import manifest, read_version_json, resolve_version_chain
from .rules import (
    artifact_to_path,
    artifact_to_url,
    is_allowed,
    library_path,
    pick_native,
)
from .utils import LOG, ensure_dir, extract_zip, sha1_of_file

ProgressCallback = Callable[[str, int, int], None]   # (阶段名, 已完成, 总数)


# -------------------------------------------------------------------- 工具


def get_mirror(key: str) -> Mirror:
    return MIRRORS.get(key) or MIRRORS[DEFAULT_MIRROR]


def library_url(lib: Dict, mirror: Mirror) -> str:
    """优先遵循 JSON 中的 url（官方源），镜像源则一律用镜像地址重写。"""
    art = (lib.get("downloads") or {}).get("artifact") or {}
    if mirror.key == "official" and art.get("url"):
        return art["url"]
    return artifact_to_url(lib["name"], mirror.libraries)


def library_backup_url(lib: Dict, mirror: Mirror) -> str:
    """同一个依赖在「另一个」下载源上的地址，用于主源不可用时自动换源。"""
    return library_url(lib, _backup_mirror(mirror))


def swap_url_root(url: str, mirror: Mirror) -> str:
    """把一个已知的下载 URL 换算到 `mirror` 这个源上的等价地址。

    识别当前 URL 属于哪个源、哪一类资源（依赖库 / 资源文件 / 版本清单），
    再套用目标源对应的根地址；无法识别时返回空串。
    """
    kinds = {
        "library": ("https://libraries.minecraft.net/", "libraries"),
        "resource": ("https://resources.download.minecraft.net/", "resources"),
    }
    meta_roots = ("https://piston-meta.mojang.com", "https://launchermeta.mojang.com")

    rest = ""
    kind = ""
    for name, (official_base, attr) in kinds.items():
        if url.startswith(official_base):
            rest, kind = url[len(official_base):], name
            break
    if not kind:
        for name, (_official_base, attr) in kinds.items():
            base = getattr(MIRRORS[BMCLAPI], attr)
            if url.startswith(base):
                rest, kind = url[len(base):], name
                break

    if kind:
        base = getattr(mirror, kinds[kind][1])
        if base and not url.startswith(base):
            return base + rest
        return ""

    # 版本清单 / 版本 JSON 类地址
    if BMCLAPI_ROOT in url:
        return url.replace(BMCLAPI_ROOT, meta_roots[0]) if mirror.key == OFFICIAL else ""
    for root in meta_roots:
        if root in url:
            return url.replace(root, BMCLAPI_ROOT) if mirror.key != OFFICIAL else ""
    return ""


def ensure_backup_urls(tasks: Iterable[DownloadTask], mirror: Mirror) -> List[DownloadTask]:
    """给所有尚未带备用地址的任务补上「换源地址」。

    公益镜像（BMCLAPI）或官方源都可能整体不可用，每个任务多留一条退路，
    可以显著减少「下载到一半全线失败」的情况。
    """
    backup = _backup_mirror(mirror)
    for task in tasks:
        if not task.extra_urls:
            alt = swap_url_root(task.url, backup)
            if alt and alt != task.url and alt != task.fallback_url:
                task.extra_urls.append(alt)
    return list(tasks)


def _backup_mirror(mirror: Mirror) -> Mirror:
    return get_mirror(OFFICIAL if mirror.key != OFFICIAL else BMCLAPI)


def _library_task(lib: Dict, game_dir: Path, mirror: Mirror, category: str = "library") -> Optional[DownloadTask]:
    """构造依赖库的下载任务。

    注意：像 `org.lwjgl.lwjgl:lwjgl-platform:2.9.1` 这类老版本依赖**只有
    `downloads.classifiers` 没有主 artifact**，此时不存在可下载的主 jar，
    硬拼地址必然得到 404 并被计为「必需文件下载失败」（1.7.10 及更早必现）。
    这类库直接跳过主条目，真正需要的 native 由 `pick_native()` 单独处理。
    """
    art = (lib.get("downloads") or {}).get("artifact") or {}
    downloads = lib.get("downloads") or {}
    has_classifiers = bool((downloads.get("classifiers")))
    if not art and (has_classifiers or not lib.get("url")):
        LOG.debug("跳过没有主构件的依赖（仅含 native 分类器）：%s", lib.get("name"))
        return None

    rel = library_path(lib)
    dest = P.libraries_dir(game_dir) / rel
    backup = _backup_mirror(mirror)
    return DownloadTask(
        name=lib["name"],
        url=library_url(lib, mirror),
        dest=dest,
        sha1=art.get("sha1", ""),
        size=art.get("size", 0),
        category=category,
        # 主构件允许缺失：只有 classifiers 的库本来就没有 jar
        optional=not art,
        fallback_url=library_url(lib, backup),
    )


# -------------------------------------------------------------------- 收集


def collect_library_tasks(
    game_dir: Path,
    version_id: str,
    mirror: Mirror,
    features: Optional[Dict[str, bool]] = None,
) -> Tuple[List[DownloadTask], List[Tuple[Path, Path]]]:
    """返回（依赖库下载任务, 需要解压的 native 包 (归档路径, 目标目录)）。"""
    chain = resolve_version_chain(game_dir, version_id)
    tasks: List[DownloadTask] = []
    natives: List[Tuple[Path, Path]] = []
    seen_libs = set()
    seen_natives = set()

    for data in chain:
        vid = data.get("id", version_id)
        for lib in data.get("libraries", []):
            name = lib.get("name", "")
            if not name or name in seen_libs:
                continue
            if not is_allowed(lib.get("rules"), features):
                continue
            if lib.get("skip"):
                continue

            seen_libs.add(name)
            task = _library_task(lib, game_dir, mirror)
            if task:
                tasks.append(task)

            # ---------------- Natives
            native = pick_native(lib)
            if not native:
                continue
            classifier = native["classifier"]
            key = (name, classifier)
            if key in seen_natives:
                continue
            seen_natives.add(key)

            if native.get("standalone") and native.get("path"):
                # 新格式：native 就是本条目的主 artifact，上面已经建过下载任务
                dest = P.libraries_dir(game_dir) / native["path"]
            else:
                rel = native.get("path") or artifact_to_path(f"{name}:{classifier}")
                url = native.get("url") or artifact_to_url(
                    f"{name}:{classifier}", mirror.libraries
                )
                dest = P.libraries_dir(game_dir) / rel
                tasks.append(
                    DownloadTask(
                        name=f"{name} ({classifier})",
                        url=url,
                        dest=dest,
                        sha1=native.get("sha1", ""),
                        size=native.get("size", 0),
                        category="native",
                        fallback_url=artifact_to_url(
                            f"{name}:{classifier}", _backup_mirror(mirror).libraries
                        ),
                    )
                )
            natives.append((dest, P.natives_dir(game_dir, version_id)))

    return tasks, natives


def collect_asset_tasks(game_dir: Path, version_id: str, mirror: Mirror) -> List[DownloadTask]:
    """下载资源索引 + 全部资源对象。

    索引文件很小，直接走官方（稳定）；体积大的资源对象走所选镜像，失败自动回退。
    """
    chain = resolve_version_chain(game_dir, version_id)
    index_info = None
    for data in chain:
        if data.get("assetIndex"):
            index_info = data["assetIndex"]
            break
    if not index_info:
        return []

    index_id = index_info["id"]
    index_file = P.asset_index_dir(game_dir) / f"{index_id}.json"
    tasks: List[DownloadTask] = []

    official_url = index_info.get("url") or ""
    mirror_url = f"{mirror.resources.rstrip('/')}/indexes/{index_id}.json"
    urls = [official_url, mirror_url] if mirror.key != "official" else [official_url]
    urls = [u for u in dict.fromkeys(urls) if u]

    tasks.append(
        DownloadTask(
            name=f"资源索引 {index_id}",
            url=urls[0],
            dest=index_file,
            sha1=index_info.get("sha1", ""),
            size=index_info.get("size", 0),
            category="asset_index",
            fallback_url=urls[1] if len(urls) > 1 else "",
        )
    )

    # 资源对象依赖索引内容，需要先把索引拿到手（同步，失败则用已有缓存）
    if not index_file.is_file() or not _json_ok(index_file):
        try:
            download_with_fallback(
                urls, index_file, index_info.get("sha1", ""), index_info.get("size", 0)
            )
        except Exception as exc:  # noqa: BLE001
            LOG.warning("资源索引下载失败，跳过资源：%s", exc)
            return tasks
        # 校验失败时清空 sha1 再试一次（少数源会返回压缩后内容）
        if not _json_ok(index_file):
            LOG.warning("资源索引校验失败，忽略校验重试：%s", index_file)
            try:
                download_with_fallback(urls, index_file, "", 0)
            except Exception as exc:  # noqa: BLE001
                LOG.warning("资源索引下载失败：%s", exc)
                return tasks

    try:
        index_data = json.loads(index_file.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return tasks

    backup = _backup_mirror(mirror)
    objects: Dict[str, Dict] = index_data.get("objects", {})
    objects_dir = P.asset_objects_dir(game_dir)
    for name, meta in objects.items():
        h = meta.get("hash", "")
        if len(h) < 2:
            continue
        dest = objects_dir / h[:2] / h
        tasks.append(
            DownloadTask(
                name=name,
                url=f"{mirror.resources.rstrip('/')}/{h[:2]}/{h}",
                dest=dest,
                sha1=h,
                size=meta.get("size", 0),
                category="asset",
                optional=True,
                fallback_url=f"{backup.resources.rstrip('/')}/{h[:2]}/{h}",
            )
        )
    return tasks


def _json_ok(path: Path) -> bool:
    try:
        json.loads(path.read_text(encoding="utf-8"))
        return True
    except Exception:  # noqa: BLE001
        return False


def client_jar_urls(version_id: str, official_url: str, mirror: Mirror) -> List[str]:
    """客户端 JAR 的候选地址（镜像优先，官方兜底）。"""
    urls: List[str] = []
    if mirror.key != "official" and official_url:
        urls.append(f"{BMCLAPI_ROOT}/version/{version_id}/client")
    if official_url:
        urls.append(official_url)
    return list(dict.fromkeys(urls))


def collect_jar_tasks(game_dir: Path, version_id: str, mirror: Mirror) -> List[DownloadTask]:
    """客户端主体 JAR（继承链上每个版本的 JAR 都会被检查）。"""
    tasks: List[DownloadTask] = []
    for data in resolve_version_chain(game_dir, version_id):
        vid = data.get("id")
        if not vid:
            continue
        client = (data.get("downloads") or {}).get("client")
        if not client:
            continue
        urls = client_jar_urls(vid, client.get("url", ""), mirror)
        if not urls:
            continue
        tasks.append(
            DownloadTask(
                name=f"{vid}.jar",
                url=urls[0],
                dest=P.version_jar(game_dir, vid),
                sha1=client.get("sha1", ""),
                size=client.get("size", 0),
                category="jar",
                fallback_url=urls[1] if len(urls) > 1 else "",
            )
        )
    return tasks


def collect_logging_task(game_dir: Path, version_id: str, mirror: Mirror) -> List[DownloadTask]:
    """log4j 配置文件（1.12+）。"""
    for data in resolve_version_chain(game_dir, version_id):
        logging = data.get("logging") or {}
        client = logging.get("client") or {}
        f = client.get("file") or {}
        if not f.get("url"):
            continue
        dest = P.assets_dir(game_dir) / "log_configs" / f.get("id", "log4j.xml")
        return [
            DownloadTask(
                name=f.get("id", "log4j.xml"),
                url=f["url"],
                dest=dest,
                sha1=f.get("sha1", ""),
                size=f.get("size", 0),
                category="logging",
                optional=True,
            )
        ]
    return []


# -------------------------------------------------------------------- 汇总


def collect_install_tasks(
    game_dir: Path,
    version_id: str,
    mirror_key: str = DEFAULT_MIRROR,
    with_assets: bool = True,
    force_check: bool = False,
) -> Tuple[List[DownloadTask], List[Tuple[Path, Path]]]:
    """汇总安装一个版本所需的全部下载任务。"""
    mirror = get_mirror(mirror_key)
    tasks: List[DownloadTask] = []

    tasks += collect_jar_tasks(game_dir, version_id, mirror)
    lib_tasks, natives = collect_library_tasks(game_dir, version_id, mirror)
    tasks += lib_tasks
    tasks += collect_logging_task(game_dir, version_id, mirror)
    if with_assets:
        tasks += collect_asset_tasks(game_dir, version_id, mirror)

    # 每个任务都补一条「换源」退路（公益镜像 / 官方源随时可能整体不可用）
    ensure_backup_urls(tasks, mirror)

    # 过滤掉已存在且校验通过的文件，减少无谓请求
    if not force_check:
        before = len(tasks)
        tasks = [t for t in tasks if not t.exists_ok]
        LOG.info("安装任务：共 %d 项，其中 %d 项已存在", before, before - len(tasks))
    return tasks, natives


# -------------------------------------------------------------------- Natives


def apply_natives(natives: List[Tuple[Path, Path]], on_progress: Optional[Callable[[str], None]] = None) -> int:
    """解压 native 包到版本 natives 目录，返回解压的包数量。"""
    count = 0
    for archive, dest in natives:
        if not archive.is_file():
            LOG.warning("native 包缺失，跳过：%s", archive)
            continue
        if on_progress:
            on_progress(archive.name)
        try:
            extract_zip(
                archive,
                dest,
                exclude=lambda n: n.startswith("META-INF/") or n.startswith("META-INF\\"),
                flatten_natives=True,
            )
            count += 1
        except (zipfile.BadZipFile, OSError) as exc:
            LOG.warning("解压 native 失败 %s：%s", archive, exc)
    LOG.info("已解压 %d 个 native 包", count)
    return count


# -------------------------------------------------------------------- 主流程


def fetch_version_json(
    game_dir: Path,
    version_id: str,
    mirror_key: str = DEFAULT_MIRROR,
    force: bool = False,
) -> Path:
    """从远程清单下载并保存版本 JSON；返回本地路径。"""
    mirror = get_mirror(mirror_key)
    target = P.version_json(game_dir, version_id)
    if target.is_file() and not force:
        return target

    manifest.refresh(mirror_key)
    summary = manifest.get(version_id)
    if not summary or not summary.url:
        raise FileNotFoundError(f"版本清单中没有 {version_id}（可能是自定义版本名）")

    # 镜像源：把官方域名替换为镜像域名；失败自动回退官方
    mirrors_url = summary.url
    if mirror.key != "official":
        mirrors_url = summary.url.replace(
            "https://piston-meta.mojang.com", BMCLAPI_ROOT
        ).replace("https://launchermeta.mojang.com", BMCLAPI_ROOT)

    ensure_dir(target.parent)
    data, used = get_json_with_fallback(list(dict.fromkeys([mirrors_url, summary.url])))
    LOG.debug("版本 JSON 来源：%s", used)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    LOG.info("版本 JSON 已保存：%s", target)
    return target


def describe_failures(stats) -> str:
    """把下载失败项整理成给人看的多行文本（最多 15 条）。"""
    if not getattr(stats, "failures", None):
        return ""
    lines = [f"共 {len(stats.failures)} 个文件下载失败："]
    for result in stats.failures[:15]:
        err = str(result.error).splitlines()[0] if result.error else "未知错误"
        lines.append(f"· {result.task.name} —— {err[:160]}")
    if len(stats.failures) > 15:
        lines.append(f"… 以及另外 {len(stats.failures) - 15} 个")
    return "\n".join(lines)


def install_version(
    game_dir: Path,
    version_id: str,
    mirror_key: str = DEFAULT_MIRROR,
    with_assets: bool = True,
    download_manager=None,
    on_stage: Optional[Callable[[str], None]] = None,
    on_progress: Optional[ProgressCallback] = None,
) -> bool:
    """完整安装一个版本；返回是否成功。

    会自动挑选当前网络下真正可用的下载源，失败时通过 `on_stage` 回报明细，
    便于 UI 直接呈现给用户而不是只说一句「失败」。
    """
    from .http import DownloadManager, available_mirror

    def stage(msg: str) -> None:
        LOG.info(msg)
        if on_stage:
            on_stage(msg)

    # 源健康检查：选不通就把整次安装切到另一个源，避免「刚开始就全线失败」
    actual = available_mirror(mirror_key)
    if actual != mirror_key:
        stage(
            f"下载源 {MIRRORS.get(mirror_key).name if mirror_key in MIRRORS else mirror_key} "
            f"当前不可用，已自动切换到 {MIRRORS[actual].name}"
        )
        mirror_key = actual

    stage(f"获取 {version_id} 版本信息…")
    ensure_dir(game_dir)
    fetch_version_json(game_dir, version_id, mirror_key, force=True)

    stage("收集待下载文件…")
    tasks, natives = collect_install_tasks(game_dir, version_id, mirror_key, with_assets)

    stats = None
    if tasks:
        stage(f"下载 {len(tasks)} 个文件…")
        manager = download_manager or DownloadManager()

        def wrapped(done: int, total: int, _delta: int) -> None:
            if on_progress:
                on_progress("下载文件", done, total)

        stats = manager.run(tasks, wrapped)
        if manager.cancelled:
            stage("已取消安装")
            return False
        if stats.failures:
            hard = [f for f in stats.failures if not f.task.optional]
            if hard:
                stage(f"有 {len(hard)} 个必需文件下载失败")
                stage(describe_failures(stats))
                return False
            stage(f"{len(stats.failures)} 个可选文件下载失败，已忽略")

    stage("解压本地库（Natives）…")
    apply_natives(natives)

    stage(f"{version_id} 安装完成")
    return True


# -------------------------------------------------------------------- 校验


def verify_version(
    game_dir: Path, version_id: str, mirror_key: str = DEFAULT_MIRROR
) -> List[DownloadTask]:
    """检查版本完整性，返回缺失/损坏的文件任务列表。"""
    tasks, _ = collect_install_tasks(game_dir, version_id, mirror_key, with_assets=True)
    return [t for t in tasks if not t.exists_ok]
