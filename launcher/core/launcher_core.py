"""启动核心：参数构建、占位符替换、进程管理与日志捕获。

命令构建严格遵循 Mojang 的 version.json 规范，并完整处理 `inheritsFrom` 继承链：
jvm/game 参数、库列表、资源索引、主类依次合并，子版本覆盖父版本。
"""

from __future__ import annotations

import json
import os
import platform
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from . import paths as P
from .auth import MICROSOFT, Account
from .config import Config, Instance
from .constants import LAUNCHER_BRAND, LAUNCHER_NAME, LAUNCHER_VERSION
from .java import JavaInfo, parse_minor_version, required_java_major, resolve_java, suggest_memory_mb
from .manifest import resolve_version_chain
from .rules import is_allowed, library_path
from .utils import LOG, is_windows

LogCallback = Callable[[str], None]
ExitCallback = Callable[[int], None]


def _console_encoding() -> str:
    """子进程控制台输出的编码（简体中文 Windows 是 GBK/GB18030）。"""
    if is_windows():
        try:
            import ctypes

            codepage = ctypes.windll.kernel32.GetACP() or 936  # type: ignore[attr-defined]
            return f"cp{codepage}"
        except Exception:  # noqa: BLE001
            return "gb18030"
    try:
        import locale

        return locale.getpreferredencoding(False) or "utf-8"
    except Exception:  # noqa: BLE001
        return "utf-8"


def _decode_order() -> List[str]:
    """日志解码尝试顺序：UTF-8 优先，控制台编码兜底。"""
    order = ["utf-8", _console_encoding(), "gb18030", "latin-1"]
    seen: List[str] = []
    for enc in order:
        if enc and enc.lower() not in {s.lower() for s in seen}:
            seen.append(enc)
    return seen


_DECORDER_ORDER = _decode_order()


# -------------------------------------------------------------------- 启动选项


@dataclass
class LaunchOptions:
    """一次启动所需的全部参数。"""

    version_id: str
    account: Optional[Account] = None
    java_path: str = ""
    max_memory_mb: int = 0
    min_memory_mb: int = 0
    width: int = 0
    height: int = 0
    fullscreen: bool = False
    extra_jvm_args: str = ""
    extra_game_args: str = ""
    custom_main_class: str = ""
    server_address: str = ""   # 预留：--server --port
    server_port: int = 25565
    env: Dict[str, str] = field(default_factory=dict)
    demo: bool = False

    @classmethod
    def from_instance(
        cls,
        instance: Instance,
        cfg: Config,
        account: Optional[Account],
        override_java: str = "",
    ) -> "LaunchOptions":
        """用「实例配置覆盖全局配置」的规则生成启动选项。"""
        max_mb = instance.max_memory_mb or int(cfg.get("memory.max_mb", 4096))
        if bool(cfg.get("memory.auto", True)) and not instance.max_memory_mb:
            max_mb = min(suggest_memory_mb(), max_mb or 4096)
        return cls(
            version_id=instance.version_id,
            account=account,
            java_path=override_java or instance.java_path or str(cfg.get("java.selected", "") or ""),
            max_memory_mb=max_mb,
            min_memory_mb=instance.min_memory_mb or int(cfg.get("memory.min_mb", 512)),
            width=instance.width or int(cfg.get("window.width", 0) or 0),
            height=instance.height or int(cfg.get("window.height", 0) or 0),
            fullscreen=bool(instance.fullscreen) if instance.fullscreen is not None
            else bool(cfg.get("window.fullscreen", False)),
            extra_jvm_args=" ".join(filter(None, [
                str(cfg.get("launch.extra_jvm_args", "") or ""),
                instance.jvm_args,
            ])),
            extra_game_args=" ".join(filter(None, [
                str(cfg.get("launch.extra_game_args", "") or ""),
                instance.game_args,
            ])),
            custom_main_class=str(cfg.get("launch.custom_main_class", "") or ""),
            env=dict(instance.env or {}),
        )


# -------------------------------------------------------------------- 版本解析


@dataclass
class ResolvedVersion:
    """从继承链合并后的版本信息。"""

    version_id: str
    main_class: str
    jvm_args: List[str]
    game_args: List[str]
    classpath: List[Path]
    jar: Path
    natives_dir: Path
    asset_index: str
    assets_root: Path
    game_directory: Path
    log_config: Optional[Path] = None
    java_required: int = 8


