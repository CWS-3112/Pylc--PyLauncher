"""Mod 页：按实例浏览、启用/禁用、导入与删除 Mod。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.mods import ModInfo, delete_mod, import_mods, scan_mods, set_mod_enabled
from ..helpers import reveal_path
from ..theme import METRICS, SPACE
from ..widgets import Card, EmptyState, caption
from ..anim import attach_tab_fade
from ..workers import Worker, start_worker
from .base import BasePage


class ModPage(BasePage):
    COLUMNS = ["启用", "名称", "版本", "加载器", "适用 MC", "大小", "操作"]

    def __init__(self, main) -> None:
        super().__init__(main)
        self._mods: list[ModInfo] = []
        self._online_hits: list = []
        self._build_ui()
        self.refresh()

    # ---------------------------------------------------------------- 界面

    def _build_ui(self) -> None:
        root = self.page_layout(self)
        header, self.header_sub = self.make_header(
            "Mod 管理", "按实例独立管理 Mod；禁用会改名为 .disabled，随时可恢复"
        )
        root.addWidget(header)

        card = Card()
        top = QHBoxLayout()
        top.setSpacing(SPACE["md"])
        top.addWidget(caption("实例"))
        self.version_combo = QComboBox()
        self.version_combo.setMinimumWidth(260)
        self.version_combo.setMinimumHeight(METRICS["control_height"])
        self.version_combo.currentIndexChanged.connect(self._reload_table)
        top.addWidget(self.version_combo, 1)

        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setProperty("quiet", True)
        self.refresh_btn.clicked.connect(self._reload_table)
        self.add_btn = QPushButton("添加 Mod…")
        self.add_btn.setProperty("accent", True)
        self.add_btn.clicked.connect(self._add_mods)
        self.folder_btn = QPushButton("打开目录")
        self.folder_btn.setProperty("quiet", True)
        self.folder_btn.clicked.connect(self._open_folder)
        self.enable_all_btn = QPushButton("全部启用")
        self.enable_all_btn.clicked.connect(lambda: self._toggle_all(True))
        self.disable_all_btn = QPushButton("全部禁用")
        self.disable_all_btn.clicked.connect(lambda: self._toggle_all(False))

        for button in (
            self.refresh_btn,
            self.add_btn,
            self.folder_btn,
            self.enable_all_btn,
            self.disable_all_btn,
        ):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            top.addWidget(button)
        card.add_layout(top)

        self.stack = QStackedWidget()
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(METRICS["row_height"])
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header_view.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header_view.setFixedHeight(METRICS["header_height"])
        for col in (0, 2, 3, 4, 5, 6):
            header_view.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.stack.addWidget(self.table)

        self.empty = EmptyState(
            "这个实例还没有 Mod",
            "点击「添加 Mod…」导入 .jar 文件，或直接把文件放进 Mod 目录后刷新。",
        )
        self.stack.addWidget(self.empty)

        # 已安装 / 在线下载 两个视图共用一个卡片，避免页面过长
        from PySide6.QtWidgets import QTabWidget

        self.tabs = QTabWidget()
        self.tabs.addTab(self.stack, "已安装")
        self.tabs.addTab(self._build_online_tab(), "在线下载")
        attach_tab_fade(self.tabs)
        card.add(self.tabs, 1)

        self.summary = QLabel("")
        self.summary.setProperty("micro", True)
        self.summary.setWordWrap(True)
        card.add(self.summary)
        root.addWidget(card, 1)

    # ---------------------------------------------------------------- 在线下载

    def _build_online_tab(self) -> QWidget:
        """Modrinth 在线模组库：搜索 → 选版本 → 下载到当前实例。"""
        from PySide6.QtWidgets import QLineEdit, QProgressBar

        from ..widgets import BusySpinner

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(SPACE["md"], SPACE["md"], SPACE["md"], SPACE["md"])
        layout.setSpacing(SPACE["sm"])

        bar = QHBoxLayout()
        bar.setSpacing(SPACE["sm"])
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索模组（如 jei、sodium）…")
        self.search_edit.setMinimumHeight(METRICS["control_height"])
        self.search_edit.returnPressed.connect(self._search_online)
        bar.addWidget(self.search_edit, 1)

        self.loader_combo = QComboBox()
        self.loader_combo.setMinimumHeight(METRICS["control_height"])
        self.loader_combo.addItem("不限加载器", "")
        self.loader_combo.addItem("Fabric", "fabric")
        self.loader_combo.addItem("Forge", "forge")
        self.loader_combo.addItem("NeoForge", "neoforge")
        bar.addWidget(self.loader_combo)

        self.search_btn = QPushButton("搜索")
        self.search_btn.setProperty("accent", True)
        self.search_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.search_btn.clicked.connect(self._search_online)
        bar.addWidget(self.search_btn)
        layout.addLayout(bar)

        self.online_spinner = BusySpinner(14)
        self.online_hint = QLabel("输入关键词后回车即可搜索（数据源：Modrinth 官方接口）")
        self.online_hint.setProperty("micro", True)
        hint_row = QHBoxLayout()
        hint_row.setSpacing(SPACE["xs"])
        hint_row.addWidget(self.online_spinner)
        hint_row.addWidget(self.online_hint, 1)
        layout.addLayout(hint_row)

        self.online_table = QTableWidget(0, 4)
        self.online_table.setHorizontalHeaderLabels(["名称", "简介", "下载量", "操作"])
        self.online_table.verticalHeader().setVisible(False)
        self.online_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.online_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.online_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.online_table.setAlternatingRowColors(True)
        self.online_table.verticalHeader().setDefaultSectionSize(METRICS["row_height"])
        oh = self.online_table.horizontalHeader()
        oh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        oh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        oh.setFixedHeight(METRICS["header_height"])
        for col in (0, 2, 3):
            oh.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.online_table, 1)

        self.online_progress = QProgressBar()
        self.online_progress.setRange(0, 0)
        self.online_progress.setVisible(False)
        self.online_progress.setFixedHeight(16)
        layout.addWidget(self.online_progress)
        return page

    def _search_online(self) -> None:
        keyword = self.search_edit.text().strip()
        loader = str(self.loader_combo.currentData() or "")
        vid = str(self.version_combo.currentData() or "")
        self.online_spinner.start()
        self.online_hint.setText("正在搜索…")
        self.search_btn.setEnabled(False)
        self.online_table.setRowCount(0)

        def work(on_progress, on_message, should_cancel=None):
            from ...core import modrinth

            return modrinth.search(keyword, kind="mod", loader=loader,
                                   game_version=vid or "", limit=20)

        def on_done(hits):
            self.online_spinner.stop()
            self.search_btn.setEnabled(True)
            self._online_hits = hits
            for hit in hits:
                row = self.online_table.rowCount()
                self.online_table.insertRow(row)
                self.online_table.setItem(row, 0, QTableWidgetItem(hit.title))
                self.online_table.setItem(row, 1, QTableWidgetItem(hit.description))
                self.online_table.setItem(row, 2, QTableWidgetItem(f"{hit.downloads:,}"))

                actions = QWidget()
                actions.setObjectName("CellHost")
                al = QHBoxLayout(actions)
                al.setContentsMargins(2, 0, 2, 0)
                al.setSpacing(SPACE["xs"])
                install_btn = QPushButton("下载安装")
                install_btn.setProperty("inTableCell", True)
                install_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                install_btn.clicked.connect(lambda _=False, h=hit: self._install_online(h))
                al.addWidget(install_btn)
                self.online_table.setCellWidget(row, 3, actions)
            self.online_hint.setText(
                f"找到 {len(hits)} 个结果" if hits else "没有找到匹配的模组，试试换个关键词"
            )

        def on_error(message):
            self.online_spinner.stop()
            self.search_btn.setEnabled(True)
            self.online_hint.setText(f"搜索失败：{message.splitlines()[0]}")
            self.online_hint.setToolTip(message)

        worker = Worker(work)
        worker.succeeded.connect(on_done)
        worker.failed.connect(on_error)
        start_worker(self, worker)

    def _install_online(self, hit) -> None:
        """把选中的模组装进当前实例的 mods 目录。"""
        directory = self._mods_dir()
        if directory is None:
            self.warn("请先安装并选择一个游戏版本")
            return
        vid = str(self.version_combo.currentData() or "")
        loader = str(self.loader_combo.currentData() or "")
        self.online_progress.setVisible(True)
        self.main.set_busy(True, f"正在安装 {hit.title}…")

        def work(on_progress, on_message, should_cancel=None):
            from ...core import modrinth

            file = modrinth.best_version(
                hit.project_id,
                loaders=[loader] if loader else [],
                game_version=vid or "",
            )
            if file is None:
                raise RuntimeError("该项目没有可下载的文件")
            return modrinth.download_file(file, directory, base=directory)

        def on_done(path):
            self.online_progress.setVisible(False)
            self.main.set_busy(False)
            self.info(f"已安装：{path.name}\n重启游戏后生效。")
            self._reload_table()

        def on_error(message):
            self.online_progress.setVisible(False)
            self.main.set_busy(False)
            self.error(f"安装失败：\n{message}")

        worker = Worker(work)
        worker.succeeded.connect(on_done)
        worker.failed.connect(on_error)
        start_worker(self, worker)

    # ---------------------------------------------------------------- 数据

    def refresh(self) -> None:
        from ...core.manifest import scan_local_versions

        current = self.version_combo.currentData()
        self.version_combo.blockSignals(True)
        self.version_combo.clear()
        for v in scan_local_versions(self.game_dir()):
            self.version_combo.addItem(f"{v.id}   [{v.loader}]", v.id)
        if current:
            idx = self.version_combo.findData(current)
            if idx >= 0:
                self.version_combo.setCurrentIndex(idx)
        self.version_combo.blockSignals(False)
        self._reload_table()

    def _mods_dir(self) -> Path | None:
        vid = self.version_combo.currentData()
        if not vid:
            return None
        ins = self.config.get_instance(vid, create=True)
        return ins.mods_directory(self.game_dir())

    def _reload_table(self) -> None:
        self.table.setRowCount(0)
        directory = self._mods_dir()
        if directory is None:
            self.stack.setCurrentIndex(1)
            self.empty.set_text("尚未安装任何版本", "请先到「版本管理」安装一个游戏版本。")
            self.summary.setText("")
            self.header_sub.setText("按实例独立管理 Mod")
            return

        self._mods = scan_mods(directory)
        for mod in self._mods:
            row = self.table.rowCount()
            self.table.insertRow(row)

            # 启用列
            cb = QCheckBox()
            cb.setChecked(mod.enabled)
            cb.setCursor(Qt.CursorShape.PointingHandCursor)
            cb.stateChanged.connect(lambda state, m=mod: self._on_checkbox(m, state))
            cell = QWidget()
            cell.setObjectName("CellHost")
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell_layout.addWidget(cb)
            self.table.setCellWidget(row, 0, cell)

            name_item = QTableWidgetItem(mod.display_name)
            name_item.setToolTip(mod.path.name)
            self.table.setItem(row, 1, name_item)
            self.table.setItem(row, 2, QTableWidgetItem(mod.version or "-"))
            self.table.setItem(row, 3, QTableWidgetItem(mod.loader))
            self.table.setItem(row, 4, QTableWidgetItem(mod.mc_version or "-"))
            size_item = QTableWidgetItem(f"{mod.size / 1024:.0f} KB")
            size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 5, size_item)

            actions = QWidget()
            actions.setObjectName("CellHost")
            al = QHBoxLayout(actions)
            al.setContentsMargins(2, 0, 2, 0)
            al.setSpacing(SPACE["xs"])
            del_btn = QPushButton("删除")
            del_btn.setProperty("inTableCell", True)
            del_btn.setProperty("danger", True)
            del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            del_btn.clicked.connect(lambda _=False, m=mod: self._delete(m))
            al.addWidget(del_btn)
            self.table.setCellWidget(row, 6, actions)

            if mod.parse_error:
                self.table.item(row, 1).setToolTip(f"{mod.path.name}\n解析警告：{mod.parse_error}")

        enabled = sum(1 for m in self._mods if m.enabled)
        self.stack.setCurrentIndex(0 if self._mods else 1)
        if not self._mods:
            self.empty.set_text("这个实例还没有 Mod", "点击「添加 Mod…」导入 .jar 文件后刷新。")
        self.summary.setText(
            f"共 {len(self._mods)} 个 Mod（启用 {enabled}，禁用 {len(self._mods) - enabled}）"
            f"  ·  目录：{directory}"
        )
        self.header_sub.setText(f"当前实例：{self.version_combo.currentText() or '-'}")

    # ---------------------------------------------------------------- 操作

    def _on_checkbox(self, mod: ModInfo, state: int) -> None:
        # 表格刷新会触发 stateChanged，用「当前是否一致」过滤掉回环
        enabled = bool(int(state))
        if mod.enabled == enabled:
            return
        self._toggle_mod(mod, enabled)

    def _toggle_mod(self, mod: ModInfo, enabled: bool) -> None:
        try:
            set_mod_enabled(mod, enabled)
            self.flash(f"{'已启用' if enabled else '已禁用'} {mod.display_name}")
        except OSError as exc:
            self.error(str(exc))
        self._reload_table()

    def _toggle_all(self, enabled: bool) -> None:
        for mod in list(self._mods):
            if mod.enabled != enabled:
                try:
                    set_mod_enabled(mod, enabled)
                except OSError:
                    pass
        self._reload_table()
        self.flash(f"已{'启用' if enabled else '禁用'}全部 Mod")

    def _delete(self, mod: ModInfo) -> None:
        if not self.ask(f"确定删除 {mod.path.name} 吗？此操作不可撤销。"):
            return
        try:
            delete_mod(mod)
            self.flash(f"已删除 {mod.path.name}")
        except OSError as exc:
            self.error(str(exc))
        self._reload_table()

    def _add_mods(self) -> None:
        directory = self._mods_dir()
        if directory is None:
            self.warn("请先安装一个游戏版本。")
            return
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择 Mod 文件", str(Path.home()), "Mod 文件 (*.jar *.zip);;所有文件 (*.*)"
        )
        if not files:
            return
        imported = import_mods(directory, [Path(f) for f in files])
        self._reload_table()
        self.info(f"已导入 {len(imported)} 个 Mod。")

    def _open_folder(self) -> None:
        directory = self._mods_dir()
        if directory:
            reveal_path(directory)
