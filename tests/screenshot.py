"""把每个页面渲染成 PNG，便于离线检查视觉效果（开发辅助脚本）。

    python tests/screenshot.py [输出目录]

会用临时数据目录构造「有版本 + 有账户」的示例状态，不触碰真实存档。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication  # noqa: E402

from launcher.core.auth import create_offline_account  # noqa: E402
from launcher.core.config import Config  # noqa: E402
from launcher.ui.app import MainWindow  # noqa: E402
from launcher.ui.theme import apply_theme  # noqa: E402

PAGES = ["home", "versions", "accounts", "mods", "settings"]


def make_fixture(root: Path) -> Config:
    game_dir = root / "game"
    versions = game_dir / "versions"

    for vid, main_class in (
        ("1.20.4", "net.minecraft.client.main.Main"),
        ("1.20.4-forge-49.0.30", "cpw.mods.bootstraplauncher.BootstrapLauncher"),
        ("1.19.4-fabric-0.15.7", "net.fabricmc.loader.impl.launch.knot.KnotClient"),
    ):
        directory = versions / vid
        directory.mkdir(parents=True, exist_ok=True)
        data = {
            "id": vid,
            "type": "release",
            "mainClass": main_class,
            "libraries": [],
            "assetIndex": {"id": "12"},
        }
        if "forge" in vid or "fabric" in vid:
            data["inheritsFrom"] = "1.20.4" if "forge" in vid else "1.19.4"
        (directory / f"{vid}.json").write_text(json.dumps(data), encoding="utf-8")
        (directory / f"{vid}.jar").write_bytes(b"")

    (versions / "1.19.4").mkdir(parents=True, exist_ok=True)
    (versions / "1.19.4" / "1.19.4.json").write_text(
        json.dumps({"id": "1.19.4", "type": "release", "mainClass": "net.minecraft.client.main.Main"}),
        encoding="utf-8",
    )
    (versions / "1.19.4" / "1.19.4.jar").write_bytes(b"")

    config = Config(root / "config.json")
    config.set("game_dir", str(game_dir))
    config.set("ui.last_version", "1.20.4-forge-49.0.30")
    config.put_instance(config.get_instance("1.20.4-forge-49.0.30", create=True))

    from launcher.core import auth

    for name in ("Steve", "Alex"):
        auth.accounts_db.add(create_offline_account(name))
    return config


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("screenshots")
    out.mkdir(parents=True, exist_ok=True)

    root = Path(tempfile.mkdtemp(prefix="pylauncher-shot-"))
    import os

    os.environ["PYLAUNCHER_HOME"] = str(root)

    config = make_fixture(root)

    app = QApplication(sys.argv[:1])
    apply_theme(app)
    window = MainWindow(config)
    window.refresh_all()   # run() 之外使用时要手动补一次首屏刷新
    window.resize(1120, 760)
    window.show()
    app.processEvents()

    for key in PAGES:
        window.navigate(key)
        app.processEvents()
        app.processEvents()
        target = out / f"{key}.png"
        window.grab().save(str(target))
        print(f"saved {target}")

    # 版本页的三个标签
    window.navigate("versions")
    versions = window.pages["versions"]
    for index, name in enumerate(("versions-local", "versions-download", "versions-loader")):
        versions.tabs.setCurrentIndex(index)
        app.processEvents()
        window.grab().save(str(out / f"{name}.png"))
    print("saved version tabs")

    # 设置页滚到「桌面快捷方式」卡片
    settings = window.pages["settings"]
    window.navigate("settings")
    settings.scroll.ensureWidgetVisible(settings.shortcut_card, 0, 40)
    app.processEvents()
    app.processEvents()
    window.grab().save(str(out / "settings-shortcut.png"))
    print("saved settings-shortcut.png")

    # 单独渲染几个对话框
    from launcher.ui.pages.versions import InstanceSettingsDialog
    from launcher.ui.widgets import ProgressDialog

    dialog = InstanceSettingsDialog(window, "1.20.4", window)
    dialog.show()
    app.processEvents()
    dialog.grab().save(str(out / "dialog-instance.png"))
    dialog.close()

    progress = ProgressDialog("安装 1.20.4", window)
    progress.set_stage("下载 libraries…")
    progress.set_progress(42, 100)
    progress.show()
    app.processEvents()
    progress.grab().save(str(out / "dialog-progress.png"))
    progress.close()
    print("dialogs saved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
