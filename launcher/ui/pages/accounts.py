"""账户页：离线账户与微软正版账户的增删管理。"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QCheckBox,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ...core.auth import (
    Account,
    MicrosoftAuthError,
    MicrosoftAuthenticator,
    create_offline_account,
)
from ..icons import dot_icon
from ..theme import COLORS, FONT_SIZE, METRICS, SPACE
from ..widgets import (
    Badge,
    BusySpinner,
    Card,
    EmptyState,
    ProgressDialog,
    caption,
    localize_buttons,
)
from ..workers import Worker, start_worker, wait_worker
from .base import BasePage


class AccountPage(BasePage):
    def __init__(self, main) -> None:
        super().__init__(main)
        self._build_ui()
        self.refresh()

    # ---------------------------------------------------------------- 界面

    def _build_ui(self) -> None:
        root = self.page_layout(self)
        header, self.header_sub = self.make_header(
            "账户管理", "离线账户开箱即用；正版账户需先配置 Azure 应用 ID"
        )
        root.addWidget(header)

        # ---- 账户列表
        list_card = Card("已保存的账户", subtitle="默认账户会在启动游戏时自动选中")
        self.list_stack = QStackedWidget()
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setSpacing(2)
        self.list.itemSelectionChanged.connect(self._on_selection_changed)
        self.list_stack.addWidget(self.list)

        self.list_empty = EmptyState(
            "还没有添加任何账户",
            "在下方输入昵称即可创建离线账户，或使用微软正版账号登录。",
        )
        self.list_stack.addWidget(self.list_empty)
        list_card.add(self.list_stack, 1)

        list_card.add_divider()
        bar = QHBoxLayout()
        bar.setSpacing(SPACE["md"])
        self.set_default_btn = QPushButton("设为默认")
        self.set_default_btn.clicked.connect(self._set_default)
        self.refresh_token_btn = QPushButton("刷新令牌")
        self.refresh_token_btn.clicked.connect(self._refresh_token)
        self.remove_btn = QPushButton("删除账户")
        self.remove_btn.setProperty("danger", True)
        self.remove_btn.clicked.connect(self._remove)
        for button in (self.set_default_btn, self.refresh_token_btn, self.remove_btn):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        bar.addWidget(self.set_default_btn)
        bar.addWidget(self.refresh_token_btn)
        bar.addStretch(1)
        bar.addWidget(self.remove_btn)
        list_card.add_layout(bar)
        root.addWidget(list_card, 1)

        # ---- 添加离线
        offline_card = Card("添加离线账户", subtitle="UUID 按 Mojang 官方规则生成，可进入允许离线的服务器")
        row = QHBoxLayout()
        row.setSpacing(SPACE["md"])
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("3–16 位，仅限字母 / 数字 / 下划线")
        self.name_edit.setMinimumHeight(METRICS["control_height"])
        self.name_edit.returnPressed.connect(self._add_offline)
        self.add_offline_btn = QPushButton("添加")
        self.add_offline_btn.setProperty("accent", True)
        self.add_offline_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_offline_btn.clicked.connect(self._add_offline)
        row.addWidget(self.name_edit, 1)
        row.addWidget(self.add_offline_btn)
        offline_card.add_layout(row)
        root.addWidget(offline_card)

        # ---- 微软
        ms_card = Card("微软正版账户", subtitle="使用设备码登录，全程无需在启动器内输入密码")
        row2 = QHBoxLayout()
        row2.setSpacing(SPACE["md"])
        self.add_ms_btn = QPushButton("使用微软账号登录")
        self.add_ms_btn.setProperty("primary", True)
        self.add_ms_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_ms_btn.clicked.connect(self._add_microsoft)
        row2.addWidget(self.add_ms_btn)
        self.ms_badge = Badge("", "neutral")
        row2.addWidget(self.ms_badge)
        row2.addStretch(1)
        ms_card.add_layout(row2)
        self.ms_hint = QLabel("")
        self.ms_hint.setProperty("muted", True)
        self.ms_hint.setWordWrap(True)
        ms_card.add(self.ms_hint)
        root.addWidget(ms_card)

        # ---- 第三方（外置）登录
        third_card = Card(
            "第三方账号登录（外置登录）",
            subtitle="支持 Yggdrasil 协议的皮肤站：Blessing Skin、统一通行证、LittleSkin 等",
        )
        row3 = QHBoxLayout()
        row3.setSpacing(SPACE["md"])
        self.add_third_btn = QPushButton("外置登录…")
        self.add_third_btn.setProperty("primary", True)
        self.add_third_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_third_btn.clicked.connect(self._add_yggdrasil)
        row3.addWidget(self.add_third_btn)
        self.third_hint = QLabel("")
        self.third_hint.setProperty("micro", True)
        self.third_hint.setWordWrap(True)
        row3.addWidget(self.third_hint, 1)
        third_card.add_layout(row3)
        root.addWidget(third_card)

    # ---------------------------------------------------------------- 刷新

    def refresh(self) -> None:
        self.list.clear()
        accounts = self.accounts_db.accounts
        for acc in accounts:
            item = QListWidgetItem(f"   {acc.username}    [{acc.type_label}]")
            item.setData(Qt.ItemDataRole.UserRole, acc.username)
            item.setSizeHint(QSize(0, 44))
            color = COLORS["primary"] if acc.type == "microsoft" else COLORS["success"]
            item.setIcon(dot_icon(color, 12))
            if acc.selected:
                font = item.font()
                font.setBold(True)
                font.setPixelSize(FONT_SIZE["body_strong"])
                item.setFont(font)
                item.setToolTip("默认账户")
            self.list.addItem(item)

        self.list_stack.setCurrentIndex(0 if accounts else 1)
        if accounts:
            default_index = next((i for i, a in enumerate(accounts) if a.selected), 0)
            self.list.blockSignals(True)
            self.list.setCurrentRow(default_index)
            self.list.blockSignals(False)
        has = bool(accounts)
        self.set_default_btn.setEnabled(has)
        self.remove_btn.setEnabled(has)
        self.header_sub.setText(
            f"共 {len(accounts)} 个账户 · "
            f"默认 {next((a.username for a in accounts if a.selected), '未设置')}"
            if accounts
            else "还没有账户，添加后即可启动游戏"
        )

        client_id = str(self.config.get("microsoft.client_id", "") or "")
        if client_id:
            self.ms_badge.set_state("已配置", "success")
            self.ms_hint.setText(
                "已配置 Azure 应用 client_id，点击左侧按钮使用设备码登录。"
                "（若提示未拥有游戏，请确认该微软账号已购买 Java 版）"
            )
            self.add_ms_btn.setEnabled(True)
        else:
            self.ms_badge.set_state("未配置", "warning")
            self.ms_hint.setText(
                "尚未配置 client_id。请到 Azure 门户注册一个「移动和桌面应用」"
                "（允许公共客户端流），把应用 ID 填到「设置 → 微软登录」后即可使用。"
            )
            self.add_ms_btn.setEnabled(False)

        self._on_selection_changed()

    # ---------------------------------------------------------------- 选择

    def _current(self):
        item = self.list.currentItem()
        if item is None:
            return None
        return self.accounts_db.find(item.data(Qt.ItemDataRole.UserRole))

    def _on_selection_changed(self) -> None:
        acc = self._current()
        self.refresh_token_btn.setEnabled(bool(acc and acc.type == "microsoft"))

    # ---------------------------------------------------------------- 操作

    def _add_offline(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            self.warn("请输入昵称")
            self.name_edit.setFocus()
            return
        try:
            acc = create_offline_account(name)
        except ValueError as exc:
            self.warn(str(exc))
            return
        self.accounts_db.add(acc)
        self.name_edit.clear()
        self.refresh()
        self.main.notify_accounts_changed()
        self.info(f"已添加离线账户：{acc.username}\nUUID：{acc.dashed_uuid}")

    def _set_default(self) -> None:
        acc = self._current()
        if acc is None:
            return
        self.accounts_db.select(acc)
        self.refresh()
        self.main.notify_accounts_changed()
        self.flash(f"已将 {acc.username} 设为默认账户")

    def _remove(self) -> None:
        acc = self._current()
        if acc is None:
            return
        if not self.ask(f"删除账户 {acc.username} 吗？"):
            return
        self.accounts_db.remove(acc)
        self.refresh()
        self.main.notify_accounts_changed()
        self.flash(f"已删除账户 {acc.username}")

    def _refresh_token(self) -> None:
        acc = self._current()
        if acc is None:
            self.warn("请先选择一个账户")
            return
        if acc.type == "yggdrasil":
            self._refresh_yggdrasil(acc)
            return
        if acc.type != "microsoft":
            self.warn("离线账户无需刷新令牌")
            return
        client_id = str(self.config.get("microsoft.client_id", "") or "")
        if not client_id:
            self.warn("未配置 client_id，无法刷新")
            return

        dlg = ProgressDialog("刷新令牌", self, cancelable=False)
        dlg.set_indeterminate()
        dlg.set_stage("正在刷新访问令牌…")

        def work(on_progress, on_message):
            return MicrosoftAuthenticator(client_id).refresh(acc)

        worker = Worker(work)

        def done(new_acc):
            dlg.accept()
            self.accounts_db.add(new_acc)
            self.refresh()
            self.main.notify_accounts_changed()
            self.info("令牌已刷新。")

        worker.succeeded.connect(done)
        worker.failed.connect(dlg.done_with_error)
        start_worker(self, worker)
        dlg.exec()
        wait_worker(worker, 1500)

    # ---------------------------------------------------------------- 外置登录

    def _add_yggdrasil(self) -> None:
        dlg = YggdrasilLoginDialog(self.config, self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.account is not None:
            self.accounts_db.add(dlg.account)
            self.refresh()
            self.main.notify_accounts_changed()
            self.info(f"外置登录成功：{dlg.account.username}")

    def _refresh_yggdrasil(self, acc) -> None:
        dlg = ProgressDialog("刷新外置登录令牌", self, cancelable=False)
        dlg.set_indeterminate()
        dlg.set_stage("正在刷新…")

        def work(on_progress, on_message):
            from ...core.auth import refresh_account

            return refresh_account(acc)

        worker = Worker(work)

        def done(new_acc):
            dlg.accept()
            self.accounts_db.add(new_acc)
            self.refresh()
            self.main.notify_accounts_changed()
            self.info("令牌已刷新。")

        worker.succeeded.connect(done)
        worker.failed.connect(dlg.done_with_error)
        start_worker(self, worker)
        dlg.exec()
        wait_worker(worker, 1500)

    # ---------------------------------------------------------------- 微软登录

    def _add_microsoft(self) -> None:
        client_id = str(self.config.get("microsoft.client_id", "") or "")
        if not client_id:
            self.warn("请先在「设置 → 微软登录」中填写 Azure 应用的 client_id。")
            return
        dlg = MicrosoftLoginDialog(client_id, self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.account is not None:
            self.accounts_db.add(dlg.account)
            self.refresh()
            self.main.notify_accounts_changed()
            self.info(f"登录成功：{dlg.account.username}")


# -------------------------------------------------------------------- 设备码对话框


class YggdrasilLoginDialog(QDialog):
    """第三方（外置）登录：服务器地址 + 账号 + 密码。"""

    def __init__(self, config, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.account = None
        self._worker: Worker | None = None
        self.setWindowTitle("第三方账号登录")
        self.setModal(True)
        self.resize(560, 300)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE["xxl"], SPACE["xl"], SPACE["xxl"], SPACE["lg"])
        layout.setSpacing(SPACE["lg"])

        title = QLabel("外置登录（Yggdrasil）")
        title.setProperty("subheading", True)
        layout.addWidget(title)

        form = QFormLayout()
        form.setHorizontalSpacing(SPACE["lg"])
        form.setVerticalSpacing(SPACE["md"])
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.server_edit = QLineEdit()
        self.server_edit.setPlaceholderText("https://skin.example.com/api/yggdrasil")
        self.server_edit.setText(str(config.get("auth.yggdrasil_url", "") or ""))
        self.server_edit.setMinimumHeight(METRICS["control_height"])

        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("账号或邮箱")
        self.user_edit.setMinimumHeight(METRICS["control_height"])

        self.pass_edit = QLineEdit()
        self.pass_edit.setPlaceholderText("密码")
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.pass_edit.setMinimumHeight(METRICS["control_height"])

        self.save_cb = QCheckBox("记住服务器地址")
        self.save_cb.setChecked(True)
        self.save_cb.setCursor(Qt.CursorShape.PointingHandCursor)

        form.addRow("服务器地址：", self.server_edit)
        form.addRow("账号：", self.user_edit)
        form.addRow("密码：", self.pass_edit)
        form.addRow("", self.save_cb)
        layout.addLayout(form)

        self.hint = QLabel(
            "登录凭据只会发给该服务器，启动器不做任何中转存储；"
            "该域名会被自动加入网络安全白名单。"
        )
        self.hint.setProperty("micro", True)
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        buttons = QDialogButtonBox()
        localize_buttons(buttons, ok="登录", cancel="取消")
        buttons.accepted.connect(self._do_login)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _do_login(self) -> None:
        url = self.server_edit.text().strip()
        user = self.user_edit.text().strip()
        pwd = self.pass_edit.text()
        if not url or not user or not pwd:
            self.hint.setText("服务器地址、账号、密码都不能为空")
            return
        if self.save_cb.isChecked():
            self.config.set("auth.yggdrasil_url", url)

        dlg = ProgressDialog("正在登录", self, cancelable=False)
        dlg.set_indeterminate()
        dlg.set_stage("连接认证服务器…")

        def work(on_progress, on_message):
            from ...core.auth import YggdrasilAuthenticator

            return YggdrasilAuthenticator(url).login(user, pwd)

        worker = Worker(work)

        def done(acc):
            dlg.accept()
            self.account = acc
            self.accept()

        def failed(message):
            dlg.accept()
            self.hint.setText(f"登录失败：{message.splitlines()[0]}")
            self.pass_edit.clear()

        worker.succeeded.connect(done)
        worker.failed.connect(failed)
        start_worker(self, worker)
        dlg.exec()
        wait_worker(worker, 1500)


class MicrosoftLoginDialog(QDialog):
    """微软设备码登录流程：获取码 → 引导用户授权 → 后台轮询。"""

    def __init__(self, client_id: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("微软账号登录")
        self.setModal(True)
        self.resize(560, 360)
        self.client_id = client_id
        self.account: Account | None = None
        self._worker: Worker | None = None
        self._stop = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE["xxl"], SPACE["xl"], SPACE["xxl"], SPACE["lg"])
        layout.setSpacing(SPACE["lg"])

        head = QHBoxLayout()
        head.setSpacing(SPACE["md"])
        self.spinner = BusySpinner(16)
        self.spinner.start()
        head.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignVCenter)
        self.title = QLabel("正在获取设备码…")
        self.title.setProperty("subheading", True)
        head.addWidget(self.title, 1)
        layout.addLayout(head)

        self.code_label = QLabel("")
        self.code_label.setStyleSheet(
            f"font-size: 32px; font-weight: 700; letter-spacing: 6px;"
            f" color: {COLORS['primary']}; padding: 12px;"
            f" background: {COLORS['primary_soft']}; border-radius: 10px;"
        )
        self.code_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.code_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.code_label)

        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        self.browser.setFixedHeight(92)
        self.browser.setHtml(
            f"<div style='font-size:{FONT_SIZE['caption']}px; line-height:1.7;'>"
            "1. 点击下方按钮在浏览器中打开授权页<br>"
            "2. 输入上面的代码并同意授权<br>"
            "3. 本窗口会自动检测完成登录</div>"
        )
        layout.addWidget(self.browser)

        self.open_btn = QPushButton("在浏览器中打开授权页")
        self.open_btn.setEnabled(False)
        self.open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_btn.clicked.connect(self._open_browser)
        layout.addWidget(self.open_btn)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        localize_buttons(box)
        box.rejected.connect(self._cancel)
        layout.addWidget(box, 0, Qt.AlignmentFlag.AlignRight)

        self._begin()

    # ------------------------------------------------------------ 第一步

    def _begin(self) -> None:
        def work(on_progress, on_message):
            on_message("获取设备码")
            return MicrosoftAuthenticator(self.client_id).begin()

        worker = Worker(work, parent=self)
        self._worker = worker

        def on_code(session):
            self._session = session
            self.code_label.setText(session.user_code)
            self.title.setText("请在浏览器中完成授权")
            self.open_btn.setEnabled(True)
            self._url = session.verification_uri
            self._open_browser()
            self._poll()

        worker.succeeded.connect(on_code)
        worker.failed.connect(lambda e: (self._close_safely(self.reject), self._show_error(e)))
        start_worker(self, worker)

    # ------------------------------------------------------------ 轮询

    def _poll(self) -> None:
        def work(on_progress, on_message):
            return MicrosoftAuthenticator(self.client_id).poll(
                self._session,
                stop=lambda: self._stop
                or bool(self._worker and self._worker.isInterruptionRequested()),
            )

        worker = Worker(work, parent=self)
        self._worker = worker
        worker.succeeded.connect(lambda acc: self._close_safely(lambda: self._on_success(acc)))
        worker.failed.connect(lambda e: self._close_safely(lambda: self._on_fail(e)))
        start_worker(self, worker)

    def _on_success(self, account: Account) -> None:
        self.account = account
        self.accept()

    def _on_fail(self, message: str) -> None:
        self._stop = True
        self.reject()
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.critical(self, "登录失败", message)

    def _show_error(self, message: str) -> None:
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.critical(self, "登录失败", message)

    # ------------------------------------------------------------ 杂项

    def _open_browser(self) -> None:
        url = getattr(self, "_url", "")
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _request_stop(self) -> bool:
        """通知后台线程停止；返回是否仍在运行。"""
        self._stop = True
        if self._worker is not None and self._worker.isRunning():
            self._worker.request_cancel()
            return True
        return False

    def _close_safely(self, action, attempts: int = 0) -> None:
        """等后台线程自然退出后再真正关闭对话框。

        QThread 对象在线程仍在运行时被销毁会直接 abort 进程，
        这是「取消登录时崩溃」的根因；因此关闭一律延后到线程结束。
        """
        if self._request_stop() and attempts < 80:
            if attempts == 0:
                self.hide()
            QTimer.singleShot(120, lambda: self._close_safely(action, attempts + 1))
            return
        action()

    def _cancel(self) -> None:
        self._close_safely(self.reject)