def _flatten(items: Iterable, features: Dict[str, bool]) -> List[str]:
    """把 `arguments.jvm/game` 中的条件条目按规则展开为扁平字符串列表。"""
    out: List[str] = []
    for item in items:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            if not is_allowed(item.get("rules"), features):
                continue
            value = item.get("value")
            if isinstance(value, str):
                out.append(value)
            elif isinstance(value, list):
                out.extend(str(v) for v in value)
    return out


def _merge_arguments(chain: Sequence[dict]) -> Dict[str, List]:
    """合并 arguments：父版本参数在前，子版本在后（子覆盖父）。"""
    features = {"is_demo_user": False, "has_custom_resolution": True}
    jvm: List[str] = []
    game: List[str] = []
    for data in reversed(chain):      # 祖父 → 父 → 子
        args = data.get("arguments") or {}
        jvm += _flatten(args.get("jvm") or [], features)
        game += _flatten(args.get("game") or [], features)

    # 老版本（1.12 及更早）没有 arguments，而是 minecraftArguments 字符串
    if not game:
        for data in reversed(chain):
            legacy = data.get("minecraftArguments")
            if legacy:
                game = shlex.split(legacy)
                break
    return {"jvm": jvm, "game": game}


def _resolve_jar(game_dir: Path, chain: Sequence[dict]) -> Path:
    """定位实际使用的客户端 JAR（支持 `jar` 字段重定向到父版本）。"""
    candidates: List[Path] = []
    for data in chain:
        jar_id = data.get("jar") or data.get("id")
        if jar_id:
            candidates.append(P.version_jar(game_dir, jar_id))
    for c in candidates:
        if c.is_file():
            return c
    return candidates[-1] if candidates else P.version_jar(game_dir, chain[0].get("id", ""))


def _build_classpath(game_dir: Path, chain: Sequence[dict], features: Dict[str, bool]) -> List[Path]:
    libs_dir = P.libraries_dir(game_dir)
    entries: List[Path] = []
    seen = set()
    for data in chain:                      # 子 → 父，保证子类库优先
        for lib in data.get("libraries", []):
            name = lib.get("name", "")
            if not name or name in seen:
                continue
            if not is_allowed(lib.get("rules"), features):
                continue
            seen.add(name)
            # 只有 classifiers、没有主构件 URL 的库（1.7.10 的 lwjgl-platform 等）
            # 本来就下载不到 jar，放进 classpath 只会让每次启动都误报「缺失文件」
            downloads = lib.get("downloads") or {}
            if not (downloads.get("artifact")) and downloads.get("classifiers") and not lib.get("url"):
                continue
            rel = library_path(lib)
            entries.append(libs_dir / rel)
    return entries


def resolve_version(
    game_dir: Path,
    version_id: str,
    instance: Optional[Instance] = None,
    logger: Optional[LogCallback] = None,
) -> ResolvedVersion:
    """解析版本继承链，产出可直接拼命令的中间表示。"""
    chain = resolve_version_chain(game_dir, version_id)
    if not chain:
        raise FileNotFoundError(f"找不到版本 {version_id} 的 JSON 文件")

    features = {"is_demo_user": False, "has_custom_resolution": True}
    merged = _merge_arguments(chain)

    jar = _resolve_jar(game_dir, chain)
    classpath = _build_classpath(game_dir, chain, features)
    entries = [jar, *classpath]

    main_class = ""
    for data in chain:
        if data.get("mainClass"):
            main_class = data["mainClass"]
            break

    asset_index = ""
    for data in chain:
        if (data.get("assetIndex") or {}).get("id"):
            asset_index = data["assetIndex"]["id"]
            break

    game_directory = instance.game_directory(game_dir) if instance else game_dir
    natives = P.natives_dir(game_dir, version_id)

    # Java 版本需求：version.json 里的 javaVersion.majorVersion 才是权威来源。
    # 早期靠版本号字符串猜（1.18→17、1.20.5→21），对 HMCL/Modrinth 导入的整合包
    #（`Better MC`、`26.3` 这类非 1.x 命名）一律猜成 Java 8，结果用 Java 21 去跑
    # 需要 Java 25 的版本，游戏起来 0.5 秒就 `UnsupportedClassVersionError` 退出。
    java_required = _resolve_java_requirement(chain, version_id)

    log_config: Optional[Path] = None
    for data in chain:
        logging = (data.get("logging") or {}).get("client") or {}
        f = logging.get("file") or {}
        if f.get("id"):
            candidate = P.assets_dir(game_dir) / "log_configs" / f["id"]
            if candidate.is_file():
                log_config = candidate
            break

    return ResolvedVersion(
        version_id=version_id,
        main_class=main_class,
        jvm_args=merged["jvm"],
        game_args=merged["game"],
        classpath=entries,
        jar=jar,
        natives_dir=natives,
        asset_index=asset_index,
        assets_root=P.assets_dir(game_dir),
        game_directory=game_directory,
        log_config=log_config,
        java_required=java_required,
    )


