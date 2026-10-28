"""材质包与光影：扫描、启用 / 禁用、导入本地文件、从 Modrinth 在线安装。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core import modrinth, resources
from ..helpers import reveal_path
from ..theme import METRICS, SPACE
from ..widgets import BusySpinner, Card, EmptyState, caption
from ..anim import attach_tab_fade
from ..workers import Worker, start_worker
from .base import BasePage


class ResourcePage(BasePage):
    """材质包 / 光影管理。"""

    def __init__(self, main) -> None:
        super().__init__(main)
        self._kind = "resourcepack"
        self._packs: list[resources.ResourcePack] = []
        self._hits: list = []
        self._build_ui()
        self.refresh()

    # ---------------------------------------------------------------- 界面

    def _build_ui(self) -> None:
        root = self.page_layout(self)
        header, self.header_sub = self.make_header(
            "材质包与光影", "按实例管理；光影需要安装 Iris 或 OptiFine 才会生效"
        )
        root.addWidget(header)

        card = Card()
        bar = QHBoxLayout()
        bar.setSpacing(SPACE["md"])
        bar.addWidget(caption("实例"))
        self.version_combo = QComboBox()
        self.version_combo.setMinimumHeight(METRICS["control_height"])
        self.version_combo.setMinimumWidth(240)
        self.version_combo.currentIndexChanged.connect(self._reload)
        bar.addWidget(self.version_combo, 1)

        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setProperty("quiet", True)
        self.refresh_btn.clicked.connect(self._reload)
        self.import_btn = QPushButton("导入本地包…")
        self.import_btn.setProperty("accent", True)
        self.import_btn.clicked.connect(self._import_local)
        self.folder_btn = QPushButton("打开目录")
        self.folder_btn.setProperty("quiet", True)
        self.folder_btn.clicked.connect(self._open_folder)
        for button in (self.refresh_btn, self.import_btn, self.folder_btn):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            bar.addWidget(button)
        card.add_layout(bar)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_list_tab(), "已安装")
        self.tabs.addTab(self._build_online_tab(), "在线下载")
        attach_tab_fade(self.tabs)
        card.add(self.tabs, 1)

        self.summary = QLabel("")
        self.summary.setProperty("micro", True)
        self.summary.setWordWrap(True)
        card.add(self.summary)
        root.addWidget(card, 1)

    def _build_list_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(SPACE["md"], SPACE["md"], SPACE["md"], SPACE["md"])
        layout.setSpacing(SPACE["sm"])

        kind_row = QHBoxLayout()
        kind_row.setSpacing(SPACE["sm"])
        self.pack_btn = QPushButton("材质包")
        self.shader_btn = QPushButton("光影")
        for button, kind in ((self.pack_btn, "resourcepack"), (self.shader_btn, "shaderpack")):
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, k=kind: self._switch_kind(k))
            kind_row.addWidget(button)
        self.pack_btn.setChecked(True)
        kind_row.addStretch(1)
        layout.addLayout(kind_row)

        self.stack = QStackedWidget()
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["启用", "名称", "大小", "操作"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(METRICS["row_height"])
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        head.setFixedHeight(METRICS["header_height"])
        for col in (0, 2, 3):
            head.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.stack.addWidget(self.table)

        self.empty = EmptyState(
            "还没有安装任何包",
            "点击「导入本地包…」选择 zip，或直接到「在线下载」里从 Modrinth 安装。",
        )
        self.stack.addWidget(self.empty)
        layout.addWidget(self.stack, 1)
        return page

    def _build_online_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(SPACE["md"], SPACE["md"], SPACE["md"], SPACE["md"])
        layout.setSpacing(SPACE["sm"])

        bar = QHBoxLayout()
        bar.setSpacing(SPACE["sm"])
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索材质包 / 光影…")
        self.search_edit.setMinimumHeight(METRICS["control_height"])
        self.search_edit.returnPressed.connect(self._search_online)
        bar.addWidget(self.search_edit, 1)
        self.search_btn = QPushButton("搜索")
        self.search_btn.setProperty("accent", True)
        self.search_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.search_btn.clicked.connect(self._search_online)
        bar.addWidget(self.search_btn)
        layout.addLayout(bar)

        hint_row = QHBoxLayout()
        self.spinner = BusySpinner(14)
        self.hint = QLabel("搜索结果会直接安装到当前实例的对应目录")
        self.hint.setProperty("micro", True)
        hint_row.addWidget(self.spinner)
        hint_row.addWidget(self.hint, 1)
        layout.addLayout(hint_row)

        self.online_table = QTableWidget(0, 3)
        self.online_table.setHorizontalHeaderLabels(["名称", "简介", "操作"])
        self.online_table.verticalHeader().setVisible(False)
        self.online_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.online_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.online_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.online_table.setAlternatingRowColors(True)
        self.online_table.verticalHeader().setDefaultSectionSize(METRICS["row_height"])
        head = self.online_table.horizontalHeader()
        head.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        head.setFixedHeight(METRICS["header_height"])
        for col in (0, 2):
            head.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.online_table, 1)
        return page

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
        self._reload()

    def _instance_dir(self) -> Path | None:
        vid = str(self.version_combo.currentData() or "")
        if not vid:
            return None
        ins = self.config.get_instance(vid, create=True)
        return ins.game_directory(self.game_dir())

    def _switch_kind(self, kind: str) -> None:
        self._kind = kind
        self.pack_btn.setChecked(kind == "resourcepack")
        self.shader_btn.setChecked(kind == "shaderpack")
        self._reload()

    def _reload(self) -> None:
        directory = self._instance_dir()
        self.table.setRowCount(0)
        if directory is None:
            self.stack.setCurrentIndex(1)
            self.empty.set_text("尚未安装任何版本", "请先到「版本管理」安装一个游戏版本。")
            self.summary.setText("")
            return

        self._packs = resources.scan(directory, self._kind)
        for pack in self._packs:
            row = self.table.rowCount()
            self.table.insertRow(row)

            toggle = QPushButton("已启用" if pack.enabled else "已禁用")
            toggle.setProperty("inTableCell", True)
            toggle.setCursor(Qt.CursorShape.PointingHandCursor)
            toggle.clicked.connect(lambda _=False, p=pack: self._toggle(p))
            self.table.setCellWidget(row, 0, toggle)

            name_item = QTableWidgetItem(pack.name)
            name_item.setToolTip(pack.description or pack.name)
            self.table.setItem(row, 1, name_item)
            self.table.setItem(row, 2, QTableWidgetItem(pack.size_text))

            actions = QWidget()
            actions.setObjectName("CellHost")
            al = QHBoxLayout(actions)
            al.setContentsMargins(2, 0, 2, 0)
            al.setSpacing(SPACE["xs"])
            del_btn = QPushButton("删除")
            del_btn.setProperty("inTableCell", True)
            del_btn.setProperty("danger", True)
            del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            del_btn.clicked.connect(lambda _=False, p=pack: self._delete(p))
            al.addWidget(del_btn)
            self.table.setCellWidget(row, 3, actions)

        enabled = sum(1 for p in self._packs if p.enabled)
        self.stack.setCurrentIndex(0 if self._packs else 1)
        if not self._packs:
            self.empty.set_text(
                f"还没有安装{'光影' if self._kind == 'shaderpack' else '材质包'}",
                "点击「导入本地包…」，或到「在线下载」里从 Modrinth 安装。",
            )
        label = "光影" if self._kind == "shaderpack" else "材质包"
        self.summary.setText(
            f"共 {len(self._packs)} 个{label}（启用 {enabled}）  ·  "
            f"目录：{resources.packs_dir(directory, self._kind)}"
        )
        self.header_sub.setText(f"当前实例：{self.version_combo.currentText() or '-'} · {label}")

    # ---------------------------------------------------------------- 动作

    def _toggle(self, pack) -> None:
        try:
            resources.set_enabled(pack, not pack.enabled)
        except OSError as exc:
            self.error(f"切换失败：{exc}")
            return
        self._reload()
        self.flash(f"已{'启用' if not pack.enabled else '禁用'}：{pack.name}")

    def _delete(self, pack) -> None:
        if not self.ask(f"确定删除 {pack.name} 吗？"):
            return
        resources.remove(pack)
        self._reload()

    def _open_folder(self) -> None:
        directory = self._instance_dir()
        if directory is None:
            self.warn("请先选择版本")
            return
        reveal_path(resources.packs_dir(directory, self._kind))

    def _import_local(self) -> None:
        directory = self._instance_dir()
        if directory is None:
            self.warn("请先安装并选择一个游戏版本")
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择材质包 / 光影包", "",
            "压缩包 (*.zip *.mcpack);;所有文件 (*.*)"
        )
        if not paths:
            return
        ok = 0
        for path in paths:
            try:
                resources.install_from_file(Path(path), directory, self._kind)
                ok += 1
            except Exception as exc:  # noqa: BLE001
                self.error(f"导入失败：{path}\n{exc}")
        self._reload()
        if ok:
            self.flash(f"已导入 {ok} 个包")

    # ---------------------------------------------------------------- 在线

    def _search_online(self) -> None:
        keyword = self.search_edit.text().strip()
        self.spinner.start()
        self.hint.setText("正在搜索…")
        self.search_btn.setEnabled(False)
        self.online_table.setRowCount(0)

        def work(on_progress, on_message, should_cancel=None):
            return modrinth.search(keyword, kind=self._kind, limit=20)

        def on_done(hits):
            self.spinner.stop()
            self.search_btn.setEnabled(True)
            self._hits = hits
            for hit in hits:
                row = self.online_table.rowCount()
                self.online_table.insertRow(row)
                self.online_table.setItem(row, 0, QTableWidgetItem(hit.title))
                self.online_table.setItem(row, 1, QTableWidgetItem(hit.description))

                actions = QWidget()
                actions.setObjectName("CellHost")
                al = QHBoxLayout(actions)
                al.setContentsMargins(2, 0, 2, 0)
                install_btn = QPushButton("下载安装")
                install_btn.setProperty("inTableCell", True)
                install_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                install_btn.clicked.connect(lambda _=False, h=hit: self._install_online(h))
                al.addWidget(install_btn)
                self.online_table.setCellWidget(row, 2, actions)
            self.hint.setText(f"找到 {len(hits)} 个结果" if hits else "没有匹配结果")

        def on_error(message):
            self.spinner.stop()
            self.search_btn.setEnabled(True)
            self.hint.setText(f"搜索失败：{message.splitlines()[0]}")

        worker = Worker(work)
        worker.succeeded.connect(on_done)
        worker.failed.connect(on_error)
        start_worker(self, worker)

    def _install_online(self, hit) -> None:
        directory = self._instance_dir()
        if directory is None:
            self.warn("请先选择版本")
            return
        self.main.set_busy(True, f"正在安装 {hit.title}…")

        def work(on_progress, on_message, should_cancel=None):
            file = modrinth.best_version(hit.project_id)
            if file is None:
                raise RuntimeError("该项目没有可下载的文件")
            return resources.install_from_url(
                file.url, file.name, directory, self._kind
            )

        def on_done(path):
            self.main.set_busy(False)
            self.info(f"已安装：{Path(path).name}")
            self._reload()

        def on_error(message):
            self.main.set_busy(False)
            self.error(f"安装失败：\n{message}")

        worker = Worker(work)
        worker.succeeded.connect(on_done)
        worker.failed.connect(on_error)
        start_worker(self, worker)
