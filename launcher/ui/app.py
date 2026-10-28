"""主窗口：侧边栏导航 + 分页容器 + 状态栏 + 游戏进程托管。"""

from __future__ import annotations

import time
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
from .pages.resources import ResourcePage
from .pages.servers import ServerPage
from .pages.settings import SettingsPage
from .pages.versions import VersionPage
from .theme import METRICS, SPACE, apply_theme
from .widgets import BusySpinner

NAV_ITEMS = [
    ("home", "启动游戏"),
    ("versions", "版本管理"),
    ("accounts", "账户管理"),
    ("mods", "Mod 管理"),
    ("resources", "材质与光影"),
    ("servers", "服务器联机"),
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
        # 构造阶段不做全局刷新：`run()` 会在窗口显示之后调用 `refresh_all()`，
        # 这样首帧只是轻量骨架，避免"空白窗口闪一下才出内容"。
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

        from .anim import CrossFadeStack

        self.stack = CrossFadeStack(central)
        outer.addWidget(self.stack, 1)

        # 页面**按需构建**：一次性实例化 7 个分页要 ~850ms，而这些控件
        # 用户在首屏根本看不到；改成首次导航时才创建，首屏只留首页。
        self._page_builders = {
            "home": HomePage,
            "versions": VersionPage,
            "accounts": AccountPage,
            "mods": ModPage,
            "resources": ResourcePage,
            "servers": ServerPage,
            "settings": SettingsPage,
        }
        self.pages: dict[str, QWidget] = {}
        self._dirty: set[str] = set()
        self._refreshed_at: dict[str, float] = {}
        self.ensure_page("home")

        self._build_statusbar()
        self._bind_shortcuts()

    # ---------------------------------------------------------------- 分页

    def ensure_page(self, key: str):
        """确保分页已构建（懒加载）；返回该页。"""
        page = self.pages.get(key)
        if page is not None:
            return page
        builder = self._page_builders.get(key)
        if builder is None:
            return None
        started = time.perf_counter()
        page = builder(self)
        self.pages[key] = page
        self.stack.addWidget(page)
        LOG.debug("构建分页 %s 用时 %.0fms", key, (time.perf_counter() - started) * 1000)
        return page

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

        # ---- 导航（按「玩游戏 / 装内容 / 设置」分组，条目多了也不至于糊成一片）
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        groups = [
            ("游戏", ["home", "servers"]),
            ("内容", ["versions", "mods", "resources"]),
            ("其它", ["accounts", "settings"]),
        ]
        order = {key: index for index, (key, _text) in enumerate(NAV_ITEMS)}
        self.nav_buttons: dict[str, QPushButton] = {}
        for title, keys in groups:
            label = QLabel(title)
            label.setObjectName("SidebarGroup")
            layout.addWidget(label)
            for key in keys:
                if key not in order:
                    continue
                text = dict(NAV_ITEMS)[key]
                btn = QPushButton(text)
                btn.setProperty("navButton", True)
                btn.setCheckable(True)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setToolTip(f"{text}（Ctrl+{order[key] + 1}）")
                btn.clicked.connect(lambda _=False, k=key: self.navigate(k))
                self.nav_group.addButton(btn)
                self.nav_buttons[key] = btn
                layout.addWidget(btn)
            layout.addSpacing(SPACE["sm"])

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
        refresh = getattr(page, "_refresh_shortcut_state", None) if page else None
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
        # 只连已构建的分页，其余在 ensure_page 时补连（见 _bind_lazy_signal）
        self.instancesChanged.connect(self._safe_refresh("home"))
        self.instancesChanged.connect(self._safe_refresh("mods"))
        self.accountsChanged.connect(self._safe_refresh("home"))
        self.gameDirChanged.connect(self._on_game_dir_changed)
        self.settingsChanged.connect(self._safe_refresh("home"))

    def _safe_refresh(self, key: str):
        """对懒加载分页安全的刷新槽：页面还没建就跳过，建了才刷。"""

        def slot() -> None:
            page = self.pages.get(key)
            if page is None:
                return
            method = getattr(page, "refresh", None)
            if callable(method) and self._needs_refresh(key):
                method()
                self._refreshed_at[key] = time.time()
                self._dirty.discard(key)

        return slot

    # ---------------------------------------------------------------- 渲染去重

    def mark_dirty(self, *keys: str) -> None:
        """标记分页数据已变化，下次导航时才会真正刷新。"""
        self._dirty.update(keys)

    def _needs_refresh(self, key: str, min_interval: float = 0.5) -> bool:
        """刚刷过（<0.5s）且没有脏标记时跳过，避免重复渲染整张表。"""
        if key in self._dirty:
            return True
        last = self._refreshed_at.get(key, 0.0)
        return (time.time() - last) > min_interval

    # ---------------------------------------------------------------- 导航

    def navigate(self, key: str) -> None:
        page = self.ensure_page(key)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        refresh = getattr(page, "refresh", None)
        if callable(refresh):
            # 避免重复渲染：短时间内反复切换（或信号重复触发）不再重建整张表，
            # 只有被标记为脏数据的页面才真正刷新。
            if self._needs_refresh(key):
                refresh()
                self._refreshed_at[key] = time.time()
                self._dirty.discard(key)
        # 按 key 定位按钮：分组后的按钮顺序与 NAV_ITEMS 不再一致，不能用下标
        button = getattr(self, "nav_buttons", {}).get(key)
        if button is not None:
            button.setChecked(True)

    def refresh_all(self) -> None:
        self.navigate("home")
        first = getattr(self, "nav_buttons", {}).get("home")
        if first is not None:
            first.setChecked(True)
        self._update_chip()

    def _update_chip(self) -> None:
        from ..core.manifest import scan_local_versions

        versions = scan_local_versions(self.game_dir())
        playable = [v for v in versions if v.playable]
        account = self.accounts_db.selected
        self._chip_versions = len(playable)
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
        self._mark_dirty_on_change()
        self.instancesChanged.emit()
        self._update_chip()

    def notify_accounts_changed(self) -> None:
        self.mark_dirty("home", "accounts")
        self.accountsChanged.emit()
        self._update_chip()

    def notify_settings_changed(self) -> None:
        self.settingsChanged.emit()
        self._update_chip()

    def notify_game_dir_changed(self) -> None:
        self.gameDirChanged.emit()

    def _mark_dirty_on_change(self) -> None:
        self.mark_dirty("home", "versions", "mods", "resources")

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

    def shutdown(self, wait_ms: int = 1500) -> None:
        """退出前收尾所有后台任务。

        不做这一步时，关闭窗口瞬间仍在跑的 QThread / 线程池会与解释器关闭竞争，
        日志出现 `cannot schedule new futures after interpreter shutdown`，
        严重时直接 abort —— 这是「来回切换后关闭就崩」的主要来源之一。
        """
        # 1) 服务器页的中继与局域网扫描（普通线程，需显式停）
        servers = self.pages.get("servers")
        if servers is not None:
            for attr in ("_scanner", "_relay"):
                obj = getattr(servers, attr, None)
                stop = getattr(obj, "stop", None)
                if callable(stop):
                    try:
                        stop()
                    except Exception:  # noqa: BLE001
                        pass
            timer = getattr(servers, "_scan_timer", None)
            if timer is not None:
                try:
                    timer.stop()
                except RuntimeError:
                    pass

        # 2) 各分页登记的 Worker（下载 / 探测 / 登录…）
        from .workers import wait_worker

        for page in list(self.pages.values()):
            for worker in list(getattr(page, "_workers", [])):
                try:
                    worker.request_cancel()
                except RuntimeError:
                    continue
            for worker in list(getattr(page, "_workers", [])):
                wait_worker(worker, wait_ms)

        # 3) 动画叠加层
        stack = getattr(self, "stack", None)
        dispose = getattr(stack, "_dispose_overlay", None)
        if callable(dispose):
            try:
                dispose()
            except RuntimeError:
                pass

        # 4) 共用的 IO 线程池
        try:
            from ..core.http import shutdown_io_pool

            shutdown_io_pool(wait=False)
        except Exception:  # noqa: BLE001
            pass

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
        self.shutdown()
        event.accept()


def run(config: Optional[Config] = None) -> int:
    """启动 GUI 应用。"""
    import sys

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app)
    app.setWindowIcon(app_icon())

    window = MainWindow(config)
    # 先让窗口可见（此时只有骨架，绘制极快），随后再填充数据；
    # 反过来（先填数据后 show）会看到一次空白窗口闪烁。
    window.show()
    QApplication.processEvents()
    window.refresh_all()
    if hasattr(window, "stack") and hasattr(window.stack, "setCurrentIndex"):
        window.stack.setCurrentIndex(0)
    try:
        return app.exec()
    finally:
        # 兜底：即便窗口被强制关闭（未走 closeEvent），也要收尾后台线程
        try:
            window.shutdown(300)
        except Exception:  # noqa: BLE001
            pass