def _resolve_java_requirement(chain: Sequence[dict], version_id: str) -> int:
    """推断所需的 Java 主版本：优先用 JSON 里的 `javaVersion.majorVersion`。

    `chain` 是「子 → 父 → 祖父」顺序，加载器（Forge/Fabric）写在子上应当优先，
    因此从子往父找第一个显式声明的值。
    """
    for data in chain:
        info = data.get("javaVersion") or {}
        major = info.get("majorVersion")
        if isinstance(major, bool):
            continue
        try:
            value = int(major)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value

    # 没有声明时才退回按版本号猜测，并把快照/预发布也算进去
    return required_java_major(parse_minor_version(version_id))


# -------------------------------------------------------------------- 占位符


def build_token_map(
    resolved: ResolvedVersion,
    options: LaunchOptions,
    account: Optional[Account],
    classpath_str: str,
) -> Dict[str, str]:
    if account is None:
        player_name = "Player"
        auth_uuid = "00000000-0000-0000-0000-000000000000"
        access_token = "0" * 32
        user_type = "legacy"
        user_props = "{}"
        xuid = ""
    else:
        player_name = account.username
        auth_uuid = account.dashed_uuid
        access_token = account.access_token or ("0" * 32)
        user_type = "msa" if account.type == MICROSOFT else "mojang"
        user_props = "{}"
        xuid = account.extra.get("xuid", "")

    version_type = f"{LAUNCHER_NAME} {LAUNCHER_VERSION}"
    client_id = account.extra.get("client_id", "") if account else ""
    if not client_id:
        client_id = "0"

    return {
        "${auth_player_name}": player_name,
        "${auth_uuid}": auth_uuid,
        "${auth_access_token}": access_token,
        "${auth_session}": access_token,
        "${user_type}": user_type,
        "${user_properties}": user_props,
        "${auth_xuid}": xuid,
        "${clientid}": client_id,
        "${version_name}": resolved.version_id,
        "${version_type}": version_type,
        "${game_directory}": str(resolved.game_directory),
        "${game_assets}": str(resolved.assets_root),
        "${assets_root}": str(resolved.assets_root),
        "${assets_index_name}": resolved.asset_index,
        "${natives_directory}": str(resolved.natives_dir),
        "${classpath}": classpath_str,
        "${classpath_separator}": ";" if is_windows() else ":",
        "${library_directory}": str(P.libraries_dir(resolved.game_directory)) if resolved.game_directory else "",
        "${launcher_name}": LAUNCHER_NAME,
        "${launcher_version}": LAUNCHER_VERSION,
        "${resolution_width}": str(options.width or 854),
        "${resolution_height}": str(options.height or 480),
        "${profile_name}": LAUNCHER_BRAND,
    }


def expand_tokens(args: Iterable[str], tokens: Dict[str, str]) -> List[str]:
    out: List[str] = []
    for arg in args:
        for key, value in tokens.items():
            if key in arg:
                arg = arg.replace(key, value)
        out.append(arg)
    return out


# -------------------------------------------------------------------- 命令组装


