"""Java 运行环境自动检测与选择。

支持 Windows / macOS / Linux 三平台，覆盖：

* `JAVA_HOME` 与 `PATH`
* 各发行版默认安装目录（Oracle、Adoptium/Eclipse Temurin、Zulu、Microsoft、Amazon Corretto、BellSoft、GraalVM …）
* Windows 注册表（`JavaSoft\\JDK` / `JavaSoft\\Java Runtime Environment`）
* macOS `/usr/libexec/java_home` 与 `*/Contents/Home` 约定
* Linux `/usr/lib/jvm`、SDKMAN!、Snap

找到的每个候选都会真正执行 `java -version` 解析出主版本号与位数，避免拿到坏路径。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .utils import LOG, is_64bit, is_linux, is_macos, is_windows

# -------------------------------------------------------------------- 数据


@dataclass
class JavaInfo:
    path: Path
    version_string: str = "unknown"
    major: int = 0
    bitness: int = 0          # 32 / 64，0 表示未知
    vendor: str = ""
    runtime: str = "JRE"      # JRE / JDK

    @property
    def display(self) -> str:
        arch = f"{self.bitness} 位" if self.bitness else "未知位数"
        vendor = f" · {self.vendor}" if self.vendor else ""
        return f"Java {self.major}（{arch}{vendor}）"

    @property
    def valid(self) -> bool:
        return self.major > 0

    def to_dict(self) -> Dict:
        return {
            "path": str(self.path),
            "version_string": self.version_string,
            "major": self.major,
            "bitness": self.bitness,
            "vendor": self.vendor,
            "runtime": self.runtime,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "JavaInfo":
        return cls(
            path=Path(data.get("path", "")),
            version_string=data.get("version_string", "unknown"),
            major=int(data.get("major", 0)),
            bitness=int(data.get("bitness", 0)),
            vendor=data.get("vendor", ""),
            runtime=data.get("runtime", "JRE"),
        )


# -------------------------------------------------------------------- 探测


def _executable_name() -> str:
    return "java.exe" if is_windows() else "java"


def _candidate_dirs() -> List[Path]:
    """各平台可能的 Java 安装根目录。"""
    dirs: List[Path] = []
    home = Path.home()

    if is_windows():
        for env in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData"):
            base = os.environ.get(env)
            if not base:
                continue
            b = Path(base)
            dirs += [
                b / "Java",
                b / "Eclipse Adoptium",
                b / "AdoptOpenJDK",
                b / "Zulu",
                b / "Microsoft",
                b / "Amazon Corretto",
                b / "BellSoft",
                b / "GraalVM",
                b / "Semeru",
                b / "Temurin",
                b / "Dragonwell",
                b / "OpenLogic",
                b / "SapMachine",
            ]
        dirs += [
            home / "scoop" / "apps",
            Path(os.environ.get("LOCALAPPDATA", home)) / "Programs",
        ]

    elif is_macos():
        dirs += [
            Path("/Library/Java/JavaVirtualMachines"),
            Path("/System/Library/Java/JavaVirtualMachines"),
            home / "Library/Java/JavaVirtualMachines",
            Path("/opt/homebrew/opt"),
            Path("/usr/local/opt"),
        ]

    else:  # Linux / BSD
        dirs += [
            Path("/usr/lib/jvm"),
            Path("/usr/java"),
            Path("/usr/lib64/jvm"),
            Path("/opt/java"),
            Path("/opt/jdk"),
            home / ".sdkman/candidates/java",
            home / ".jdks",
            Path("/snap"),
        ]

    return [d for d in dirs if d.exists()]


def _registry_candidates() -> List[Path]:
    """Windows 注册表中的 JDK / JRE。"""
    out: List[Path] = []
    if not is_windows():
        return out
    try:
        import winreg  # type: ignore[import-not-found]

        keys = [
            r"SOFTWARE\JavaSoft\JDK",
            r"SOFTWARE\JavaSoft\Java Runtime Environment",
            r"SOFTWARE\WOW6432Node\JavaSoft\JDK",
            r"SOFTWARE\WOW6432Node\JavaSoft\Java Runtime Environment",
        ]
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for key in keys:
                try:
                    with winreg.OpenKey(hive, key) as root:
                        index = 0
                        while True:
                            try:
                                sub = winreg.EnumKey(root, index)
                            except OSError:
                                break
                            index += 1
                            try:
                                with winreg.OpenKey(root, sub) as k:
                                    home, _ = winreg.QueryValueEx(k, "JavaHome")
                                    out.append(Path(home))
                            except OSError:
                                continue
                except OSError:
                    continue
    except ImportError:
        pass
    return out


def _java_home_candidates() -> List[Path]:
    out: List[Path] = []
    for var in ("JAVA_HOME", "JDK_HOME", "JRE_HOME"):
        val = os.environ.get(var)
        if val:
            out.append(Path(val))
    return out


def _is_java_exe(path: Path) -> bool:
    """Windows 上文件名大小写不敏感（java.exe / java.EXE / JavaW.exe 都要认）。"""
    return path.is_file() and path.name.lower() in ("java.exe", "javaw.exe", "java")


def _expand(root: Path) -> Iterable[Path]:
    """把根目录展开为具体的 java 可执行文件路径。"""
    exe = _executable_name()

    # 根目录下直接就躺着可执行文件（PATH 里的 java、用户手动指的文件）
    if _is_java_exe(root):
        yield root
        return

    # 标准布局 <root>/bin/java
    direct = root / "bin" / exe
    if direct.is_file():
        yield direct
        return
    for name in ("java.exe", "javaw.exe", "java"):
        candidate = root / name
        if candidate.is_file():
            yield candidate
            return

    # Contents/Home（macOS 约定）
    mac_home = root / "Contents" / "Home" / "bin" / exe
    if mac_home.is_file():
        yield mac_home
        return

    try:
        children = sorted(p for p in root.iterdir() if p.is_dir())
    except (OSError, PermissionError):
        return
    for child in children:
        if child.name in ("bin", "jre", "zulu.jdk", "libexec", "current", "default"):
            yield from _expand(child)
        elif child.suffix in (".jdk", ".jre", ".jvm") or "jdk" in child.name.lower() \
                or "jre" in child.name.lower() or "java" in child.name.lower():
            yield from _expand(child)


_VERSION_RE = re.compile(r'version "(?P<v>[^"]+)"', re.IGNORECASE)
_RUNTIME_RE = re.compile(r"^(?P<name>.+?)\s+runtime environment", re.IGNORECASE | re.MULTILINE)


def parse_version_output(text: str) -> Dict[str, object]:
    """从 `java -version` 输出解析主版本号、位数与厂商。"""
    info: Dict[str, object] = {
        "version_string": "unknown",
        "major": 0,
        "bitness": 64 if is_64bit() else 0,
        "vendor": "",
        "runtime": "JRE",
    }
    m = _VERSION_RE.search(text)
    if m:
        raw = m.group("v")
        info["version_string"] = raw
        nums = re.findall(r"\d+", raw)
        if nums:
            if nums[0] == "1" and len(nums) > 1:      # 1.8.0_402 → 8
                info["major"] = int(nums[1])
            else:
                info["major"] = int(nums[0])

    low = text.lower()
    if "64-bit" in low or "64-bit" in low.replace(" ", "-"):
        info["bitness"] = 64
    elif "32-bit" in low:
        info["bitness"] = 32

    r = _RUNTIME_RE.search(text)
    if r:
        name = r.group("name").strip()
        info["vendor"] = name
        info["runtime"] = "JDK" if "jdk" in name.lower() else "JRE"

    return info


def probe_java(path: Path, timeout: float = 6.0) -> Optional[JavaInfo]:
    """实际执行 `java -version` 验证并解析信息；失败返回 None。"""
    try:
        if not path.is_file():
            return None
        proc = subprocess.run(
            [str(path), "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            creationflags=getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0) if is_windows() else 0,
        )
        text = (proc.stdout or b"").decode("utf-8", errors="replace")
        parsed = parse_version_output(text)
        return JavaInfo(
            path=path,
            version_string=str(parsed["version_string"]),
            major=int(parsed["major"]),
            bitness=int(parsed["bitness"]),
            vendor=str(parsed["vendor"]),
            runtime=str(parsed["runtime"]),
        )
    except Exception as exc:  # noqa: BLE001
        LOG.debug("探测 Java 失败 %s：%s", path, exc)
        return None


def find_java(include_path: bool = True) -> List[JavaInfo]:
    """扫描本机所有可用 Java，按「主版本号降序 + 64 位优先」排序。"""
    seen: Dict[str, JavaInfo] = {}

    roots: List[Path] = []
    roots += _java_home_candidates()
    roots += _registry_candidates()
    roots += _candidate_dirs()

    if include_path:
        which = shutil.which("java")
        if which:
            roots.append(Path(which))
        # PATH 里的 java.exe 所在目录
        for p in os.environ.get("PATH", "").split(os.pathsep):
            if p and Path(p).is_dir():
                roots.append(Path(p))

    for root in roots:
        for exe in _expand(root):
            key = str(exe.resolve()).lower()
            if key in seen:
                continue
            info = probe_java(exe)
            if info and info.valid:
                seen[key] = info

    result = sorted(
        seen.values(),
        key=lambda i: (i.bitness == 64, i.major, str(i.path)),
        reverse=True,
    )
    LOG.info("检测到 %d 个 Java 运行环境", len(result))
    return result


@lru_cache(maxsize=4)
def cached_find_java() -> tuple:
    return tuple(find_java())


def invalidate_cache() -> None:
    cached_find_java.cache_clear()


# -------------------------------------------------------------------- 选择


def required_java_major(minor_game_version: int) -> int:
    """根据 MC 小版本推断所需的最低 Java 主版本。

    1.17 → 16，1.18~1.20.4 → 17，1.20.5 及以上 → 21
    """
    if minor_game_version >= 21:
        return 21
    if minor_game_version >= 18:
        return 17
    if minor_game_version == 17:
        return 16
    return 8


def parse_minor_version(version_id: str) -> int:
    """从 `1.20.4` / `24w07a` / `1.21-rc1` / `26.3` 中解析次版本号。

    1.17 及更早写作 `1.x`（→x）；Mojang 新命名直接是 `26.3` 这样的年份_id，
    这类一律返回 >=21 的数，交给 `required_java_major` 处理。
    """
    m = re.match(r"^1\.(\d+)", version_id)
    if m:
        return int(m.group(1))
    m = re.match(r"^(\d+)(?:\.|_|-)", version_id)
    if m:
        value = int(m.group(1))
        if value >= 21:          # 26.3 → 现代命名，至少按 1.21+ 对待
            return value
    return 0


def best_java(
    candidates: Iterable[JavaInfo],
    required_major: int = 0,
    prefer_64: bool = True,
) -> Optional[JavaInfo]:
    """选一个满足要求且最优的 Java：优先刚好够用、64 位、版本不过高。"""
    pool = [j for j in candidates if j.valid]
    if prefer_64:
        pool = [j for j in pool if j.bitness != 32] or pool
    if not pool:
        return None

    qualified = [j for j in pool if j.major >= required_major]
    if qualified:
        # 选最接近需求的版本，避免用 Java 21 跑 1.12 的 Forge 出问题
        return min(qualified, key=lambda j: (j.major, j.bitness != 64))
    return max(pool, key=lambda j: (j.major, j.bitness == 64))


def resolve_java(
    configured: str = "",
    required_major: int = 0,
    candidates: Optional[List[JavaInfo]] = None,
) -> Optional[JavaInfo]:
    """按「用户指定 > 自动最优」返回 Java。"""
    if configured:
        p = Path(configured)
        candidates = [p, p / "bin" / _executable_name(), p / "bin" / "javaw.exe"]
        for exe in candidates:
            if exe.is_file():
                info = probe_java(exe)
                if info:
                    return info
        LOG.warning("配置的 Java 无效，回退自动检测：%s", configured)
    pool = candidates if candidates is not None else find_java()
    return best_java(pool, required_major)


def describe_java_problem(
    candidates: Iterable[JavaInfo],
    required_major: int,
) -> str:
    """描述「本机 Java 能否满足要求」；返回空串表示没问题。

    用于启动前给出可执行的提示——过去只是默默挑一个最高的 Java 去跑，
    版本不对时游戏 0.5 秒退出且日志是乱码，用户完全无从判断。
    """
    pool = [j for j in candidates if j.valid]
    if not pool:
        return "本机没有检测到可用的 Java 运行环境"
    if required_major <= 0:
        return ""
    best = max(j.major for j in pool)
    if best >= required_major:
        return ""
    return (
        f"当前版本需要 Java {required_major} 或更高，但本机最高只检测到 Java {best}。"
        f"请安装 Java {required_major} 后重试，或在实例设置里手动指定 javaw.exe 路径。"
    )


def total_system_memory_mb() -> int:
    """系统物理内存（MB），用于智能分配最大内存。"""
    try:
        if is_windows():
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))  # type: ignore[attr-defined]
            return int(stat.ullTotalPhys // (1024 * 1024))
        if is_macos():
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True)
            return int(int(out.strip()) // (1024 * 1024))
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except Exception:  # noqa: BLE001
        pass
    return 8192


def suggest_memory_mb() -> int:
    """按物理内存给出推荐的最大堆内存（留一半给系统）。"""
    total = total_system_memory_mb()
    if is_64bit():
        suggested = min(max(total // 2, 2048), 16384)
    else:
        suggested = min(1024, max(total // 4, 512))
    return suggested
