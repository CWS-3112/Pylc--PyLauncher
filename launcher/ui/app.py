"""主窗口：侧边栏导航 + 分页容器 + 状态栏 + 游戏进程托管。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .. import LAUNCHER_NAME, LAUNCHER_VERSION
from ..core import paths as P
from ..core.auth import AccountDatabase, accounts_db
from ..core.config import Config, get_config
from ..core.launcher_core import GameProcess
from ..core.utils import LOG, setup_logging
from .icons import app_icon, render_logo
from .pages.accounts import AccountPage
from .pages.home import HomePage
from .pages.mods import ModPage
from .pages.settings import SettingsPage
from .pages.versions import VersionPage
from .theme import METRICS, SPACE, apply_theme
from .widgets import BusySpinner

NAV_ITEMS = [
    ("home", "启动游戏"),
    ("versions", "版本管理"),
    ("accounts", "账户管理"),
    ("mods", "Mod 管理"),
    ("settings", "设置"),
]


class MainWindow(QMainWindow):
    instancesChanged = Signal()
    accountsChanged = Signal()
    gameDirChanged = Signal()
    settingsChanged = Signal()

    def __init__(self, config: Optional[Config] = None) -> None:
        super().__init__()
        setup_logging()
        self.config = config or get_config()
        self.accounts_db: AccountDatabase = accounts_db
        self._process: Optional[GameProcess] = None
        self._process_version: str = ""
        self._flash_timer: Optional[QTimer] = None

        self.setWindowTitle(f"{LAUNCHER_NAME} {LAUNCHER_VERSION}")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(METRICS["window_min_width"], METRICS["window_min_height"])
        self._apply_adaptive_geometry()

        self._build_ui()
        self._connect_signals()
        self.refresh_all()

        # 首次运行自动补上桌面快捷方式（幂等，失败不打扰）
        QTimer.singleShot(400, self._maybe_auto_create_shortcut)

    # ---------------------------------------------------------------- 尺寸

    def _apply_adaptive_geometry(self) -> None:
        """按屏幕可用区域选择初始尺寸，兼顾 1366×768 与小尺寸高分屏。"""
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.resize(1040, 700)
            return
        available = screen.availableGeometry()
        small = available.width() < 1180 or available.height() < 760
        if small:
            self.setMinimumSize(METRICS["window_min_width_small"], METRICS["window_min_height_small"])
            width = min(available.width() - 40, 1000)
            height = min(available.height() - 60, 660)
        else:
            width = min(1200, int(available.width() * 0.68))
            height = min(820, int(available.height() * 0.74))
        self.resize(max(width, self.minimumWidth()), max(height, self.minimumHeight()))
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(frame.topLeft())

    # ---------------------------------------------------------------- 界面

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("RootPage")
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_sidebar())

        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)

        self.pages: dict[str, QWidget] = {
            "home": HomePage(self),
            "versions": VersionPage(self),
            "accounts": AccountPage(self),
            "mods": ModPage(self),
            "settings": SettingsPage(self),
        }
        for key, page in self.pages.items():
            self.stack.addWidget(page)

        self._build_statusbar()
        self._bind_shortcuts()

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(METRICS["sidebar_width"])
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        layout.setSpacing(SPACE["xs"])

        # ---- 品牌区
        brand = QWidget()
        brand.setObjectName("SidebarBrand")
        brand_layout = QHBoxLayout(brand)
        brand_layout.setContentsMargins(SPACE["sm"], 0, 0, 0)
        brand_layout.setSpacing(SPACE["md"])

        logo = QLabel()
        logo.setPixmap(QPixmap.fromImage(render_logo(34)))
        logo.setFixedSize(34, 34)
        logo.setScaledContents(True)
        brand_layout.addWidget(logo)

        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(0)
        title = QLabel(LAUNCHER_NAME)
        title.setObjectName("SidebarTitle")
        sub = QLabel(f"v{LAUNCHER_VERSION} · MC 启动器")
        sub.setObjectName("SidebarSub")
        texts.addWidget(title)
        texts.addWidget(sub)
        brand_layout.addLayout(texts, 1)

        layout.addWidget(brand)
        layout.addSpacing(SPACE["lg"])

        # ---- 导航
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for index, (key, text) in enumerate(NAV_ITEMS):
            btn = QPushButton(f"{text}")
            btn.setProperty("navButton", True)
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(f"{text}（Ctrl+{index + 1}）")
            btn.clicked.connect(lambda _=False, k=key: self.navigate(k))
            self.nav_group.addButton(btn)
            layout.addWidget(btn)

        layout.addStretch(1)

        # ---- 底部状态卡
        footer = QFrame()
        footer.setObjectName("SidebarFooter")
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        footer_layout.setSpacing(SPACE["xs"])
        self.status_chip = QLabel("")
        self.status_chip.setObjectName("SidebarFooterLabel")
        self.status_chip.setWordWrap(True)
        footer_layout.addWidget(self.status_chip)
        layout.addWidget(footer)
        return sidebar

    def _maybe_auto_create_shortcut(self) -> None:
        """首次运行时自动创建桌面快捷方式。

        只在从未尝试过时执行；设置页随时可以手动创建或删除。
        失败一律不弹窗——用户可能只是没有桌面写入权限。
        """
        try:
            from .shortcut_flow import ensure_created_once

            result = ensure_created_once(self.config)
        except Exception as exc:  # noqa: BLE001 - 自动创建绝不能影响主流程
            LOG.warning("自动创建桌面快捷方式时发生异常：%s", exc)
            return
        if result is None:
            return
        if result.degraded:
            self.flash(f"已生成桌面启动脚本（未能创建原生快捷方式）：{result.path}")
        else:
            self.flash(f"已自动创建桌面快捷方式：{result.path}")
        page = self.pages.get("settings")
        refresh = getattr(page, "_refresh_shortcut_state", None)
        if callable(refresh):
            refresh()

    def _build_statusbar(self) -> None:
        bar = QStatusBar()
        bar.setSizeGripEnabled(False)
        self.setStatusBar(bar)

        self.status_label = QLabel("就绪")
        bar.addWidget(self.status_label, 1)

        self.busy = BusySpinner(14)
        bar.addPermanentWidget(self.busy)
        self.account_status = QLabel("")
        bar.addPermanentWidget(self.account_status)
        self.mirror_status = QLabel("")
        bar.addPermanentWidget(self.mirror_status)
        version_label = QLabel(f"{LAUNCHER_NAME} {LAUNCHER_VERSION}")
        bar.addPermanentWidget(version_label)

    def _bind_shortcuts(self) -> None:
        for index, (key, _text) in enumerate(NAV_ITEMS):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index + 1}"), self)
            shortcut.activated.connect(lambda k=key: self.navigate(k))
        refresh = QShortcut(QKeySequence.StandardKey.Refresh, self)
        refresh.activated.connect(self._refresh_current_page)

    def _refresh_current_page(self) -> None:
        page = self.stack.currentWidget()
        method = getattr(page, "refresh", None)
        if callable(method):
            method()
        self.flash("已刷新")

    def _connect_signals(self) -> None:
        self.instancesChanged.connect(self.pages["home"].refresh)
        self.instancesChanged.connect(self.pages["mods"].refresh)
        self.accountsChanged.connect(self.pages["home"].refresh)
        self.gameDirChanged.connect(self._on_game_dir_changed)
        self.settingsChanged.connect(self.pages["home"].refresh)

    # ---------------------------------------------------------------- 导航

    def navigate(self, key: str) -> None:
        page = self.pages.get(key)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        refresh = getattr(page, "refresh", None)
        if callable(refresh):
            refresh()
        for index, (nav_key, _text) in enumerate(NAV_ITEMS):
            if nav_key == key:
                buttons = self.nav_group.buttons()
                if index < len(buttons):
                    buttons[index].setChecked(True)
                break

    def refresh_all(self) -> None:
        self.navigate("home")
        if self.nav_group.buttons():
            self.nav_group.buttons()[0].setChecked(True)
        self._update_chip()

    def _update_chip(self) -> None:
        from ..core.manifest import scan_local_versions

        versions = scan_local_versions(self.game_dir())
        playable = [v for v in versions if v.playable]
        account = self.accounts_db.selected
        self.status_chip.setText(
            f"可用版本 {len(playable)} 个\n账户 {account.username if account else '未添加'}"
        )
        self.mirror_status.setText(
            "下载源 " + ("BMCLAPI" if str(self.config.get("mirror", "")) == "bmclapi" else "官方")
        )
        self.account_status.setText(
            f"账户：{account.username}（{account.type_label}）" if account else "账户：未登录"
        )

    # ---------------------------------------------------------------- 访问器

    def game_dir(self) -> Path:
        return Path(str(self.config.get("game_dir", P.default_game_dir())))

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def set_busy(self, busy: bool, text: str = "") -> None:
        """状态栏加载态：后台任务开始时调用，让用户知道程序没卡住。"""
        if busy:
            self.busy.start()
            if text:
                self.status_label.setText(text)
        else:
            self.busy.stop()

    def flash(self, text: str, timeout: int = 3200) -> None:
        """短暂提示：写入状态栏后自动恢复为「就绪」。"""
        self.status_label.setText(text)
        if self._flash_timer is not None:
            self._flash_timer.stop()
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(lambda: self.status_label.setText("就绪"))
        self._flash_timer.start(timeout)

    # ---------------------------------------------------------------- 通知

    def notify_instances_changed(self) -> None:
        self.instancesChanged.emit()
        self._update_chip()

    def notify_accounts_changed(self) -> None:
        self.accountsChanged.emit()
        self._update_chip()

    def notify_settings_changed(self) -> None:
        self.settingsChanged.emit()
        self._update_chip()

    def notify_game_dir_changed(self) -> None:
        self.gameDirChanged.emit()

    def _on_game_dir_changed(self) -> None:
        for page in self.pages.values():
            refresh = getattr(page, "refresh", None)
            if callable(refresh):
                refresh()
        self._update_chip()

    # ---------------------------------------------------------------- 进程

    def register_process(self, proc: GameProcess, version_id: str) -> None:
        self._process = proc
        self._process_version = version_id
        self.set_busy(False)

    def kill_game(self) -> None:
        if self._process and self._process.running:
            self._process.kill()
            self.set_status("游戏进程已结束")

    @property
    def game_running(self) -> bool:
        return bool(self._process and self._process.running)

    def on_game_exit(self, code: int) -> None:
        mins = 0
        if self._process:
            mins = self._process.elapsed / 60
        self.set_busy(False)
        self.set_status(
            f"{self._process_version} 已退出（代码 {code}，运行 {mins:.1f} 分钟）"
        )
        self._process = None
        if not self.isVisible():
            self.show()
            self.raise_()
            self.activateWindow()

    # ---------------------------------------------------------------- 关闭

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.game_running:
            answer = QMessageBox.question(
                self,
                "游戏正在运行",
                "《我的世界》仍在运行。确定要退出启动器吗？\n（游戏进程会被保留，但将无法再显示日志）",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        event.accept()


def run(config: Optional[Config] = None) -> int:
    """启动 GUI 应用。"""
    import sys

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app)
    app.setWindowIcon(app_icon())
    window = MainWindow(config)
    window.show()
    return app.exec()