def build_command(
    game_dir: Path,
    options: LaunchOptions,
    resolved: Optional[ResolvedVersion] = None,
    java: Optional[JavaInfo] = None,
    instance: Optional[Instance] = None,
) -> tuple:
    """返回 `(可执行文件列表, ResolvedVersion, JavaInfo)`。"""
    resolved = resolved or resolve_version(game_dir, options.version_id, instance)

    java_exe = Path(options.java_path) if options.java_path else ""
    if not java_exe:
        info = resolve_java("", resolved.java_required)
        if info is None:
            raise RuntimeError("未找到可用的 Java 运行环境，请在设置中手动指定")
        java = info
        java_exe = info.path
    else:
        java = java or JavaInfo(path=java_exe)

    exe_path = java_exe
    # Windows 上优先用 javaw.exe，避免弹出控制台窗口
    if is_windows() and exe_path.name.lower() == "java.exe":
        javaw = exe_path.with_name("javaw.exe")
        if javaw.is_file():
            exe_path = javaw

    classpath_str = (";" if is_windows() else ":").join(str(p) for p in resolved.classpath)

    tokens = build_token_map(resolved, options, options.account, classpath_str)

    # ---------------- JVM 参数
    jvm: List[str] = []
    if options.min_memory_mb and options.min_memory_mb > 0:
        jvm.append(f"-Xms{options.min_memory_mb}M")
    if options.max_memory_mb and options.max_memory_mb > 0:
        jvm.append(f"-Xmx{options.max_memory_mb}M")

    jvm += ["-XX:+UnlockExperimentalVMOptions", "-XX:+UseG1GC", "-XX:-UseAdaptiveSizePolicy"]
    jvm.append("-Dfile.encoding=UTF-8")
    jvm.append("-Dlog4j2.formatMsgNoLookups=true")
    jvm.append(f"-Djava.library.path={resolved.natives_dir}")
    if resolved.log_config:
        jvm.append(f"-Dlog4j.configurationFile={resolved.log_config}")

    # 版本自带的 JVM 参数（含 -cp / -Djava.library.path 占位符）
    version_jvm = expand_tokens(resolved.jvm_args, tokens)
    if version_jvm:
        jvm += version_jvm
    else:
        # 老版本（1.12-）没有 arguments.jvm，需要手动补
        jvm += [
            "-Dminecraft.launcher.brand=" + LAUNCHER_BRAND,
            "-Dminecraft.launcher.version=" + LAUNCHER_VERSION,
            "-cp", classpath_str,
        ]

    if options.extra_jvm_args:
        jvm += expand_tokens(shlex.split(options.extra_jvm_args), tokens)

    # ---------------- 游戏参数
    game: List[str] = expand_tokens(resolved.game_args, tokens)
    if options.fullscreen:
        game += ["--fullscreen"]
    elif options.width and options.height:
        game += ["--width", str(options.width), "--height", str(options.height)]
    if options.server_address:
        game += ["--server", options.server_address, "--port", str(options.server_port)]
    if options.extra_game_args:
        game += expand_tokens(shlex.split(options.extra_game_args), tokens)

    main_class = options.custom_main_class or resolved.main_class
    if not main_class:
        raise RuntimeError("无法确定主类（version.json 缺少 mainClass）")

    cmd: List[str] = [str(exe_path)]
    cmd += [a for a in jvm if a]
    cmd += [main_class]
    cmd += game

    return cmd, resolved, java


def command_to_string(cmd: Sequence[str]) -> str:
    """把命令列表转成便于阅读/复制的一行字符串。"""
    if is_windows():
        return subprocess.list2cmdline(list(cmd))
    return " ".join(shlex.quote(c) for c in cmd)


# -------------------------------------------------------------------- 进程


