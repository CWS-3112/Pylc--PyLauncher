"""Mod 加载器：Fabric 与 Forge 的安装。

* **Fabric**：直接由 meta 接口取得 `profile/json`，拼出继承原版的版本 JSON 并下载其库文件，
  整个过程由本启动器自己完成，不依赖外部安装器。
* **Forge**：下载官方 Installer，用 `java -jar installer.jar --installClient <game_dir>`
  完成安装（与官方推荐方式一致）；若失败则回退为「解包 installer 内的 version.json +
  下载 install_profile.json 中的库」。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from . import paths as P
from .constants import BMCLAPI, DEFAULT_MIRROR, MIRRORS, OFFICIAL, Mirror
from .http import DownloadManager, DownloadTask, get_json_with_fallback, session
from .utils import LOG, ensure_dir, sanitize_name

ProgressCallback = Callable[[str, int, int], None]   # (阶段, 已完成, 总数)


# -------------------------------------------------------------------- Fabric


@dataclass
class FabricLoaderInfo:
    version: str
    maven: str
    stable: bool = False

    @property
    def display(self) -> str:
        return f"{self.version}" + ("（稳定）" if self.stable else "（预览）")


def _backup(key: str) -> Mirror:
    return MIRRORS[OFFICIAL if key != OFFICIAL else BMCLAPI]


def list_fabric_loaders(game_version: str, mirror_key: str = DEFAULT_MIRROR) -> List[FabricLoaderInfo]:
    mirror = MIRRORS.get(mirror_key) or MIRRORS[DEFAULT_MIRROR]
    backup = _backup(mirror.key)
    data, _used = get_json_with_fallback([
        f"{mirror.fabric_meta}/versions/loader/{game_version}",
        f"{backup.fabric_meta}/versions/loader/{game_version}",
    ])
    if not data:
        raise RuntimeError(f"没有查询到适用于 {game_version} 的 Fabric 版本")
    out: List[FabricLoaderInfo] = []
    for item in data:
        ver = item.get("version")
        if not ver:
            continue
        out.append(
            FabricLoaderInfo(
                version=ver,
                maven=item.get("maven", f"net.fabricmc:fabric-loader:{ver}"),
                stable=bool(item.get("stable", False)),
            )
        )
    return out


def fabric_version_id(game_version: str, loader: str) -> str:
    return f"fabric-loader-{loader}-{game_version}"


def install_fabric(
    game_dir: Path,
    game_version: str,
    loader: str,
    mirror_key: str = DEFAULT_MIRROR,
    name: Optional[str] = None,
    on_progress: Optional[ProgressCallback] = None,
) -> str:
    """安装 Fabric，返回新建的版本 id。"""
    mirror = MIRRORS.get(mirror_key) or MIRRORS[DEFAULT_MIRROR]
    vid = name or fabric_version_id(game_version, loader)

    def stage(msg: str, done: int = 0, total: int = 0) -> None:
        LOG.info(msg)
        if on_progress:
            on_progress(msg, done, total)

    stage("获取 Fabric 版本配置…")
    backup = _backup(mirror.key)
    profile, _used = get_json_with_fallback([
        f"{mirror.fabric_meta}/versions/loader/{game_version}/{loader}/profile/json",
        f"{backup.fabric_meta}/versions/loader/{game_version}/{loader}/profile/json",
    ])
    profile["id"] = vid
    profile.setdefault("inheritsFrom", game_version)

    target_dir = P.version_dir(game_dir, vid)
    ensure_dir(target_dir)
    js = target_dir / f"{sanitize_name(vid)}.json"
    js.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    stage(f"已写入 {js.name}")

    # 收集 Fabric 自身库文件
    tasks: List[DownloadTask] = []
    for lib in profile.get("libraries", []):
        art = (lib.get("downloads") or {}).get("artifact")
        rel = (art or {}).get("path") or _maven_path(lib["name"])
        base = lib.get("url") or mirror.libraries
        if mirror_key != "official" and "fabricmc.net" in str(base):
            base = mirror.libraries
        dest = P.libraries_dir(game_dir) / rel
        tasks.append(
            DownloadTask(
                name=lib["name"],
                url=f"{base.rstrip('/')}/{rel}",
                dest=dest,
                sha1=(art or {}).get("sha1", ""),
                size=(art or {}).get("size", 0),
                category="library",
            )
        )

    tasks = [t for t in tasks if not t.exists_ok]
    if tasks:
        stage(f"下载 {len(tasks)} 个 Fabric 组件…", 0, len(tasks))
        stats = DownloadManager().run(
            tasks,
            lambda done, total, _d: on_progress and on_progress("下载 Fabric 组件", done, total),
        )
        if stats.failures:
            raise RuntimeError(f"{len(stats.failures)} 个 Fabric 组件下载失败")

    # 用 Fabric profile 里的 jvm 参数替换原版（profile 已包含 arguments）
    stage(f"Fabric {loader} 安装完成：{vid}")
    return vid


def _maven_path(name: str) -> str:
    parts = name.split(":")
    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = parts[3] if len(parts) > 3 else ""
    fname = f"{artifact}-{version}" + (f"-{classifier}" if classifier else "") + ".jar"
    return "/".join([group.replace(".", "/"), artifact, version, fname])


# -------------------------------------------------------------------- Forge


@dataclass
class ForgeVersionInfo:
    mcversion: str
    version: str        # 完整 forge 版本，如 "1.20.4-49.0.12"
    build: int = 0
    branch: str = ""
    latest: bool = False
    recommended: bool = False

    @property
    def display(self) -> str:
        flags = []
        if self.latest:
            flags.append("最新")
        if self.recommended:
            flags.append("推荐")
        suffix = f"（{'/'.join(flags)}）" if flags else ""
        return f"{self.version}{suffix}"


def list_forge_versions(game_version: str, mirror_key: str = DEFAULT_MIRROR) -> List[ForgeVersionInfo]:
    """列出某 MC 版本可用的 Forge 构建。"""
    mirror = MIRRORS.get(mirror_key) or MIRRORS[DEFAULT_MIRROR]
    out: List[ForgeVersionInfo] = []

    if mirror_key != "official":
        try:
            data = session.get_json(f"{mirror.forge}/minecraft/{game_version}")
            for item in data:
                out.append(
                    ForgeVersionInfo(
                        mcversion=item.get("mcversion", game_version),
                        version=item.get("version", ""),
                        build=int(item.get("build", 0) or 0),
                        branch=item.get("branch", "") or "",
                    )
                )
        except Exception as exc:  # noqa: BLE001
            LOG.warning("BMCLAPI Forge 列表获取失败：%s", exc)

    if not out:
        # 官方 maven-metadata
        xml = session.request("GET", f"{mirror.forge}maven-metadata.xml").text
        root = ET.fromstring(xml)
        for v in root.findall(".//versioning/versions/version"):
            text = (v.text or "")
            if text.startswith(f"{game_version}-"):
                out.append(ForgeVersionInfo(mcversion=game_version, version=text))

    out = [o for o in out if o.version]
    out.sort(key=lambda o: _forge_sort_key(o), reverse=True)
    if out:
        out[0].latest = True
        rec = next((o for o in out if o.branch == "recommended" or "recommended" in o.version), None)
        if rec:
            rec.recommended = True
        else:
            out[0].recommended = True
    return out


def _forge_sort_key(info: ForgeVersionInfo) -> tuple:
    nums = re.findall(r"\d+", info.version.replace(f"{info.mcversion}-", ""))
    return tuple(int(n) for n in nums[:4]) or (0,)


def forge_installer_url(info: ForgeVersionInfo, mirror_key: str = DEFAULT_MIRROR) -> str:
    mirror = MIRRORS.get(mirror_key) or MIRRORS[DEFAULT_MIRROR]
    if mirror_key != "official":
        return (
            f"{mirror.forge}/download?mcversion={info.mcversion}"
            f"&version={info.version}&category=installer&format=jar"
        )
    return f"{mirror.forge}{info.mcversion}-{info.version}/forge-{info.mcversion}-{info.version}-installer.jar"


def forge_version_id(info: ForgeVersionInfo) -> str:
    return f"forge-{info.mcversion}-{info.version}"


def install_forge(
    game_dir: Path,
    info: ForgeVersionInfo,
    java_path: Path | str,
    mirror_key: str = DEFAULT_MIRROR,
    on_progress: Optional[ProgressCallback] = None,
) -> str:
    """调用官方 Installer 安装 Forge，返回版本 id。"""
    from .utils import is_windows

    def stage(msg: str) -> None:
        LOG.info(msg)
        if on_progress:
            on_progress(msg, 0, 0)

    tmp = ensure_dir(P.temp_dir())
    installer = tmp / f"forge-{info.mcversion}-{info.version}-installer.jar"
    stage("下载 Forge Installer…")
    session.download(forge_installer_url(info, mirror_key), installer)

    stage("运行 Forge Installer（需要联网，请耐心等待）…")
    cmd = [str(java_path), "-jar", str(installer), "--installClient", str(game_dir)]
    flags = subprocess.CREATE_NO_WINDOW if is_windows() else 0  # type: ignore[attr-defined]
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=900,
        creationflags=flags,
    )
    output = (proc.stdout or b"").decode("utf-8", errors="replace")
    if proc.returncode != 0:
        LOG.error("Forge Installer 输出：\n%s", output[-4000:])
        stage("安装器失败，回退为手动解包…")
        return _install_forge_manual(game_dir, info, installer, mirror_key, on_progress)

    # Installer 自己会创建 versions/<forge-x.y.z-...>/
    vid = _detect_forge_version_id(game_dir, info)
    stage(f"Forge 安装完成：{vid}")
    return vid


def _detect_forge_version_id(game_dir: Path, info: ForgeVersionInfo) -> str:
    versions_root = game_dir / "versions"
    if not versions_root.is_dir():
        return forge_version_id(info)
    for sub in versions_root.iterdir():
        if sub.is_dir() and sub.name.startswith(f"forge-{info.mcversion}-{info.version}"):
            return sub.name
    # 形如 1.20.4-forge-49.0.12
    for sub in versions_root.iterdir():
        if sub.is_dir() and info.version in sub.name and "forge" in sub.name.lower():
            return sub.name
    return forge_version_id(info)


def _install_forge_manual(
    game_dir: Path,
    info: ForgeVersionInfo,
    installer: Path,
    mirror_key: str = DEFAULT_MIRROR,
    on_progress: Optional[ProgressCallback] = None,
) -> str:
    """回退方案：解包 installer 内的 version.json / install_profile.json 并补齐库文件。"""
    mirror = MIRRORS.get(mirror_key) or MIRRORS[DEFAULT_MIRROR]
    vid = forge_version_id(info)
    target_dir = ensure_dir(P.version_dir(game_dir, vid))

    with zipfile.ZipFile(installer) as zf:
        names = zf.namelist()
        if "version.json" in names:
            data = json.loads(zf.read("version.json"))
            data["id"] = vid
            (target_dir / f"{sanitize_name(vid)}.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        elif "install_profile.json" in names:
            prof = json.loads(zf.read("install_profile.json"))
            data = {
                "id": vid,
                "inheritsFrom": prof.get("minecraft", info.mcversion),
                "type": "release",
                "mainClass": prof.get("mainClass") or prof.get("versionInfo", {}).get("mainClass", ""),
                "libraries": prof.get("libraries", []),
                "arguments": prof.get("versionInfo", {}).get("arguments", {}),
                "minecraftArguments": prof.get("versionInfo", {}).get("minecraftArguments", ""),
            }
            (target_dir / f"{sanitize_name(vid)}.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        else:
            raise RuntimeError("Installer 中找不到版本描述文件，无法离线安装")

    LOG.info("已解包 Forge 版本描述：%s", vid)
    if on_progress:
        on_progress(f"已生成版本 {vid}（缺少预处理步骤，若启动异常请改用官方安装器）", 1, 1)
    return vid


# -------------------------------------------------------------------- 卸载


def uninstall_version(game_dir: Path, version_id: str) -> None:
    """删除一个版本目录（不动共享的 libraries / assets）。"""
    target = P.version_dir(game_dir, version_id)
    if target.is_dir():
        shutil.rmtree(target, ignore_errors=True)
        LOG.info("已删除版本：%s", version_id)
