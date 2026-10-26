"""把「创建桌面快捷方式」的界面流程与核心实现粘合起来。

职责：

* 推断**当前应用真正的启动方式**（打包后的 exe / 解释器 + `main.py` 参数）；
* 生成并缓存平台所需图标资产（.ico / .icns / .png）；
* 组装 `ShortcutSpec` 并调用 `core.shortcut`；
* 提供「已创建 / 未创建」状态查询，供设置页展示。

界面层不直接拼路径，保证 UI 与平台细节解耦。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, Optional

from .. import LAUNCHER_NAME, LAUNCHER_VERSION
from ..core import paths as P
from ..core.config import Config
from ..core.shortcut import (
    ShortcutError,
    ShortcutResult,
    ShortcutSpec,
    create_shortcut,
    find_existing_shortcut,
    icon_asset_key,
    remove_shortcut,
    shortcut_filename,
)

# ------------------------------------------------------------------ 启动方式


def is_frozen() -> bool:
    """是否由 PyInstaller / Nuitka 等打包运行。"""
    return bool(getattr(sys, "frozen", False))


def launcher_executable() -> Path:
    """双击快捷方式时真正被调用的可执行文件。"""
    if is_frozen():
        return Path(sys.executable)
    exe = Path(sys.executable)
    if os.name == "nt" and exe.name.lower() == "python.exe":
        windowless = exe.with_name("pythonw.exe")
        if windowless.is_file():
            return windowless
    return exe


def main_script() -> Path:
    """项目入口脚本（打包运行时不适用）。"""
    return Path(__file__).resolve().parents[2] / "main.py"


def startup_arguments() -> list[str]:
    """快捷方式的启动参数：显式带上数据目录，避免工作目录不同导致配置丢失。"""
    if is_frozen():
        return ["--launcher-home", str(P.launcher_root())]
    return [str(main_script()), "--launcher-home", str(P.launcher_root())]


def app_name(config: Optional[Config] = None) -> str:
    if config is not None:
        custom = str(config.get("shortcut.name", "") or "").strip()
        if custom:
            return custom
    return LAUNCHER_NAME


def app_description() -> str:
    return f"{LAUNCHER_NAME} {LAUNCHER_VERSION} — 模块化的《我的世界》Java 版启动器"


# ------------------------------------------------------------------ 图标资产


def icon_assets(force: bool = False) -> Dict[str, Path]:
    """生成（或复用）图标资产，返回各类文件路径。"""
    from .icons import write_icon_assets

    return write_icon_assets(P.launcher_root() / "icons", force=force)


def platform_icon(assets: Optional[Dict[str, Path]] = None) -> Optional[Path]:
    assets = assets or icon_assets()
    key = icon_asset_key()
    return assets.get(key) or assets.get("png")


# ------------------------------------------------------------------ 规格


def build_spec(config: Optional[Config] = None) -> ShortcutSpec:
    exe = launcher_executable()
    try:
        icon = platform_icon(icon_assets())
    except Exception as exc:  # noqa: BLE001  # 图标生成失败不该阻断创建，只是没有自定义图标
        from ..core.utils import LOG

        LOG.warning("图标生成失败，快捷方式将使用默认图标：%s", exc)
        icon = None
    return ShortcutSpec(
        name=app_name(config),
        description=app_description(),
        executable=exe,
        arguments=startup_arguments(),
        working_dir=Path(__file__).resolve().parents[2],
        icon=icon,
        startup_wm_class=app_name(config),
    )


# ------------------------------------------------------------------ 操作


def current_path(config: Optional[Config] = None) -> Optional[Path]:
    """桌面上是否已存在本应用的快捷方式。"""
    return find_existing_shortcut(app_name(config))


def create(
    config: Optional[Config] = None,
    dest_dir: Optional[Path] = None,
    overwrite: bool = False,
) -> ShortcutResult:
    spec = build_spec(config)
    result = create_shortcut(spec, dest_dir=dest_dir, overwrite=overwrite)
    if config is not None and result.path is not None:
        import time

        try:
            config.set("shortcut.last_path", str(result.path))
            config.set("shortcut.last_created", time.strftime("%Y-%m-%d %H:%M"))
        except OSError as exc:  # 只读配置目录：创建成功但记不下来，忽略
            from ..core.utils import LOG

            LOG.warning("记录快捷方式信息失败：%s", exc)
    return result


def remove(path: Path) -> ShortcutResult:
    return remove_shortcut(path)


# ------------------------------------------------------------------ 首次运行自动创建


def auto_create_suppressed() -> bool:
    """环境变量开关：自动化测试 / 便携运行时不应当往真实桌面写东西。"""
    return os.environ.get("PYLAUNCHER_NO_AUTOSHORTCUT", "") == "1"


def ensure_created_once(config: Optional[Config] = None) -> Optional[ShortcutResult]:
    """首次运行时自动创建桌面快捷方式（幂等，只会成功一次的另一次）。

    判断顺序：
    1. 环境变量禁止 → 跳过；
    2. 用户关闭了「首次运行自动创建」→ 跳过；
    3. 之前已经尝试过（成功与否都记 `shortcut.auto_done`）→ 跳过，避免每次启动都打扰；
    4. 桌面已存在同名快捷方式 → 只把路径记进配置，不重复创建；
    5. 否则尝试创建；失败不弹窗打扰，只写日志（设置页仍可手动重试）。

    这是「安装后桌面上没有图标」的直接修复：以前只有手动点按钮才会生成。
    """
    from ..core.utils import LOG

    if config is None:
        return None
    if auto_create_suppressed():
        return None
    if not bool(config.get("shortcut.auto_create", True)):
        return None
    if bool(config.get("shortcut.auto_done", False)):
        return None
    if str(config.get("shortcut.last_path", "") or ""):
        return None

    name = app_name(config)
    try:
        existing = find_existing_shortcut(name)
    except Exception as exc:  # noqa: BLE001
        LOG.debug("查询桌面快捷方式失败：%s", exc)
        existing = None
    if existing is not None:
        try:
            config.set("shortcut.last_path", str(existing))
            config.set("shortcut.auto_done", True)
        except OSError as exc:
            LOG.warning("记录快捷方式信息失败：%s", exc)
        return None

    try:
        result = create(config)
    except ShortcutError as exc:
        LOG.warning("首次运行自动创建桌面快捷方式失败：%s", exc)
        _mark_auto_done(config)
        return None
    except OSError as exc:
        LOG.warning("首次运行写入桌面失败（可能无权限）：%s", exc)
        _mark_auto_done(config)
        return None
    _mark_auto_done(config)
    return result


def _mark_auto_done(config: Config) -> None:
    try:
        config.set("shortcut.auto_done", True)
    except OSError:  # 配置只读：下次再说，不影响主流程
        pass


def expected_filename(config: Optional[Config] = None) -> str:
    return shortcut_filename(app_name(config))


__all__ = [
    "ShortcutError",
    "ShortcutResult",
    "app_description",
    "app_name",
    "auto_create_suppressed",
    "build_spec",
    "create",
    "current_path",
    "ensure_created_once",
    "expected_filename",
    "icon_assets",
    "is_frozen",
    "launcher_executable",
    "main_script",
    "platform_icon",
    "remove",
    "startup_arguments",
]
