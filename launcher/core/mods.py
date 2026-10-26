"""Mod 管理：扫描实例 mods 目录，解析元数据，启用 / 禁用 / 删除。

支持三种常见元数据格式：

* Fabric —— `fabric.mod.json`
* Forge 1.13+ —— `META-INF/mods.toml`
* Forge 1.7~1.12 —— `mcmod.info`

禁用通过重命名为 `<name>.jar.disabled` 实现（HMCL 同样做法），不破坏原文件。
"""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .utils import LOG

DISABLED_SUFFIX = ".disabled"


@dataclass
class ModInfo:
    path: Path
    file_name: str
    enabled: bool = True
    mod_id: str = ""
    name: str = ""
    version: str = ""
    description: str = ""
    authors: List[str] = field(default_factory=list)
    loader: str = "unknown"     # fabric / forge / unknown
    mc_version: str = ""
    size: int = 0
    parse_error: str = ""

    @property
    def display_name(self) -> str:
        return self.name or self.mod_id or self.file_name

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["path"] = str(self.path)
        return d


# -------------------------------------------------------------------- 解析


def _parse_fabric(zf: zipfile.ZipFile) -> Dict:
    raw = json.loads(zf.read("fabric.mod.json").decode("utf-8", errors="replace"))
    depends = raw.get("depends") or {}
    return {
        "mod_id": raw.get("id", ""),
        "name": raw.get("name", ""),
        "version": raw.get("version", ""),
        "description": _first_line(raw.get("description", "")),
        "authors": [a if isinstance(a, str) else a.get("name", "") for a in raw.get("authors", [])],
        "loader": "fabric",
        "mc_version": depends.get("minecraft", "") if isinstance(depends, dict) else "",
    }


_TOML_MODS = re.compile(r"\[\[mods\]\](.*?)(?=\n\[|\Z)", re.DOTALL)


def _parse_mods_toml(zf: zipfile.ZipFile) -> Dict:
    text = zf.read("META-INF/mods.toml").decode("utf-8", errors="replace")
    block = _TOML_MODS.search(text)
    chunk = block.group(1) if block else text

    def find(key: str, default: str = "") -> str:
        m = re.search(rf'^\s*{key}\s*=\s*"([^"]*)"', chunk, re.MULTILINE)
        return m.group(1) if m else default

    loader = "forge"
    m = re.search(r'^\s*modLoader\s*=\s*"([^"]*)"', text, re.MULTILINE)
    if m and m.group(1) != "javafml":
        loader = m.group(1)

    return {
        "mod_id": find("modId"),
        "name": find("displayName") or find("modId"),
        "version": find("version"),
        "description": _first_line(find("description")),
        "authors": [a.strip() for a in re.findall(r'"([^"]+)"', find("authors", ""))] if find("authors") else [],
        "loader": loader,
        "mc_version": find("mcversion"),
    }


def _parse_mcmod_info(zf: zipfile.ZipFile) -> Dict:
    raw = zf.read("mcmod.info").decode("utf-8", errors="replace")
    # 一些老 mod 的 mcmod.info 带 BOM 或非法逗号
    raw = raw.lstrip("\ufeff").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = json.loads(re.sub(r",\s*([}\]])", r"\1", raw))
    entry = data[0] if isinstance(data, list) and data else (data.get("modList", [{}])[0] if isinstance(data, dict) else {})
    return {
        "mod_id": entry.get("modid", ""),
        "name": entry.get("name", ""),
        "version": entry.get("version", ""),
        "description": _first_line(entry.get("description", "")),
        "authors": entry.get("authorList", []) or [],
        "loader": "forge",
        "mc_version": entry.get("mcversion", ""),
    }


def _first_line(text: str) -> str:
    if not text:
        return ""
    return str(text).strip().splitlines()[0][:300]


def parse_mod_file(path: Path) -> ModInfo:
    """解析单个 mod 文件，失败时返回带 parse_error 的 ModInfo。"""
    info = ModInfo(
        path=path,
        file_name=path.name,
        enabled=not path.name.endswith(DISABLED_SUFFIX),
        size=path.stat().st_size if path.exists() else 0,
    )
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "fabric.mod.json" in names:
                info.__dict__.update(_parse_fabric(zf))
            elif "META-INF/mods.toml" in names:
                info.__dict__.update(_parse_mods_toml(zf))
            elif "mcmod.info" in names:
                info.__dict__.update(_parse_mcmod_info(zf))
            else:
                info.parse_error = "未找到已知元数据文件"
    except Exception as exc:  # noqa: BLE001
        info.parse_error = f"{type(exc).__name__}: {exc}"
        LOG.debug("解析 mod 失败 %s：%s", path, exc)
    return info


# -------------------------------------------------------------------- 扫描


def scan_mods(mods_dir: Path) -> List[ModInfo]:
    if not mods_dir.is_dir():
        return []
    out: List[ModInfo] = []
    for f in sorted(mods_dir.iterdir()):
        if not f.is_file():
            continue
        if f.suffix.lower() not in (".jar", ".zip") and not f.name.endswith(DISABLED_SUFFIX):
            continue
        out.append(parse_mod_file(f))
    return out


# -------------------------------------------------------------------- 操作


def set_mod_enabled(mod: ModInfo, enabled: bool) -> ModInfo:
    """启用/禁用（重命名），返回更新后的 ModInfo。"""
    if mod.enabled == enabled:
        return mod
    path = mod.path
    if enabled:
        new_path = path.with_name(path.name[: -len(DISABLED_SUFFIX)] if path.name.endswith(DISABLED_SUFFIX) else path.name)
    else:
        new_path = path.with_name(path.name + DISABLED_SUFFIX)

    if new_path.exists():
        raise FileExistsError(f"目标文件已存在：{new_path.name}")

    path.rename(new_path)
    LOG.info("Mod %s → %s", "启用" if enabled else "禁用", new_path.name)
    return parse_mod_file(new_path)


def delete_mod(mod: ModInfo) -> None:
    try:
        mod.path.unlink()
        LOG.info("已删除 mod：%s", mod.path.name)
    except OSError as exc:
        raise OSError(f"删除失败：{exc}") from exc


def import_mods(dest_dir: Path, sources: List[Path]) -> List[ModInfo]:
    """把外部 jar 复制进 mods 目录。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    imported: List[ModInfo] = []
    for src in sources:
        if not src.is_file():
            continue
        target = dest_dir / src.name
        if target.exists():
            stem, suffix = src.stem, src.suffix
            i = 1
            while target.exists():
                target = dest_dir / f"{stem}_{i}{suffix}"
                i += 1
        import shutil

        shutil.copy2(src, target)
        imported.append(parse_mod_file(target))
    return imported
