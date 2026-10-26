"""通用工具：日志、哈希、路径安全化、跨平台辅助。"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sys
import zipfile
from pathlib import Path
from typing import Callable, Iterable, Iterator, List, Optional

LOG = logging.getLogger("pylauncher")


def setup_logging(level: int = logging.INFO, log_file: Optional[Path] = None) -> None:
    """配置根日志器（只做一次）。

    除控制台输出外，还会在 `<launcher_root>/logs/launcher.log` 追加一份，
    这样即使用 `pythonw.exe`（无控制台）或双击快捷方式启动，也能事后排查。
    """
    root = logging.getLogger()
    if root.handlers:
        return
    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(fmt)
    root.addHandler(handler)

    # 文件日志（失败不影响启动）
    try:
        if log_file is None:
            from . import paths as _P

            log_file = _P.logs_dir() / "launcher.log"
        log_file = Path(log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        if log_file.is_file() and log_file.stat().st_size > 2 * 1024 * 1024:
            log_file.unlink()
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except Exception:  # noqa: BLE001  # 只读目录 / 磁盘满等场景忽略
        pass

    root.setLevel(level)


# -------------------------------------------------------------------- 哈希


def sha1_of_file(path: Path | str, chunk: int = 1 << 20) -> str:
    """计算文件 SHA1（大文件友好）。"""
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def sha1_of_bytes(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


# -------------------------------------------------------------------- 路径


_UNSAFE = re.compile(r'[<>:"|?*/\\\x00-\x1f]')


def sanitize_name(name: str, replacement: str = "_") -> str:
    """把任意字符串变成可安全用作目录名的形式（Windows 友好）。"""
    name = _UNSAFE.sub(replacement, name).strip().rstrip(".")
    return name or "unnamed"


def ensure_dir(path: Path | str) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def human_size(num: float) -> str:
    """字节数转人类可读字符串。"""
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < step:
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.2f} {unit}"
        num /= step
    return f"{num:.2f} PB"


# -------------------------------------------------------------------- 压缩包


NATIVE_EXTS = (".dll", ".so", ".dylib", ".jnilib", ".a")


def extract_zip(
    archive: Path,
    dest: Path,
    exclude: Optional[Callable[[str], bool]] = None,
    flatten_natives: bool = False,
) -> List[Path]:
    """解压 zip，返回解压出的文件列表；`exclude` 返回 True 的条目会被跳过。

    `flatten_natives=True` 时，对于带目录层级的原生库（LWJGL 3 的
    `windows/x64/org/lwjgl/glfw/glfw.dll` 这类布局），除了按原结构解压，
    还会在根目录额外放一份同名文件。这样无论运行时是走扁平的
    `System.loadLibrary` 还是带子目录搜索的加载器，都能找到库文件。
    """
    extracted: List[Path] = []
    flat_seen: set = set()
    ensure_dir(dest)
    root = dest.resolve()

    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            name = info.filename
            if name.endswith("/"):
                continue
            if exclude and exclude(name):
                continue
            # 防止 zip slip
            target = (dest / name).resolve()
            if not str(target).startswith(str(root)):
                continue
            ensure_dir(target.parent)
            with zf.open(info) as src, open(target, "wb") as dst:
                dst.write(src.read())
            extracted.append(target)

            if flatten_natives and "/" in name.replace("\\", "/"):
                base = Path(name.replace("\\", "/")).name
                if base.lower().endswith(NATIVE_EXTS):
                    key = base.lower()
                    if key not in flat_seen:
                        flat_seen.add(key)
                        flat_target = root / base
                        with zf.open(info) as src, open(flat_target, "wb") as dst:
                            dst.write(src.read())
                        extracted.append(flat_target)
    return extracted


# -------------------------------------------------------------------- 迭代


def chunked(iterable: Iterable, size: int) -> Iterator[List]:
    buf: List = []
    for item in iterable:
        buf.append(item)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf


# -------------------------------------------------------------------- 平台


def is_windows() -> bool:
    return sys.platform.startswith("win")


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def normal_arch() -> str:
    """返回 Minecraft 规则里使用的架构名（x86 / x86_64 / arm64 …）。"""
    machine = (os.uname().machine if hasattr(os, "uname") else os.environ.get("PROCESSOR_ARCHITECTURE", "")).lower()
    aliases = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "x86_64": "x86_64",
        "i386": "x86",
        "i686": "x86",
        "x86": "x86",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    return aliases.get(machine, machine)


def is_64bit() -> bool:
    return normal_arch() in ("x86_64", "arm64", "ia64", "ppc64")
