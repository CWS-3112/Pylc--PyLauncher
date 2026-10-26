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
from .base import BasePage


class ModPage(BasePage):
    COLUMNS = ["启用", "名称", "版本", "加载器", "适用 MC", "大小", "操作"]

    def __init__(self, main) -> None:
        super().__init__(main)
        self._mods: list[ModInfo] = []
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
        card.add(self.stack, 1)

        self.summary = QLabel("")
        self.summary.setProperty("micro", True)
        self.summary.setWordWrap(True)
        card.add(self.summary)
        root.addWidget(card, 1)

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
