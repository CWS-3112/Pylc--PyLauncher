"""材质包与光影管理。

目录约定（与官方启动器一致）：

* 材质包：`<实例目录>/resourcepacks`
* 光影：`<实例目录>/shaderpacks`（需要 Iris / OptiFine 才生效）

数据源可能是 zip（直接放入即可）也可能是目录（把整个目录打包成 zip 才能被游戏识别），
因此这里统一用「文件名 + 是否 `.disabled` 后缀」来表示启用状态。
"""

from __future__ import annotations

import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from .utils import LOG, ensure_dir, human_size, safe_join, sanitize_name

PACK_EXTS = (".zip", ".mcpack")
DISABLED_SUFFIX = ".disabled"


@dataclass
class ResourcePack:
    """一个材质包 / 光影包。"""

    path: Path
    name: str
    size: int
    enabled: bool
    kind: str                 # resourcepack / shaderpack
    description: str = ""

    @property
    def size_text(self) -> str:
        return human_size(self.size) if self.size else "-"


def packs_dir(instance_dir: Path, kind: str) -> Path:
    sub = "shaderpacks" if kind == "shaderpack" else "resourcepacks"
    return ensure_dir(Path(instance_dir) / sub)


def _read_description(path: Path) -> str:
    """从 zip 内的 `pack.mcmeta` 里读一句描述（读不到就返回空）。"""
    try:
        if not zipfile.is_zipfile(path):
            return ""
        import json

        with zipfile.ZipFile(path) as zf:
            for name in ("pack.mcmeta", "shaders/shaders.properties"):
                if name in zf.namelist():
                    raw = zf.read(name)[:4096]
                    if name.endswith(".mcmeta"):
                        data = json.loads(raw.decode("utf-8", errors="ignore"))
                        desc = ((data.get("pack") or {}).get("description") or "")
                        return desc if isinstance(desc, str) else str(desc)[:120]
                    return "光影包"
    except Exception:  # noqa: BLE001 - 描述读不出来不影响使用
        return ""
    return ""


def scan(instance_dir: Path, kind: str) -> List[ResourcePack]:
    """扫描某个实例目录下的材质包 / 光影包。"""
    root = packs_dir(instance_dir, kind)
    out: List[ResourcePack] = []
    try:
        entries = sorted(root.iterdir())
    except OSError as exc:
        LOG.warning("扫描 %s 失败：%s", root, exc)
        return out

    for entry in entries:
        if entry.is_dir():
            continue
        name = entry.name
        enabled = not name.lower().endswith(DISABLED_SUFFIX)
        display = name
        if not enabled and name.lower().endswith(DISABLED_SUFFIX):
            display = name[: -len(DISABLED_SUFFIX)]
        # 只认压缩包；其余文件（如说明文本）忽略
        if not display.lower().endswith(PACK_EXTS):
            continue
        try:
            size = entry.stat().st_size
        except OSError:
            size = 0
        out.append(
            ResourcePack(
                path=entry,
                name=display,
                size=size,
                enabled=enabled,
                kind=kind,
                description=_read_description(entry) if enabled or True else "",
            )
        )
    return out


def set_enabled(pack: ResourcePack, enabled: bool) -> ResourcePack:
    """启用 / 禁用（改名加 `.disabled` 后缀）。"""
    src = Path(pack.path)
    if src.name.lower().endswith(DISABLED_SUFFIX):
        target = src.with_name(src.name[: -len(DISABLED_SUFFIX)])
    else:
        target = src.with_name(src.name + DISABLED_SUFFIX)

    if enabled and src.name.lower().endswith(DISABLED_SUFFIX):
        src.rename(target)
        return ResourcePack(target, target.name, pack.size, True, pack.kind, pack.description)
    if not enabled and not src.name.lower().endswith(DISABLED_SUFFIX):
        src.rename(target)
        return ResourcePack(target, target.name, pack.size, False, pack.kind, pack.description)
    return pack


def install_from_file(
    src: Path,
    instance_dir: Path,
    kind: str,
    on_conflict: Optional[Callable[[Path], bool]] = None,
) -> Path:
    """把本地 zip 安装为材质包 / 光影包；返回落盘路径。"""
    src = Path(src)
    if not src.is_file():
        raise FileNotFoundError(f"文件不存在：{src}")
    if not zipfile.is_zipfile(src):
        raise ValueError("只支持 zip 格式的材质包 / 光影包")

    dest_dir = packs_dir(instance_dir, kind)
    # 文件名来自用户/远端，必须过安全化，避免写到目录之外
    target = safe_join(dest_dir, sanitize_name(src.name))
    if target.exists() and on_conflict is not None and not on_conflict(target):
        raise FileExistsError(str(target))
    ensure_dir(dest_dir)
    shutil.copy2(src, target)
    LOG.info("已安装包：%s", target)
    return target


def install_from_url(
    url: str,
    filename: str,
    instance_dir: Path,
    kind: str,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> Path:
    """从远端安装包（Modrinth 下载走这里），落盘前过防火墙与路径约束。"""
    from .http import session
    from .netguard import guard

    guard.check_url(url)
    dest_dir = packs_dir(instance_dir, kind)
    target = safe_join(dest_dir, sanitize_name(filename))
    guard.check_download(target, base=dest_dir)
    ensure_dir(dest_dir)
    session.download(url, target)
    if on_progress:
        on_progress(1, 1)
    LOG.info("已从远端安装包：%s", target)
    return target


def remove(pack: ResourcePack) -> bool:
    try:
        Path(pack.path).unlink()
        return True
    except OSError as exc:
        LOG.warning("删除包失败：%s", exc)
        return False


__all__ = [
    "PACK_EXTS",
    "ResourcePack",
    "install_from_file",
    "install_from_url",
    "packs_dir",
    "remove",
    "scan",
    "set_enabled",
]
