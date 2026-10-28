"""服务器与联机：服务器列表 / 状态查询 / 局域网发现 / 跨网联机。"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core import netplay, servers as SVC
from ..theme import METRICS, SPACE
from ..widgets import Badge, BusySpinner, Card, EmptyState, caption, localize_buttons
from ..workers import Worker, start_worker
from .base import BasePage


class ServerPage(BasePage):
    """服务器列表 + 局域网联机 + 跨网联机。"""

    # 后台线程只能通过信号回主线程
    lanFound = Signal(object)

    def __init__(self, main) -> None:
        super().__init__(main)
        self._servers: list[SVC.ServerInfo] = []
        self._status: dict[str, SVC.ServerStatus] = {}
        self._scanner: netplay.LanScanner | None = None
        self._relay: netplay.RelayServer | None = None
        self._build_ui()
        self.lanFound.connect(self._on_lan_found)
        self.refresh()

    # ---------------------------------------------------------------- 界面

    def _build_ui(self) -> None:
        root = self.page_layout(self)
        header, self.header_sub = self.make_header(
            "服务器与联机", "管理服务器、发现局域网房间，或跨网与好友进入同一世界"
        )
        root.addWidget(header)

        root.addWidget(self._build_server_card())
        root.addWidget(self._build_lan_card())
        root.addWidget(self._build_remote_card())

    # ---------------- 服务器列表

    def _build_server_card(self) -> Card:
        card = Card("服务器列表", subtitle="双击行即可直接进入；列表保存在启动器配置里")

        bar = QHBoxLayout()
        bar.setSpacing(SPACE["md"])
        self.address_edit = QLineEdit()
        self.address_edit.setPlaceholderText("服务器地址，如 mc.example.com:25565")
        self.address_edit.setMinimumHeight(METRICS["control_height"])
        bar.addWidget(self.address_edit, 1)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("备注名（可留空）")
        self.name_edit.setMinimumHeight(METRICS["control_height"])
        self.name_edit.setFixedWidth(160)
        bar.addWidget(self.name_edit)
        self.add_btn = QPushButton("添加")
        self.add_btn.setProperty("accent", True)
        self.add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_btn.clicked.connect(self._add_server)
        self.ping_btn = QPushButton("刷新状态")
        self.ping_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ping_btn.clicked.connect(self._refresh_status)
        for button in (self.add_btn, self.ping_btn):
            bar.addWidget(button)
        card.add_layout(bar)

        self.stack = QStackedWidget()
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["服务器", "地址", "状态", "人数", "操作"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(METRICS["row_height"])
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        head.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        head.setFixedHeight(METRICS["header_height"])
        for col in (1, 2, 3, 4):
            head.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.doubleClicked.connect(lambda _: self._join_selected())
        self.stack.addWidget(self.table)

        self.empty = EmptyState(
            "还没有添加服务器",
            "在上方输入地址后点「添加」；也可以直接扫描局域网里已经开放的世界。",
        )
        self.stack.addWidget(self.empty)
        card.add(self.stack, 1)

        self.server_summary = QLabel("")
        self.server_summary.setProperty("micro", True)
        self.server_summary.setWordWrap(True)
        card.add(self.server_summary)
        return card

    # ---------------- 局域网

    def _build_lan_card(self) -> Card:
        card = Card("局域网联机", subtitle="自动发现同一网络下「开放到局域网」的世界")

        bar = QHBoxLayout()
        bar.setSpacing(SPACE["md"])
        self.lan_spinner = BusySpinner(14)
        bar.addWidget(self.lan_spinner)
        self.lan_hint = QLabel("未扫描")
        self.lan_hint.setProperty("micro", True)
        bar.addWidget(self.lan_hint, 1)
        self.scan_btn = QPushButton("开始扫描")
        self.scan_btn.setProperty("accent", True)
        self.scan_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.scan_btn.clicked.connect(self._toggle_scan)
        bar.addWidget(self.scan_btn)
        card.add_layout(bar)

        self.lan_table = QTableWidget(0, 3)
        self.lan_table.setHorizontalHeaderLabels(["世界", "地址", "操作"])
        self.lan_table.verticalHeader().setVisible(False)
        self.lan_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.lan_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.lan_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.lan_table.setAlternatingRowColors(True)
        self.lan_table.verticalHeader().setDefaultSectionSize(METRICS["row_height"])
        head = self.lan_table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        head.setFixedHeight(METRICS["header_height"])
        for col in (1, 2):
            head.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        card.add(self.lan_table)

        hint = QLabel(
            "提示：需要房主先在游戏中「开放到局域网」。启动器按 Mojang 的组播协议"
            "（224.0.2.60:4445）发现房间，进入时会带上 --server/--port 参数。"
        )
        hint.setProperty("micro", True)
        hint.setWordWrap(True)
        card.add(hint)
        return card

    # ---------------- 跨网

    def _build_remote_card(self) -> Card:
        card = Card("跨网联机", subtitle="没有公网 IP 时用「内置中继 + 联机码」与好友联机")

        form = QHBoxLayout()
        form.setSpacing(SPACE["md"])
        self.relay_host_edit = QLineEdit()
        self.relay_host_edit.setPlaceholderText("本机游戏端口，如 127.0.0.1:25565")
        self.relay_host_edit.setMinimumHeight(METRICS["control_height"])
        form.addWidget(caption("游戏地址"), 0)
        form.addWidget(self.relay_host_edit, 1)
        self.relay_port_edit = QLineEdit(str(netplay.RELAY_DEFAULT_PORT))
        self.relay_port_edit.setFixedWidth(120)
        self.relay_port_edit.setMinimumHeight(METRICS["control_height"])
        form.addWidget(caption("中继端口"), 0)
        form.addWidget(self.relay_port_edit, 0)
        card.add_layout(form)

        row = QHBoxLayout()
        row.setSpacing(SPACE["md"])
        self.relay_btn = QPushButton("启动中继")
        self.relay_btn.setProperty("primary", True)
        self.relay_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.relay_btn.clicked.connect(self._toggle_relay)
        self.copy_code_btn = QPushButton("复制联机码")
        self.copy_code_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_code_btn.clicked.connect(self._copy_code)
        for button in (self.relay_btn, self.copy_code_btn):
            button.setProperty("inTableCell", True)
            row.addWidget(button)
        row.addStretch(1)
        self.relay_badge = Badge("未启动", "neutral")
        row.addWidget(self.relay_badge)
        card.add_layout(row)

        self.code_label = QLabel("")
        self.code_label.setProperty("mono", True)
        self.code_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.code_label.setWordWrap(True)
        card.add(self.code_label)

        join_row = QHBoxLayout()
        join_row.setSpacing(SPACE["md"])
        self.join_edit = QLineEdit()
        self.join_edit.setPlaceholderText("粘贴好友给你的联机码…")
        self.join_edit.setMinimumHeight(METRICS["control_height"])
        join_row.addWidget(self.join_edit, 1)
        self.join_btn = QPushButton("解析并联接")
        self.join_btn.setProperty("accent", True)
        self.join_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.join_btn.clicked.connect(self._use_code)
        join_row.addWidget(self.join_btn)
        card.add_layout(join_row)

        hint = QLabel(
            "中继只转发 TCP 字节流，连接需携带联机码里的口令；房主需保证中继端口"
            "在路由器上做了端口映射，否则外网仍无法访问。"
        )
        hint.setProperty("micro", True)
        hint.setWordWrap(True)
        card.add(hint)
        return card

    # ---------------------------------------------------------------- 数据

    def refresh(self) -> None:
        self._servers = SVC.load_servers(self.config)
        self._reload_table()
        online = sum(1 for s in self._status.values() if s.online)
        self.header_sub.setText(
            f"{len(self._servers)} 个服务器"
            + (f" · {online} 个在线" if self._status else " · 未查询状态")
        )

    def _reload_table(self) -> None:
        self.table.setRowCount(0)
        for info in self._servers:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(info.name))
            self.table.setItem(row, 1, QTableWidgetItem(info.host_port))

            status = self._status.get(info.host_port)
            if status is None:
                state_item = QTableWidgetItem("未查询")
                players = "-"
            elif status.online:
                state_item = QTableWidgetItem(f"{status.latency_ms} ms · {status.version}")
                state_item.setToolTip(status.motd or "")
                players = status.players_text
            else:
                state_item = QTableWidgetItem("离线")
                state_item.setToolTip(status.error)
                players = "-"
            self.table.setItem(row, 2, state_item)
            self.table.setItem(row, 3, QTableWidgetItem(players))

            actions = QWidget()
            actions.setObjectName("CellHost")
            al = QHBoxLayout(actions)
            al.setContentsMargins(2, 0, 2, 0)
            al.setSpacing(SPACE["xs"])
            join_btn = QPushButton("进入")
            join_btn.setProperty("inTableCell", True)
            join_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            join_btn.clicked.connect(lambda _=False, s=info: self._join(s))
            del_btn = QPushButton("删除")
            del_btn.setProperty("inTableCell", True)
            del_btn.setProperty("danger", True)
            del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            del_btn.clicked.connect(lambda _=False, s=info: self._remove(s))
            for button in (join_btn, del_btn):
                al.addWidget(button)
            self.table.setCellWidget(row, 4, actions)

        self.stack.setCurrentIndex(0 if self._servers else 1)
        self.server_summary.setText(
            f"共 {len(self._servers)} 个服务器 · 双击行直接进入"
            if self._servers else ""
        )

    # ---------------------------------------------------------------- 服务器动作

    def _add_server(self) -> None:
        text = self.address_edit.text().strip()
        if not text:
            self.warn("请输入服务器地址")
            return
        host, port = SVC.split_address(text)
        info = SVC.ServerInfo(
            name=self.name_edit.text().strip() or host,
            address=host,
            port=port,
        )
        self._servers = SVC.add_server(self.config, info)
        self.address_edit.clear()
        self.name_edit.clear()
        self.refresh()
        self._refresh_status()

    def _remove(self, info: SVC.ServerInfo) -> None:
        if not self.ask(f"确定删除服务器 {info.name}（{info.host_port}）吗？"):
            return
        self._servers = SVC.remove_server(self.config, info.host_port)
        self._status.pop(info.host_port, None)
        self.refresh()

    def _refresh_status(self) -> None:
        if not self._servers:
            return
        self.ping_btn.setEnabled(False)
        self.main.set_busy(True, "正在查询服务器状态…")
        targets = [(s.address, s.port) for s in self._servers]

        def work(on_progress, on_message, should_cancel=None):
            result = {}
            for index, (host, port) in enumerate(targets):
                if should_cancel and should_cancel():
                    break
                result[f"{host}:{port}"] = SVC.ping(host, port, timeout=4)
                on_progress(index + 1, len(targets))
            return result

        def on_done(result):
            self.ping_btn.setEnabled(True)
            self.main.set_busy(False)
            self._status.update(result)
            self.refresh()
            self.flash(f"已查询 {len(result)} 个服务器")

        def on_error(message):
            self.ping_btn.setEnabled(True)
            self.main.set_busy(False)
            self.warn(f"查询失败：{message.splitlines()[0]}")

        worker = Worker(work)
        worker.progress.connect(lambda done, total: self.main.set_busy(True, f"查询中 {done}/{total}"))
        worker.succeeded.connect(on_done)
        worker.failed.connect(on_error)
        start_worker(self, worker)

    def _selected(self) -> SVC.ServerInfo | None:
        row = self.table.currentRow()
        if 0 <= row < len(self._servers):
            return self._servers[row]
        return None

    def _join_selected(self) -> None:
        info = self._selected()
        if info:
            self._join(info)

    def _join(self, info: SVC.ServerInfo) -> None:
        home = getattr(self.main.pages, "get", None)
        page = self.main.pages.get("home") if isinstance(self.main.pages, dict) else None
        if page is None or not hasattr(page, "launch_into_server"):
            self.warn("无法进入服务器：启动页不可用")
            return
        SVC.touch_server(self.config, info.host_port)
        page.launch_into_server(info.address, info.port)

    # ---------------------------------------------------------------- 局域网

    def _toggle_scan(self) -> None:
        if self._scanner is not None:
            self._scanner.stop()
            self._scanner = None
            self.lan_spinner.stop()
            self.scan_btn.setText("开始扫描")
            self.lan_hint.setText("已停止扫描")
            return

        self._scanner = netplay.LanScanner(timeout=1.0)
        self._scanner.start(on_found=lambda world: self.lanFound.emit(world))
        self.lan_spinner.start()
        self.scan_btn.setText("停止扫描")
        self.lan_hint.setText(f"正在监听 {netplay.LAN_MULTICAST_GROUP}:{netplay.LAN_MULTICAST_PORT}…")
        self.lan_table.setRowCount(0)

        self._scan_timer = QTimer(self)
        self._scan_timer.timeout.connect(self._poll_lan)
        self._scan_timer.start(2000)

    def _poll_lan(self) -> None:
        if self._scanner is None:
            return
        self._scanner.prune(max_age=45)
        self._render_lan(self._scanner.worlds)

    def _on_lan_found(self, world) -> None:
        """主线程槽：更新局域网房间列表。"""
        self._render_lan(self._scanner.worlds if self._scanner else [world])

    def _render_lan(self, worlds) -> None:
        self.lan_table.setRowCount(0)
        for world in worlds:
            row = self.lan_table.rowCount()
            self.lan_table.insertRow(row)
            self.lan_table.setItem(row, 0, QTableWidgetItem(world.motd))
            self.lan_table.setItem(row, 1, QTableWidgetItem(world.host_port))

            actions = QWidget()
            actions.setObjectName("CellHost")
            al = QHBoxLayout(actions)
            al.setContentsMargins(2, 0, 2, 0)
            join_btn = QPushButton("进入")
            join_btn.setProperty("inTableCell", True)
            join_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            join_btn.clicked.connect(
                lambda _=False, w=world: self._join_world(w.address, w.port)
            )
            al.addWidget(join_btn)
            self.lan_table.setCellWidget(row, 2, actions)
        if worlds:
            self.lan_hint.setText(f"发现 {len(worlds)} 个局域网世界")

    def _join_world(self, host: str, port: int) -> None:
        page = self.main.pages.get("home") if isinstance(self.main.pages, dict) else None
        if page is None or not hasattr(page, "launch_into_server"):
            self.warn("无法进入：启动页不可用")
            return
        page.launch_into_server(host, port)

    # ---------------------------------------------------------------- 跨网

    def _toggle_relay(self) -> None:
        if self._relay is not None and self._relay.running:
            self._relay.stop()
            self._relay = None
            self.relay_badge.set_state("未启动", "neutral")
            self.relay_btn.setText("启动中继")
            self.code_label.setText("")
            return

        host_text = self.relay_host_edit.text().strip() or f"127.0.0.1:{SVC.DEFAULT_PORT}"
        target_host, target_port = SVC.split_address(host_text)
        try:
            listen_port = int(self.relay_port_edit.text().strip() or netplay.RELAY_DEFAULT_PORT)
        except ValueError:
            self.warn("中继端口必须是数字")
            return

        import secrets

        token = secrets.token_hex(4)
        relay = netplay.RelayServer(listen_port, target_host, target_port, token)
        if not relay.start():
            self.error(f"中继启动失败：{relay.error}\n\n可能是端口被占用，换一个端口试试。")
            return
        self._relay = relay
        public_host = netplay.local_ip()
        code = netplay.make_join_code(token, public_host, listen_port)
        self.code_label.setText(
            f"联机码：{code}\n"
            f"好友需在路由器上把 {listen_port} 端口映射到你本机（{public_host}）；"
            f"游戏端口为 {target_host}:{target_port}"
        )
        self.relay_badge.set_state(f"已启动 · {listen_port}", "success")
        self.relay_btn.setText("停止中继")
        self.flash("中继已启动，复制联机码发给好友")

    def _copy_code(self) -> None:
        text = self.code_label.text()
        if not text:
            self.warn("请先启动中继以生成联机码")
            return
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(text.splitlines()[0].replace("联机码：", ""))
        self.flash("联机码已复制到剪贴板")

    def _use_code(self) -> None:
        try:
            token, host, port = netplay.parse_join_code(self.join_edit.text())
        except ValueError as exc:
            self.warn(str(exc))
            return
        # 口令用于中继鉴权；直连时把它一并展示，便于核对
        self._servers = SVC.add_server(
            self.config,
            SVC.ServerInfo(name=f"联机码房间 {host}", address=host, port=port,
                           note=f"token={token}"),
        )
        self.refresh()
        self._refresh_status()
        self.flash(f"已添加 {host}:{port}，点「进入」即可以该口令接入")


__all__ = ["ServerPage"]
