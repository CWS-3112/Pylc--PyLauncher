"""界面冒烟测试：在临时目录中构建主窗口，逐个切换页面并捕获异常。

    python tests/smoke_ui.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 测试绝不能往真实桌面写快捷方式
os.environ.setdefault("PYLAUNCHER_NO_AUTOSHORTCUT", "1")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialogButtonBox  # noqa: E402

from launcher.core.config import Config  # noqa: E402
from launcher.ui.app import MainWindow  # noqa: E402
from launcher.ui.theme import COLORS, apply_theme  # noqa: E402

PAGES = ["home", "versions", "accounts", "mods", "settings"]
failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  [OK]   {name}")
    else:
        failures.append(name)
        print(f"  [FAIL] {name}  {detail}")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pylauncher-ui-"))
    config = Config(tmp / "config.json")
    config.set("game_dir", str(tmp / "game"))

    app = QApplication(sys.argv)
    apply_theme(app)

    window = MainWindow(config)
    window.setWindowTitle("PyLauncher — 界面冒烟测试")
    window.show()

    def step() -> None:
        for key in PAGES:
            try:
                window.navigate(key)
                page = window.pages[key]
                refresh = getattr(page, "refresh", None)
                if callable(refresh):
                    refresh()
                print(f"  [OK]   页面 {key}")
            except Exception:  # noqa: BLE001
                failures.append(key)
                print(f"  [FAIL] 页面 {key}\n{traceback.format_exc()}")

        # —— 主题与窗口
        try:
            window.set_status("冒烟测试")
            window._update_chip()
            window.flash("提示测试")
            check("状态栏与侧边栏统计", True)
        except Exception:  # noqa: BLE001
            check("状态栏与侧边栏统计", False, traceback.format_exc())

        check("窗口图标已设置", not window.windowIcon().isNull())
        check("样式表已加载", bool(app.styleSheet()))
        check("调色板页面底色生效", app.palette().window().color().name() == COLORS["bg"].lower())
        check("最小尺寸适配小屏", window.minimumWidth() >= 800, str(window.minimumWidth()))
        check("快捷导航快捷键数量", len(window.nav_group.buttons()) == len(PAGES))
        check("加载指示器存在", window.busy is not None)

        # —— 版本页三个标签可切换
        versions = window.pages["versions"]
        window.navigate("versions")
        for index in range(versions.tabs.count()):
            versions.tabs.setCurrentIndex(index)
            app.processEvents()
        check("版本页标签可切换", versions.tabs.count() == 3)

        # —— 空状态堆栈
        check(
            "本地版本空状态已生效",
            versions.local_stack.currentIndex() == 1,
            str(versions.local_stack.currentIndex()),
        )

        # —— 设置页：桌面快捷方式卡片
        settings = window.pages["settings"]
        window.navigate("settings")
        check("快捷方式卡片存在", settings.shortcut_card is not None)
        check("快捷方式状态徽标有文案", bool(settings.shortcut_badge.text()))
        check(
            "启动目标指向启动器入口",
            "main.py" in settings.shortcut_target_label.text()
            or "PyLauncher" in settings.shortcut_target_label.text(),
            settings.shortcut_target_label.text(),
        )
        check(
            "未创建时删除按钮禁用",
            settings._shortcut_path is not None or not settings.remove_shortcut_btn.isEnabled(),
        )

        # —— 标准按钮中文化（实例设置对话框）
        try:
            from launcher.ui.pages.versions import InstanceSettingsDialog

            dialog = InstanceSettingsDialog(window, "1.20.4", window)
            box = dialog.findChild(QDialogButtonBox)
            labels = [b.text() for b in (box.buttons() if box else [])]
            check("对话框按钮为中文", all(lbl in ("保存", "取消") for lbl in labels), str(labels))
            dialog.deleteLater()
        except Exception:  # noqa: BLE001
            check("对话框按钮为中文", False, traceback.format_exc())

        # —— 首页按钮状态
        home = window.pages["home"]
        window.navigate("home")
        check(
            "无版本时启动按钮禁用并提示原因",
            not home.launch_btn.isEnabled() and "无" in home.launch_btn.text(),
            f"{home.launch_btn.isEnabled()} / {home.launch_btn.text()}",
        )
        check("空状态引导按钮可见", home.go_versions_btn.isVisible())

        QTimer.singleShot(120, app.quit)

    QTimer.singleShot(400, step)
    app.exec()

    print("\n" + "=" * 60)
    if failures:
        print(f"界面冒烟测试失败：{', '.join(failures)}")
        return 1
    print(f"界面冒烟测试通过（{len(PAGES)} 个页面 + 快捷方式/主题检查全部正常）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
