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
# Windows 保留设备名：COM1 / NUL / CON … 不能作为目录名
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
             *(f"lpt{i}" for i in range(1, 10))}


def sanitize_name(name: str, replacement: str = "_") -> str:
    """把任意字符串变成可安全用作目录名的形式（Windows 友好）。

    除非法字符外，还必须挡住两类越权输入：
    * `..`、`~`、前导 `/` —— 否则 `versions/../../evil` 会写到游戏目录之外；
    * Windows 保留设备名 —— 否则会写入设备而不是文件。
    """
    text = _UNSAFE.sub(replacement, str(name))
    # 去掉相对路径逃逸片段
    while ".." in text:
        text = text.replace("..", ".")
    text = text.replace("~", replacement).lstrip("/\\").strip().rstrip(".")
    if text.split(".")[0].lower() in _RESERVED:
        text = f"_{text}"
    return text or "unnamed"


def safe_join(base: Path | str, *parts: str) -> Path:
    """把不可信的相对路径拼到 `base` 下，并保证结果不会逃出 `base`。

    用于所有「路径来自远端/压缩包/用户输入」的场景（下载目标、解压目标等）。
    """
    base_path = Path(base).resolve()
    candidate = base_path
    for part in parts:
        text = str(part).replace("\\", "/")
        text = text.lstrip("/")
        while text.startswith("../"):
            text = text[3:]
        if text in ("", ".", ".."):
            continue
        candidate = candidate / text
    resolved = Path(os.path.normpath(str(candidate))).resolve()
    try:
        resolved.relative_to(base_path)
    except ValueError:
        raise ValueError(f"非法路径（试图逃出目标目录）：{parts}") from None
    return resolved


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

# 解压防护阈值：单个压缩包最多解出 20 万个条目 / 2GB 内容，压缩比不得超过 200:1
MAX_ZIP_ENTRIES = 200_000
MAX_ZIP_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_ZIP_RATIO = 200


def extract_zip(
    archive: Path,
    dest: Path,
    exclude: Optional[Callable[[str], bool]] = None,
    flatten_natives: bool = False,
) -> List[Path]:
    """解压 zip，返回解压出的文件列表；`exclude` 返回 True 的条目会被跳过。

    安全约束（恶意压缩包是最典型的攻击面）：
    * **zip slip**：条目名含 `../`、绝对路径或 Windows 盘符时直接跳过；
    * **符号链接 / 特殊文件**：zip 可以携带 symlink 条目指向系统任意位置，一律跳过；
    * **压缩包炸弹**：限制条目数、总解压体积与压缩比，超限立即中止。

    `flatten_natives=True` 时，对于带目录层级的原生库（LWJGL 3 的
    `windows/x64/org/lwjgl/glfw/glfw.dll` 这类布局），除了按原结构解压，
    还会在根目录额外放一份同名文件。
    """
    extracted: List[Path] = []
    flat_seen: set = set()
    ensure_dir(dest)
    root = Path(dest).resolve()

    with zipfile.ZipFile(archive) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise ValueError(f"压缩包条目过多（{len(infos)}），已中止解压以防压缩包炸弹")

        total = 0
        for info in infos:
            name = info.filename
            if name.endswith("/"):
                continue
            if exclude and exclude(name):
                continue

            # 跳过符号链接等非常规条目（external_attr 高 16 位存 Unix 文件类型）
            mode = info.external_attr >> 16
            if mode and (mode & 0o170000) not in (0o100000, 0o000000):
                LOG.warning("跳过非常规压缩包条目：%s", name)
                continue

            # 绝对路径 / 盘符 / 反斜杠转义都先归一化，再做越界检查
            text = name.replace("\\", "/")
            if text.startswith("/") or (len(text) > 1 and text[1] == ":"):
                LOG.warning("跳过绝对路径条目：%s", name)
                continue
            if ".." in text.split("/"):
                # 含 `..` 的条目一律丢弃，而不是「剥掉 .. 后照样解压」
                LOG.warning("跳过含上级目录的条目：%s", name)
                continue
            try:
                target = safe_join(root, text)
            except ValueError:
                LOG.warning("跳过越界条目（zip slip）：%s", name)
                continue
            if target.exists() and target.is_symlink():
                continue

            size = info.file_size or 0
            total += size
            if total > MAX_ZIP_TOTAL_BYTES:
                raise ValueError("解压后体积超过限制，已中止（疑似压缩包炸弹）")
            if size and info.compress_size:
                if size / max(1, info.compress_size) > MAX_ZIP_RATIO and size > 8 * 1024 * 1024:
                    raise ValueError(f"压缩比异常（{size}:{info.compress_size}），已中止解压")

            ensure_dir(target.parent)
            with zf.open(info) as src, open(target, "wb") as dst:
                dst.write(src.read())
            extracted.append(target)

            if flatten_natives and "/" in text:
                base = text.rsplit("/", 1)[-1]
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
