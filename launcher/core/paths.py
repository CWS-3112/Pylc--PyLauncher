"""目录布局：启动器根目录、游戏目录、版本隔离目录。

目录结构（默认：启动器根目录即当前工作目录）：

    <launcher_root>/
        config.json            启动器全局配置
        accounts.json          账户数据库
        logs/
        game/                  公共游戏目录
            versions/<id>/<id>.jar|.json
            libraries/
            assets/indexes|objects
            mods/
            saves/
        isolates/<instance>/   版本隔离实例目录
            mods/ saves/ options.txt …

`launcher_root` 可通过环境变量 `PYLAUNCHER_HOME` 覆盖，方便绿色化 / 测试。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from .utils import ensure_dir, sanitize_name


@lru_cache(maxsize=1)
def launcher_root() -> Path:
    """启动器自身的数据根目录。"""
    env = os.environ.get("PYLAUNCHER_HOME")
    return ensure_dir(Path(env) if env else Path.cwd())


def set_launcher_root(path: Path | str) -> Path:
    launcher_root.cache_clear()
    os.environ["PYLAUNCHER_HOME"] = str(path)
    return launcher_root()


# ------------------------------------------------------------------ 子系统


def config_file() -> Path:
    return launcher_root() / "config.json"


def accounts_file() -> Path:
    return launcher_root() / "accounts.json"


def logs_dir() -> Path:
    return ensure_dir(launcher_root() / "logs")


def temp_dir() -> Path:
    return ensure_dir(launcher_root() / "temp")


# ------------------------------------------------------------------ 游戏目录


def default_game_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home())) / ".minecraft"
    elif os.uname().sysname == "Darwin":
        base = Path.home() / "Library" / "Application Support" / "minecraft"
    else:
        base = Path.home() / ".minecraft"
    return base


def version_dir(game_dir: Path, version_id: str) -> Path:
    return game_dir / "versions" / sanitize_name(version_id)


def version_json(game_dir: Path, version_id: str) -> Path:
    return version_dir(game_dir, version_id) / f"{sanitize_name(version_id)}.json"


def version_jar(game_dir: Path, version_id: str) -> Path:
    return version_dir(game_dir, version_id) / f"{sanitize_name(version_id)}.jar"


def libraries_dir(game_dir: Path) -> Path:
    return game_dir / "libraries"


def assets_dir(game_dir: Path) -> Path:
    return game_dir / "assets"


def asset_index_dir(game_dir: Path) -> Path:
    return ensure_dir(assets_dir(game_dir) / "indexes")


def asset_objects_dir(game_dir: Path) -> Path:
    return ensure_dir(assets_dir(game_dir) / "objects")


def asset_virtual_dir(game_dir: Path) -> Path:
    return ensure_dir(assets_dir(game_dir) / "virtual" / "legacy")


def natives_dir(game_dir: Path, version_id: str) -> Path:
    return ensure_dir(version_dir(game_dir, version_id) / "natives")


def isolate_base(game_dir: Path) -> Path:
    return ensure_dir(game_dir / "isolates")


def isolate_dir(game_dir: Path, instance_name: str) -> Path:
    return ensure_dir(isolate_base(game_dir) / sanitize_name(instance_name))


def mods_dir(instance_dir: Path) -> Path:
    return ensure_dir(instance_dir / "mods")


def resources_dir(instance_dir: Path) -> Path:
    return ensure_dir(instance_dir / "resourcepacks")


def saves_dir(instance_dir: Path) -> Path:
    return ensure_dir(instance_dir / "saves")