class GameProcess:
    """封装游戏子进程：异步读取输出、可强制结束、记录运行时长。"""

    def __init__(
        self,
        cmd: Sequence[str],
        cwd: Path,
        env: Optional[Dict[str, str]] = None,
        on_log: Optional[LogCallback] = None,
        on_exit: Optional[ExitCallback] = None,
    ) -> None:
        self.cmd = list(cmd)
        self.cwd = Path(cwd)
        self.on_log = on_log
        self.on_exit = on_exit
        self.start_time = 0.0
        self.end_time = 0.0
        self._proc: Optional[subprocess.Popen] = None
        self._readers: List[threading.Thread] = []
        self._env = env or {}

    # ------------------------------------------------------------ 生命周期

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def pid(self) -> int:
        return self._proc.pid if self._proc else -1

    @property
    def exit_code(self) -> Optional[int]:
        return self._proc.poll() if self._proc else None

    @property
    def elapsed(self) -> float:
        if not self.start_time:
            return 0.0
        return (self.end_time or time.time()) - self.start_time

    def start(self) -> None:
        env = os.environ.copy()
        env.update({k: str(v) for k, v in self._env.items()})
        # 保证子进程能找到 java
        if is_windows():
            env.setdefault("APPDATA", str(Path(os.path.expanduser("~")) / "AppData" / "Roaming"))

        LOG.info("启动游戏：%s", command_to_string(self.cmd)[:2000])

        # 实例目录不存在会让 Popen 直接抛 FileNotFoundError —— 用户看到的就是
        # 一句莫名其妙的「启动失败」，所以这里先把目录补出来。
        cwd = Path(self.cwd)
        try:
            cwd.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(
                f"无法创建游戏运行目录：{cwd}\n{exc}\n"
                "请检查该路径是否有写入权限，或到实例设置里换一个隔离目录。"
            ) from exc

        for exe in ([self.cmd[0]] if self.cmd else []):
            if not Path(exe).is_file():
                raise RuntimeError(f"找不到可执行文件：{exe}\n请到「设置」页面重新选择 Java。")

        # 二进制读取 + 按行智能解码：Java 主体输出跟随控制台代码页，
        # 而部分原生/内部组件写的是 UTF-8，固定单一编码必然有一半是乱码。
        self._proc = subprocess.Popen(
            self.cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            stdin=subprocess.DEVNULL,
        )
        self.start_time = time.time()

        if self._proc.stdout:
            t = threading.Thread(target=self._pump, args=(self._proc.stdout,), daemon=True)
            t.start()
            self._readers.append(t)

        watcher = threading.Thread(target=self._watch, daemon=True)
        watcher.start()

    def _decode(self, raw: bytes) -> str:
        """把一行原始字节解码成文本，UTF-8 优先、控制台编码兜底。"""
        if isinstance(raw, str):
            return raw
        for encoding in _DECORDER_ORDER:
            try:
                text = raw.decode(encoding)
            except UnicodeDecodeError:
                continue
            # 出现替换字符说明猜错了，换下一种编码
            if "�" in text and encoding != _DECORDER_ORDER[-1]:
                continue
            return text
        return raw.decode("utf-8", errors="replace")

    def _pump(self, stream) -> None:
        try:
            for line in iter(stream.readline, b""):
                if not line:
                    break
                text = self._decode(line).rstrip("\r\n")
                if self.on_log and isinstance(text, str) and text.strip():
                    self.on_log(text)
        except (ValueError, OSError):
            pass

    def _watch(self) -> None:
        if not self._proc:
            return
        code = self._proc.wait()
        self.end_time = time.time()
        for t in self._readers:
            t.join(timeout=2)
        LOG.info("游戏进程退出，代码 %s，运行时长 %.1fs", code, self.elapsed)
        if self.on_exit:
            self.on_exit(code)

    def kill(self) -> None:
        if self._proc and self.running:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    self._proc.kill()
                except Exception:  # noqa: BLE001
                    pass

    def wait(self, timeout: Optional[float] = None) -> Optional[int]:
        if not self._proc:
            return None
        try:
            return self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None


# -------------------------------------------------------------------- 便捷入口


def launch(
    game_dir: Path,
    options: LaunchOptions,
    instance: Optional[Instance] = None,
    on_log: Optional[LogCallback] = None,
    on_exit: Optional[ExitCallback] = None,
) -> tuple:
    """一步到位：解析版本 → 构建命令 → 启动进程。

    返回 `(GameProcess, command_string, ResolvedVersion, JavaInfo)`。
    """
    resolved = resolve_version(game_dir, options.version_id, instance)
    cmd, resolved, java = build_command(game_dir, options, resolved, None, instance)

    proc = GameProcess(cmd, resolved.game_directory, options.env, on_log, on_exit)
    proc.start()
    return proc, command_to_string(cmd), resolved, java
