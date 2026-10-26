"""版本页：本地版本管理、远程版本下载、Forge / Fabric 安装、实例设置。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core import paths as P
from ...core.config import Instance
from ...core.http import DownloadManager
from ...core.installer import get_mirror, install_version, verify_version
from ...core.java import find_java
from ...core.manifest import LocalVersion, manifest, scan_local_versions
from ...core.modloader import (
    ForgeVersionInfo,
    install_fabric,
    install_forge,
    list_fabric_loaders,
    list_forge_versions,
    uninstall_version,
)
from ..helpers import format_timestamp, reveal_path
from ..theme import METRICS, SPACE
from ..widgets import (
    Card,
    EmptyState,
    PathPicker,
    ProgressDialog,
    caption,
    localize_buttons,
    make_heading,
)
from ..workers import Worker, start_worker, wait_worker
from .base import BasePage


class VersionPage(BasePage):
    def __init__(self, main) -> None:
        super().__init__(main)
        self._local_versions: list[LocalVersion] = []
        self._forge_cache: dict[str, list[ForgeVersionInfo]] = {}
        self._build_ui()
        self.refresh()

    # ---------------------------------------------------------------- 界面

    def _build_ui(self) -> None:
        root = self.page_layout(self)
        header, _sub = self.make_header(
            "版本管理", "安装原版游戏、校验文件完整性，或为版本套上 Forge / Fabric 加载器"
        )
        root.addWidget(header)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_local_tab(), "本地版本")
        self.tabs.addTab(self._build_download_tab(), "下载新版本")
        self.tabs.addTab(self._build_loader_tab(), "安装加载器")
        root.addWidget(self.tabs, 1)

    # ---------------------------------------------------------------- 本地

    def _build_local_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["xl"])
        layout.setSpacing(SPACE["md"])

        head = QHBoxLayout()
        head.setSpacing(SPACE["md"])
        self.local_summary = QLabel("")
        self.local_summary.setProperty("muted", True)
        head.addWidget(self.local_summary, 1)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setProperty("quiet", True)
        self.refresh_btn.clicked.connect(self.refresh)
        head.addWidget(self.refresh_btn)
        layout.addLayout(head)

        self.local_stack = QStackedWidget()
        self.local_table = QTableWidget(0, 5)
        self.local_table.setHorizontalHeaderLabels(["版本", "加载器", "类型", "JAR", "操作"])
        self.local_table.verticalHeader().setVisible(False)
        self.local_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.local_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.local_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.local_table.setAlternatingRowColors(True)
        self.local_table.verticalHeader().setDefaultSectionSize(METRICS["row_height"])
        header_view = self.local_table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header_view.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header_view.setFixedHeight(METRICS["header_height"])
        for col in range(1, 5):
            header_view.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.local_stack.addWidget(self.local_table)

        self.local_empty = EmptyState(
            "还没有安装任何游戏版本",
            "切换到「下载新版本」获取官方版本列表，选中后点击下载即可。",
        )
        self.local_stack.addWidget(self.local_empty)
        layout.addWidget(self.local_stack, 1)

        hint = QLabel("提示：删除版本只会移除 versions/<id> 目录，共享的 libraries / assets 不会被删除。")
        hint.setProperty("micro", True)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return page

    def _reload_local_table(self) -> None:
        self._local_versions = scan_local_versions(self.game_dir())
        table = self.local_table
        table.setRowCount(0)
        type_label = {
            "release": "正式版",
            "snapshot": "快照版",
            "old_beta": "Beta",
            "old_alpha": "Alpha",
        }

        for v in self._local_versions:
            row = table.rowCount()
            table.insertRow(row)
            table.setItem(row, 0, QTableWidgetItem(v.id))
            table.setItem(row, 1, QTableWidgetItem(v.loader))
            table.setItem(row, 2, QTableWidgetItem(type_label.get(v.type, v.type)))
            jar_item = QTableWidgetItem("✓" if v.jar_exists else "缺失")
            jar_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, 3, jar_item)

            actions = QWidget()
            actions_layout = QHBoxLayout(actions)
            actions_layout.setContentsMargins(2, 0, 2, 0)
            actions_layout.setSpacing(SPACE["xs"])

            settings_btn = QPushButton("设置")
            settings_btn.setProperty("inTableCell", True)
            settings_btn.clicked.connect(lambda _=False, vid=v.id: self._open_instance_settings(vid))
            folder_btn = QPushButton("目录")
            folder_btn.setProperty("inTableCell", True)
            folder_btn.clicked.connect(
                lambda _=False, vid=v.id: reveal_path(P.version_dir(self.game_dir(), vid))
            )
            verify_btn = QPushButton("校验")
            verify_btn.setProperty("inTableCell", True)
            verify_btn.clicked.connect(lambda _=False, vid=v.id: self._verify(vid))
            del_btn = QPushButton("删除")
            del_btn.setProperty("inTableCell", True)
            del_btn.setProperty("danger", True)
            del_btn.clicked.connect(lambda _=False, vid=v.id: self._delete(vid))

            for button in (settings_btn, folder_btn, verify_btn, del_btn):
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                actions_layout.addWidget(button)
            table.setCellWidget(row, 4, actions)

        has_rows = bool(self._local_versions)
        self.local_stack.setCurrentIndex(0 if has_rows else 1)
        self.local_summary.setText(
            f"共 {len(self._local_versions)} 个已安装版本" if has_rows else "尚无本地版本"
        )

    # ---------------------------------------------------------------- 下载

    def _build_download_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["xl"])
        layout.setSpacing(SPACE["md"])

        filters = QHBoxLayout()
        filters.setSpacing(SPACE["xl"])
        filters.addWidget(caption("类型"))
        self.show_release = QCheckBox("正式版")
        self.show_release.setChecked(True)
        self.show_snapshot = QCheckBox("快照版")
        self.show_old_beta = QCheckBox("Beta 版")
        self.show_old_alpha = QCheckBox("Alpha 版")
        for cb in (self.show_release, self.show_snapshot, self.show_old_beta, self.show_old_alpha):
            cb.stateChanged.connect(self._reload_remote_table)
            cb.setCursor(Qt.CursorShape.PointingHandCursor)
            filters.addWidget(cb)
        filters.addStretch(1)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索版本号…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setFixedWidth(200)
        self.search_edit.textChanged.connect(self._reload_remote_table)
        filters.addWidget(self.search_edit)
        layout.addLayout(filters)

        self.remote_stack = QStackedWidget()
        self.remote_table = QTableWidget(0, 4)
        self.remote_table.setHorizontalHeaderLabels(["版本", "类型", "发布日期", "操作"])
        self.remote_table.verticalHeader().setVisible(False)
        self.remote_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.remote_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.remote_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.remote_table.setAlternatingRowColors(True)
        self.remote_table.verticalHeader().setDefaultSectionSize(METRICS["row_height"])
        rh = self.remote_table.horizontalHeader()
        rh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        rh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        rh.setFixedHeight(METRICS["header_height"])
        for col in (1, 2, 3):
            rh.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.remote_stack.addWidget(self.remote_table)

        self.remote_empty = EmptyState(
            "还没有获取版本列表",
            "点击下方「获取版本列表」从 Mojang 官方（或镜像源）拉取全部可用版本。",
        )
        self.remote_stack.addWidget(self.remote_empty)
        layout.addWidget(self.remote_stack, 1)

        bar = QHBoxLayout()
        self.remote_summary = QLabel("")
        self.remote_summary.setProperty("muted", True)
        bar.addWidget(self.remote_summary, 1)
        self.fetch_btn = QPushButton("获取版本列表")
        self.fetch_btn.setProperty("accent", True)
        self.fetch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fetch_btn.clicked.connect(self._fetch_manifest)
        bar.addWidget(self.fetch_btn)
        layout.addLayout(bar)
        return page

    def _reload_remote_table(self) -> None:
        table = self.remote_table
        table.setRowCount(0)
        if not manifest.loaded:
            self.remote_stack.setCurrentIndex(1)
            self.remote_summary.setText("")
            return

        wanted = set()
        if self.show_release.isChecked():
            wanted.add("release")
        if self.show_snapshot.isChecked():
            wanted.add("snapshot")
        if self.show_old_beta.isChecked():
            wanted.add("old_beta")
        if self.show_old_alpha.isChecked():
            wanted.add("old_alpha")

        keyword = self.search_edit.text().strip().lower()
        installed = {v.id for v in self._local_versions}

        type_label = {
            "release": "正式版",
            "snapshot": "快照版",
            "old_beta": "Beta",
            "old_alpha": "Alpha",
            "custom": "自定义",
        }

        count = 0
        for v in manifest.all():
            if v.type not in wanted:
                continue
            if keyword and keyword not in v.id.lower():
                continue
            row = table.rowCount()
            table.insertRow(row)
            table.setItem(row, 0, QTableWidgetItem(v.id))
            table.setItem(row, 1, QTableWidgetItem(type_label.get(v.type, v.type)))
            table.setItem(row, 2, QTableWidgetItem(format_timestamp(v.release_time)))

            actions = QWidget()
            al = QHBoxLayout(actions)
            al.setContentsMargins(2, 0, 2, 0)
            al.setSpacing(SPACE["xs"])
            if v.id in installed:
                btn = QPushButton("已安装")
                btn.setEnabled(False)
            else:
                btn = QPushButton("下载")
                btn.clicked.connect(lambda _=False, vid=v.id: self._download(vid))
            btn.setProperty("inTableCell", True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            al.addWidget(btn)
            table.setCellWidget(row, 3, actions)
            count += 1

        if count:
            self.remote_stack.setCurrentIndex(0)
            self.remote_summary.setText(f"共 {count} 个版本可选")
        else:
            self.remote_stack.setCurrentIndex(1)
            self.remote_empty.set_text("没有符合条件的版本", "请调整筛选条件或清空搜索关键词。")
            self.remote_summary.setText(f"共 {count} 个版本可选")

    # ---------------------------------------------------------------- 加载器

    def _build_loader_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(SPACE["xl"], SPACE["xl"], SPACE["xl"], SPACE["xl"])
        layout.setSpacing(SPACE["lg"])

        card = Card(
            "安装 Forge / Fabric",
            subtitle="加载器会作为独立版本安装，原版与本加载器版本互不影响",
        )
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(SPACE["lg"])
        form.setVerticalSpacing(SPACE["lg"])
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.base_version_combo = QComboBox()
        self.base_version_combo.setMinimumHeight(METRICS["control_height"])
        self.base_version_combo.currentTextChanged.connect(self._on_base_version_changed)

        self.loader_type_combo = QComboBox()
        self.loader_type_combo.setMinimumHeight(METRICS["control_height"])
        self.loader_type_combo.addItem("Fabric", "fabric")
        self.loader_type_combo.addItem("Forge", "forge")
        self.loader_type_combo.currentIndexChanged.connect(self._on_loader_type_changed)

        self.loader_version_combo = QComboBox()
        self.loader_version_combo.setMinimumHeight(METRICS["control_height"])
        self.loader_version_combo.setEnabled(False)

        form.addRow("基于的 MC 版本：", self.base_version_combo)
        form.addRow("加载器类型：", self.loader_type_combo)
        form.addRow("加载器版本：", self.loader_version_combo)
        card.add_layout(form)

        card.add_divider()
        bar = QHBoxLayout()
        bar.setSpacing(SPACE["md"])
        bar.addStretch(1)
        self.reload_loader_btn = QPushButton("获取加载器列表")
        self.reload_loader_btn.clicked.connect(self._fetch_loader_list)
        self.install_loader_btn = QPushButton("安装")
        self.install_loader_btn.setProperty("primary", True)
        self.install_loader_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.install_loader_btn.clicked.connect(self._install_loader)
        bar.addWidget(self.reload_loader_btn)
        bar.addWidget(self.install_loader_btn)
        card.add_layout(bar)
        layout.addWidget(card)

        hint = QLabel(
            "说明：Fabric 由本启动器直接组装版本；Forge 会调用官方 Installer"
            "（需要 Java 与网络）。安装前请先在「下载新版本」中装好对应的原版。"
        )
        hint.setProperty("micro", True)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch(1)
        return page

    # ---------------------------------------------------------------- 刷新

    def refresh(self) -> None:
        self._reload_local_table()
        self._reload_base_versions()
        self._reload_remote_table()

    def _reload_base_versions(self) -> None:
        current = self.base_version_combo.currentText()
        self.base_version_combo.blockSignals(True)
        self.base_version_combo.clear()
        for v in self._local_versions:
            if v.loader == "vanilla":
                self.base_version_combo.addItem(v.id)
        if current:
            idx = self.base_version_combo.findText(current)
            if idx >= 0:
                self.base_version_combo.setCurrentIndex(idx)
        self.base_version_combo.blockSignals(False)

    # ---------------------------------------------------------------- 远程列表

    def _fetch_manifest(self) -> None:
        mirror = str(self.config.get("mirror", "bmclapi"))
        dlg = ProgressDialog("获取版本列表", self)

        should = {"stop": False}

        def work(on_progress, on_message):
            on_message("检查下载源可用性…")
            # 优先挑一个当前真能连通的源，避免「点了没反应然后失败」
            from ...core.http import available_mirror

            usable = available_mirror(mirror)
            if usable != mirror:
                on_message(f"检测到 {mirror} 不可用，已切换到 {usable}")
                self.config.set("mirror", usable)
            on_message("连接版本清单服务器…")
            manifest.refresh(usable, force=True)
            return len(manifest.all())

        worker = Worker(work)
        worker.message.connect(dlg.set_stage)
        worker.succeeded.connect(lambda n: (dlg.set_stage(f"共获取到 {n} 个版本"), dlg.accept(), self._reload_remote_table()))
        worker.failed.connect(dlg.done_with_error)
        # 协作式取消：只置标记，绝不使用 terminate（强杀线程会随机崩溃）
        dlg.cancelled.connect(lambda: (should.__setitem__("stop", True), worker.request_cancel()))
        start_worker(self, worker)
        dlg.exec()
        wait_worker(worker, 1500)

    # ---------------------------------------------------------------- 下载版本

    def _download(self, version_id: str) -> None:
        game_dir = self.game_dir()
        manager = DownloadManager()
        dlg = ProgressDialog(f"安装 {version_id}", self)

        def work(on_progress, on_message, should_cancel=None):
            # install_version 内部会先做源健康检查并自动切到可用源，
            # 因此这里直接读配置即可（切换结果会同步写回 config）
            return install_version(
                game_dir,
                version_id,
                str(self.config.get("mirror", "bmclapi")),
                with_assets=True,
                download_manager=manager,
                on_stage=on_message,
                on_progress=lambda stage, done, total: on_progress(done, total),
            )

        worker = Worker(work)
        worker.message.connect(dlg.set_stage)
        worker.progress.connect(dlg.set_progress)

        def on_done(ok):
            dlg.accept()
            if ok:
                self.info(f"{version_id} 安装完成。")
            else:
                self.warn(
                    f"{version_id} 安装未完成。\n\n"
                    "可能原因：网络不可用、下载源故障或磁盘无写入权限。\n"
                    "可在「设置」里切换下载源后重试，或查看日志了解细节。"
                )
            self.refresh()
            self.main.notify_instances_changed()

        worker.succeeded.connect(on_done)
        worker.failed.connect(dlg.done_with_error)
        # 协作式取消：置标记 + 通知下载器停止，绝不是 terminate
        dlg.cancelled.connect(worker.request_cancel)
        start_worker(self, worker, cancel_hook=manager.cancel)
        dlg.exec()
        wait_worker(worker, 2000)

    # ---------------------------------------------------------------- 校验/删除

    def _verify(self, version_id: str) -> None:
        mirror = str(self.config.get("mirror", "bmclapi"))
        dlg = ProgressDialog(f"校验 {version_id}", self, cancelable=False)

        def work(on_progress, on_message):
            on_message("扫描文件…")
            missing = verify_version(self.game_dir(), version_id, mirror)
            return missing

        worker = Worker(work)
        worker.message.connect(dlg.set_stage)

        def on_done(missing):
            dlg.accept()
            if not missing:
                self.info(f"{version_id} 完整无缺。")
            else:
                names = "\n".join(m.name for m in missing[:12])
                more = f"\n… 以及另外 {len(missing) - 12} 个文件" if len(missing) > 12 else ""
                if self.ask(f"检测到 {len(missing)} 个文件缺失或损坏：\n{names}{more}\n\n现在修复？"):
                    self._download(version_id)

        worker.succeeded.connect(on_done)
        worker.failed.connect(dlg.done_with_error)
        start_worker(self, worker)
        dlg.exec()
        wait_worker(worker, 1500)

    def _delete(self, version_id: str) -> None:
        if not self.ask(f"确定删除版本 {version_id} 吗？\n（仅删除版本目录，存档位于实例目录不会被自动移除）"):
            return
        uninstall_version(self.game_dir(), version_id)
        self.config.remove_instance(version_id)
        self.refresh()
        self.main.notify_instances_changed()

    # ---------------------------------------------------------------- 实例设置

    def _open_instance_settings(self, version_id: str) -> None:
        dlg = InstanceSettingsDialog(self.main, version_id, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.main.notify_instances_changed()

    # ---------------------------------------------------------------- 加载器动作

    def _on_base_version_changed(self, _text: str) -> None:
        self.loader_version_combo.clear()

    def _on_loader_type_changed(self, _idx: int) -> None:
        self.loader_version_combo.clear()

    def _fetch_loader_list(self) -> None:
        mc = self.base_version_combo.currentText()
        if not mc:
            self.warn("请先安装一个原版 MC（左侧「下载新版本」）作为基础版本。")
            return
        kind = self.loader_type_combo.currentData()
        mirror = str(self.config.get("mirror", "bmclapi"))
        dlg = ProgressDialog("获取加载器列表", self)

        def work(on_progress, on_message, should_cancel=None):
            on_message("检查下载源可用性…")
            from ...core.http import available_mirror

            usable = mirror
            try:
                usable = available_mirror(mirror)
                if usable != mirror:
                    on_message(f"已切换到 {usable}")
                    self.config.set("mirror", usable)
            except Exception:  # noqa: BLE001 - 探测失败也要继续尝试原源
                pass
            on_message(f"查询 {kind} 可用版本…")
            if kind == "fabric":
                return [("fabric", v.version, v.display) for v in list_fabric_loaders(mc, usable)]
            infos = self._forge_cache.get(mc)
            if infos is None:
                infos = list_forge_versions(mc, usable)
                self._forge_cache[mc] = infos
            return [("forge", i.version, i.display) for i in infos]

        worker = Worker(work)
        worker.message.connect(dlg.set_stage)

        def on_done(items):
            dlg.accept()
            self.loader_version_combo.clear()
            if not items:
                self.warn(f"未找到适用于 {mc} 的 {kind} 版本。")
                return
            for _, value, label in items:
                self.loader_version_combo.addItem(label, value)
            self.loader_version_combo.setEnabled(True)

        worker.succeeded.connect(on_done)
        worker.failed.connect(dlg.done_with_error)
        dlg.cancelled.connect(worker.request_cancel)
        start_worker(self, worker)
        dlg.exec()
        wait_worker(worker, 1500)

    def _install_loader(self) -> None:
        mc = self.base_version_combo.currentText()
        loader = self.loader_version_combo.currentData()
        kind = self.loader_type_combo.currentData()
        if not mc or not loader:
            self.warn("请先选择基础版本并获取加载器列表。")
            return

        mirror = str(self.config.get("mirror", "bmclapi"))
        game_dir = self.game_dir()
        manager = DownloadManager()
        dlg = ProgressDialog(f"安装 {kind}", self)

        def work(on_progress, on_message):
            if kind == "fabric":
                return install_fabric(
                    game_dir, mc, loader, mirror,
                    on_progress=lambda stage, done, total: (on_message(stage), on_progress(done, total)),
                )
            # Forge：需要 Java
            from ...core.java import resolve_java

            java = resolve_java(str(self.config.get("java.selected", "") or ""), 17)
            if java is None:
                raise RuntimeError("未找到 Java，无法运行 Forge Installer")
            infos = self._forge_cache.get(mc) or list_forge_versions(mc, mirror)
            info = next((i for i in infos if i.version == loader), None) or ForgeVersionInfo(
                mcversion=mc, version=loader
            )
            return install_forge(
                game_dir, info, java.path, mirror,
                on_progress=lambda stage, done, total: on_message(stage),
            )

        worker = Worker(work)
        worker.message.connect(dlg.set_stage)
        worker.succeeded.connect(lambda vid: (dlg.accept(), self.info(f"已安装：{vid}")))
        worker.failed.connect(dlg.done_with_error)
        dlg.cancelled.connect(worker.request_cancel)
        start_worker(self, worker, cancel_hook=manager.cancel)
        dlg.exec()
        wait_worker(worker, 2000)
        self.refresh()
        self.main.notify_instances_changed()


# -------------------------------------------------------------------- 实例设置


class InstanceSettingsDialog(QDialog):
    """单版本实例的隔离与启动参数配置。"""

    def __init__(self, main, version_id: str, parent=None) -> None:
        super().__init__(parent)
        self.main = main
        self.config = main.config
        self.version_id = version_id
        self.instance = self.config.get_instance(version_id, create=True)
        self.setWindowTitle(f"实例设置 — {version_id}")
        self.resize(560, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(SPACE["xxl"], SPACE["xl"], SPACE["xxl"], SPACE["lg"])
        root.setSpacing(SPACE["lg"])

        title = QLabel(f"实例设置 · {version_id}")
        title.setProperty("subheading", True)
        root.addWidget(title)

        self.isolate_cb = QCheckBox("启用版本隔离（独立存档 / Mod / 配置目录）")
        self.isolate_cb.setChecked(self.instance.isolated)
        self.isolate_cb.setToolTip("启用后该版本使用 isolates/<id> 作为游戏目录")
        self.isolate_cb.setCursor(Qt.CursorShape.PointingHandCursor)
        root.addWidget(self.isolate_cb)

        card = Card("运行参数")
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(SPACE["lg"])
        form.setVerticalSpacing(SPACE["lg"])
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.java_combo = QComboBox()
        self.java_combo.addItem("（跟随全局设置）", "")
        for j in find_java():
            self.java_combo.addItem(f"{j.display} — {j.path}", str(j.path))
        saved = self.instance.java_path
        if saved:
            idx = self.java_combo.findData(saved)
            if idx < 0:
                self.java_combo.addItem(saved, saved)
                idx = self.java_combo.count() - 1
            self.java_combo.setCurrentIndex(idx)

        self.max_mem = QSpinBox()
        self.max_mem.setRange(0, 65536)
        self.max_mem.setSingleStep(256)
        self.max_mem.setSuffix(" MB（0 = 跟随全局）")
        self.max_mem.setFixedWidth(230)
        self.max_mem.setValue(self.instance.max_memory_mb)

        self.min_mem = QSpinBox()
        self.min_mem.setRange(0, 8192)
        self.min_mem.setSingleStep(128)
        self.min_mem.setSuffix(" MB（0 = 跟随全局）")
        self.min_mem.setFixedWidth(230)
        self.min_mem.setValue(self.instance.min_memory_mb)

        self.jvm_args = QLineEdit(self.instance.jvm_args)
        self.jvm_args.setPlaceholderText("例如：-Dfml.ignoreInvalidMinecraftCertificates=true")
        self.game_args = QLineEdit(self.instance.game_args)
        self.game_args.setPlaceholderText("例如：--server mc.example.com --port 25565")

        self.width_spin = QSpinBox()
        self.width_spin.setRange(0, 7680)
        self.width_spin.setValue(self.instance.width)
        self.width_spin.setSuffix("  px（0 = 跟随全局）")
        self.width_spin.setFixedWidth(190)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(0, 4320)
        self.height_spin.setValue(self.instance.height)
        self.height_spin.setSuffix("  px（0 = 跟随全局）")
        self.height_spin.setFixedWidth(190)

        res = QHBoxLayout()
        res.addWidget(self.width_spin)
        res.addWidget(QLabel("×"))
        res.addWidget(self.height_spin)
        res.addStretch(1)
        self.fullscreen_cb = QCheckBox("全屏")
        self.fullscreen_cb.setTristate(True)
        if self.instance.fullscreen is None:
            self.fullscreen_cb.setCheckState(Qt.CheckState.PartiallyChecked)
        else:
            self.fullscreen_cb.setChecked(self.instance.fullscreen)
        res.addWidget(self.fullscreen_cb)

        form.addRow("Java：", self.java_combo)
        form.addRow("最大内存：", self.max_mem)
        form.addRow("最小内存：", self.min_mem)
        form.addRow("窗口尺寸：", res)
        form.addRow("额外 JVM 参数：", self.jvm_args)
        form.addRow("额外游戏参数：", self.game_args)
        card.add_layout(form)
        root.addWidget(card)

        info = QLabel(
            "游戏目录：" + str(self.instance.game_directory(self.main.game_dir()))
        )
        info.setProperty("muted", True)
        info.setWordWrap(True)
        self.isolate_cb.stateChanged.connect(
            lambda _: info.setText(
                "游戏目录：" + str(
                    Instance(
                        version_id=self.version_id, isolated=self.isolate_cb.isChecked()
                    ).game_directory(self.main.game_dir())
                )
            )
        )
        root.addWidget(info)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        localize_buttons(buttons, save="保存", cancel="取消")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons, 0, Qt.AlignmentFlag.AlignRight)

    def _save(self) -> None:
        ins = self.instance
        ins.isolated = self.isolate_cb.isChecked()
        ins.java_path = str(self.java_combo.currentData() or "")
        ins.max_memory_mb = self.max_mem.value()
        ins.min_memory_mb = self.min_mem.value()
        ins.jvm_args = self.jvm_args.text().strip()
        ins.game_args = self.game_args.text().strip()
        ins.width = self.width_spin.value()
        ins.height = self.height_spin.value()
        state = self.fullscreen_cb.checkState()
        ins.fullscreen = None if state == Qt.CheckState.PartiallyChecked else (state == Qt.CheckState.Checked)
        self.config.put_instance(ins)
        self.accept()
