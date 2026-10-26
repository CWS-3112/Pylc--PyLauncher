"""Minecraft version.json 的 `rules` 解析与 Maven 坐标 → 文件路径转换。"""

from __future__ import annotations

import platform
import re
from typing import Any, Dict, Iterable, List, Optional

from .utils import is_64bit, normal_arch

# -------------------------------------------------------------------- 环境


def os_name() -> str:
    """返回 version.json 中使用的操作系统名。"""
    s = platform.system().lower()
    if s == "windows":
        return "windows"
    if s == "darwin":
        return "osx"
    if s == "linux":
        return "linux"
    return s or "unknown"


def os_version() -> str:
    """Windows 上返回 `10.0.19045` 形式；其它平台返回内核版本号。"""
    try:
        return platform.version()
    except Exception:  # noqa: BLE001
        return ""


# -------------------------------------------------------------------- 规则


def check_rule(rule: Dict[str, Any], features: Optional[Dict[str, bool]] = None) -> bool:
    """判断单条 rule 的条件是否满足（不关心 action）。"""
    features = features or {}

    if "os" in rule:
        spec = rule["os"]
        if spec.get("name") and spec["name"] != os_name():
            return False
        if spec.get("arch"):
            want = spec["arch"]
            arch = normal_arch()
            # "x86" 在 64 位 Windows 上通常指 32 位；这里按字面比较
            if want == "x86" and arch not in ("x86", "i386", "i686"):
                return False
            if want != "x86" and want != arch:
                return False
        if spec.get("version"):
            pattern = spec["version"]
            if not re.search(pattern, os_version()):
                return False

    if "features" in rule:
        for key, expected in rule["features"].items():
            if bool(features.get(key, False)) != bool(expected):
                return False

    return True


def is_allowed(
    rules: Optional[Iterable[Dict[str, Any]]],
    features: Optional[Dict[str, bool]] = None,
) -> bool:
    """按 Mojang 语义求值：默认 `disallow`，最后一条命中的 rule 决定结果。"""
    allowed = False
    rules = list(rules or [])
    if not rules:
        return True     # 没有 rules 约束视为允许（libraries 的常见情形）
    for rule in rules or []:
        if check_rule(rule, features):
            allowed = rule.get("action") == "allow"
    return allowed


# -------------------------------------------------------------------- Maven


def artifact_to_path(name: str, native_suffix: str = "") -> str:
    """`group:artifact:version[:classifier[@ext]]` → 仓库相对路径。

    例：`com.mojang:authlib:6.0.54` → `com/mojang/authlib/6.0.54/authlib-6.0.54.jar`
        `org.lwjgl:lwjgl:3.3.3:natives-windows` → `org/lwjgl/lwjgl/3.3.3/lwjgl-3.3.3-natives-windows.jar`
    """
    parts = name.split(":")
    if len(parts) < 3:
        raise ValueError(f"非法的 Maven 坐标：{name}")
    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = ""
    ext = "jar"
    if len(parts) >= 4 and parts[3]:
        tail = parts[3]
        if "@" in tail:
            classifier, ext = tail.split("@", 1)
        else:
            classifier = tail

    filename = f"{artifact}-{version}"
    if classifier:
        filename += f"-{classifier}"
    if native_suffix and not classifier:
        filename += f"-{native_suffix}"
    filename += f".{ext}"

    return "/".join([group.replace(".", "/"), artifact, version, filename])


def artifact_to_url(name: str, base_url: str) -> str:
    return base_url.rstrip("/") + "/" + artifact_to_path(name)


def library_path(lib: Dict[str, Any]) -> str:
    """从 library 条目中取得仓库相对路径（优先 `downloads.artifact.path`）。"""
    art = (lib.get("downloads") or {}).get("artifact")
    if art and art.get("path"):
        return art["path"]
    return artifact_to_path(lib["name"])


# -------------------------------------------------------------------- Natives


_NATIVE_BASE = {
    "windows": "natives-windows",
    "osx": "natives-macos",
    "linux": "natives-linux",
}


def native_classifier() -> str:
    """当前平台的基础 native 分类器（不含架构后缀）。"""
    return _NATIVE_BASE.get(os_name(), "")


def native_candidates() -> List[str]:
    """本平台按优先级排序的 native 分类器。

    * 1.19 起：LWJGL native 是 **独立的 library 条目**，classifier 写在 Maven 坐标里，
      例如 `org.lwjgl:lwjgl-glfw:3.3.2:natives-windows-arm64`。
    * 1.18 及更早：主条目上挂 `natives` 映射 + `downloads.classifiers`。
    """
    base = native_classifier()
    if not base:
        return []
    arch = normal_arch()
    if arch == "arm64":
        return [f"{base}-arm64", base]
    if arch == "x86":
        return [f"{base}-x86", base]
    return [base]                       # x86_64 使用不带后缀的基础名


def classifier_of_name(name: str) -> str:
    """取 Maven 坐标中的 classifier（第 4 段）。"""
    parts = name.split(":")
    return parts[3] if len(parts) >= 4 else ""


def pick_native(library: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """判断某个 library 是否为当前平台需要的 native 包。

    返回 `{"classifier", "path", "url", "sha1", "size", "standalone"}` 或 None。
    `standalone=True` 表示 native 就是该条目的主 artifact（新格式），不必再建下载任务。
    """
    name = library.get("name", "")
    downloads = library.get("downloads") or {}
    candidates = native_candidates()
    if not candidates:
        return None

    # ---------------- 新格式（1.19+）：classifier 直接在坐标里
    cls = classifier_of_name(name)
    if cls.startswith("natives-"):
        if cls not in candidates:
            return None
        art = downloads.get("artifact") or {}
        return {
            "classifier": cls,
            "path": art.get("path") or artifact_to_path(name),
            "url": art.get("url", ""),
            "sha1": art.get("sha1", ""),
            "size": art.get("size", 0),
            "standalone": True,
        }

    # ---------------- 旧格式：natives 映射 + downloads.classifiers
    natives = library.get("natives") or {}
    if not natives:
        return None
    classifiers = downloads.get("classifiers") or {}
    for cand in candidates:
        for value in natives.values():
            resolved = value.replace("${arch}", "64" if is_64bit() else "32")
            if resolved != cand:
                continue
            info = classifiers.get(cand) or classifiers.get(resolved)
            if info:
                return {
                    "classifier": cand,
                    "path": info.get("path"),
                    "url": info.get("url", ""),
                    "sha1": info.get("sha1", ""),
                    "size": info.get("size", 0),
                    "standalone": False,
                }
            # 1.7.x 及更早没有 downloads 段，路径需自行推导
            return {
                "classifier": cand,
                "path": None,
                "url": "",
                "sha1": "",
                "size": 0,
                "standalone": False,
            }
    return None
