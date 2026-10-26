"""首页：账户 / 版本选择 + 一键启动 + 实例概览。"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.java import describe_java_problem, find_java, resolve_java
from ...core.launcher_core import (
    LaunchOptions,
    build_command,
    command_to_string,
    launch,
    resolve_version,
)
from ...core.manifest import scan_local_versions
from ..helpers import reveal_path
from ..theme import FONT_SIZE, METRICS, MONO_FAMILY, SPACE
from ..widgets import Badge, Card, HeroCard, LoadingButton, LogWindow, caption, localize_buttons
from .base import BasePage

LOGGER = logging.getLogger("pylauncher")


class HomePage(BasePage):
    # 游戏进程的日志/退出回调运行在读线程里，Qt 控件只能在主线程操作，
    # 因此统一用信号转发（跨线程 emit 是安全的，槽会在主线程执行）。
    gameLog = Signal(str)
    gameExit = Signal(int)

    def __init__(self, main) -> None:
        super().__init__(main)
        self._launching = False
        self._workers: list = []
        self._build_ui()
        self.refresh()

    # ---------------------------------------------------------------- 界面

    def _build_ui(self) -> None:
        root = self.page_layout(self)
        root.setSpacing(SPACE["xl"])

        header, self.header_sub = self.make_header(
            "启动游戏", "选择版本与账户，一键进入《我的世界》"
        )
        root.addWidget(header)

        # ---- 快速启动
        hero = HeroCard("快速启动", subtitle="配置会随所选版本自动保存")
        form = QGridLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(SPACE["lg"])
        form.setVerticalSpacing(SPACE["xs"])
        form.setColumnStretch(0, 3)
        form.setColumnStretch(1, 2)

        form.addWidget(caption("游戏版本"), 0, 0)
        self.version_combo = QComboBox()
        self.version_combo.setMinimumHeight(METRICS["control_height"])
        self.version_combo.setMinimumWidth(300)
        self.version_combo.currentIndexChanged.connect(self._update_info)
        form.addWidget(self.version_combo, 1, 0)

        form.addWidget(caption("登录账户"), 0, 1)
        self.account_combo = QComboBox()
        self.account_combo.setMinimumHeight(METRICS["control_height"])
        self.account_combo.currentIndexChanged.connect(self._update_info)
        form.addWidget(self.account_combo, 1, 1)
        hero.add_layout(form)

        self.launch_btn = LoadingButton("启动游戏")
        self.launch_btn.setProperty("primary", True)
        self.launch_btn.setMinimumHeight(METRICS["primary_button_height"])
        self.launch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.launch_btn.clicked.connect(self.on_launch)
        hero.add(self.launch_btn)

        self.hero_hint = QLabel("")
        self.hero_hint.setProperty("muted", True)
        self.hero_hint.setWordWrap(True)
        hero.add(self.hero_hint)

        self.empty_row = QWidget()
        empty_layout = QHBoxLayout(self.empty_row)
        empty_layout.setContentsMargins(0, 0, 0, 0)
        empty_layout.setSpacing(SPACE["md"])
        self.empty_label = QLabel("还没有可启动的版本，先去安装一个吧。")
        self.empty_label.setProperty("muted", True)
        empty_layout.addWidget(self.empty_label)
        self.go_versions_btn = QPushButton("去下载版本")
        self.go_versions_btn.setProperty("accent", True)
        self.go_versions_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.go_versions_btn.clicked.connect(self._goto_hint_target)
        empty_layout.addWidget(self.go_versions_btn)
        empty_layout.addStretch(1)
        hero.add(self.empty_row)
        root.addWidget(hero)

        # ---- 实例概览
        info_card = Card("实例概览", subtitle="当前版本实际生效的运行参数")
        self.env_badge = Badge("", "neutral")
        self.env_badge.setVisible(False)
        info_card.add_header_widget(self.env_badge)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(SPACE["xl"])
        grid.setVerticalSpacing(SPACE["lg"])
        for column in range(3):
            grid.setColumnStretch(column, 1)

        self.labels: dict[str, QLabel] = {}
        items = [
            ("运行环境", 0, 0, 1),
            ("内存分配", 0, 1, 1),
            ("窗口尺寸", 0, 2, 1),
            ("版本信息", 1, 0, 1),
            ("上次游玩", 1, 1, 1),
            ("实例目录", 2, 0, 3),
        ]
        for key, row, column, span in items:
            block = QWidget()
            block_layout = QVBoxLayout(block)
            block_layout.setContentsMargins(0, 0, 0, 0)
            block_layout.setSpacing(2)

            cap = QLabel(key)
            cap.setProperty("micro", True)
            block_layout.addWidget(cap)

            value = QLabel("-")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setWordWrap(True)
            if key == "实例目录":
                value.setStyleSheet(
                    f"font-family: {MONO_FAMILY}; font-size: {FONT_SIZE['micro']}px;"
                )
            block_layout.addWidget(value)
            block_layout.addStretch(1)
            self.labels[key] = value
            grid.addWidget(block, row, column, 1, span)
        info_card.add_layout(grid)

        info_card.add_divider()
        actions = QHBoxLayout()
        actions.setSpacing(SPACE["md"])
        self.open_game_btn = QPushButton("打开实例目录")
        self.open_game_btn.clicked.connect(lambda: reveal_path(self._instance_dir()))
        self.open_mods_btn = QPushButton("打开 Mod 目录")
        self.open_mods_btn.clicked.connect(lambda: reveal_path(self._mods_dir()))
        self.preview_btn = QPushButton("查看启动命令")
        self.preview_btn.clicked.connect(self.on_preview)
        for button in (self.open_game_btn, self.open_mods_btn, self.preview_btn):
            button.setProperty("quiet", True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            actions.addWidget(button)
        actions.addStretch(1)
        info_card.add_layout(actions)
        root.addWidget(info_card)

        root.addStretch(1)

    # ---------------------------------------------------------------- 刷新

    def refresh(self) -> None:
        # 账户
        self.account_combo.blockSignals(True)
        self.account_combo.clear()
        accounts = self.accounts_db.accounts
        for acc in accounts:
            self.account_combo.addItem(acc.display, acc.username)
        if accounts:
            idx = next((i for i, a in enumerate(accounts) if a.selected), 0)
            self.account_combo.setCurrentIndex(idx)
        self.account_combo.blockSignals(False)

        # 版本
        self.version_combo.blockSignals(True)
        self.version_combo.clear()
        versions = scan_local_versions(self.game_dir())
        playable = [v for v in versions if v.playable]
        for v in playable:
            self.version_combo.addItem(f"{v.id}   [{v.loader}]", v.id)
        last = str(self.config.get("ui.last_version", "") or "")
        if last:
            idx = self.version_combo.findData(last)
            if idx >= 0:
                self.version_combo.setCurrentIndex(idx)
        self.version_combo.blockSignals(False)

        total = len(playable)
        ready = total > 0 and bool(accounts)
        self._sync_launch_button()

        self.empty_row.setVisible(not ready)
        if total == 0:
            self.empty_label.setText("还没有可启动的版本，先去安装一个吧。")
            self.go_versions_btn.setText("去下载版本")
            self.go_versions_btn.setVisible(True)
        elif not accounts:
            self.empty_label.setText("还没有账户，离线账户 3 秒即可创建。")
            self.go_versions_btn.setText("去添加账户")
            self.go_versions_btn.setVisible(True)
        else:
            self.go_versions_btn.setVisible(False)

        self.header_sub.setText(
            f"{total} 个可启动版本 · "
            + (f"当前账户 {accounts[0].username}" if accounts else "尚未添加账户")
        )
        self._update_info()

    def _sync_launch_button(self) -> None:
        """按钮文案与可用状态跟随「版本 / 账户」数量，避免点了没反应。"""
        total = self.version_combo.count()
        has_account = bool(self.accounts_db.accounts)
        if total == 0:
            text = "暂无可启动版本"
        elif not has_account:
            text = "请先添加账户"
        else:
            text = "启动游戏"
        if self._launching:
            return
        self.launch_btn.set_busy(False, text)
        self.launch_btn.setEnabled(total > 0 and has_account)

    def _goto_hint_target(self) -> None:
        self.main.navigate("versions" if self.version_combo.count() == 0 else "accounts")

    # ---------------------------------------------------------------- 辅助

    def _current_version_id(self) -> str:
        return str(self.version_combo.currentData() or "")

    def _current_account(self):
        username = str(self.account_combo.currentData() or "")
        return self.accounts_db.find(username)

    def _instance(self):
        vid = self._current_version_id()
        if not vid:
            return None
        return self.config.get_instance(vid, create=True)

    def _instance_dir(self) -> Path:
        ins = self._instance()
        return ins.game_directory(self.game_dir()) if ins else self.game_dir()

    def _mods_dir(self) -> Path:
        ins = self._instance()
        return ins.mods_directory(self.game_dir()) if ins else (self.game_dir() / "mods")

    def _update_info(self) -> None:
        vid = self._current_version_id()
        ins = self._instance()
        if not vid or ins is None:
            for label in self.labels.values():
                label.setText("-")
            self.hero_hint.setText("尚未选择版本。")
            self.env_badge.setVisible(False)
            return

        cfg = self.config
        java_text = "未找到 Java（请到设置页指定）"
        loader_text = "-"
        try:
            resolved = resolve_version(self.game_dir(), vid, ins)
            loader_text = resolved.version_id
            self.labels["版本信息"].setText(resolved.version_id)
            self.labels["版本信息"].setToolTip(f"主类：{resolved.main_class}")
            java = resolve_java(
                ins.java_path or str(cfg.get("java.selected", "") or ""),
                resolved.java_required,
            )
            java_text = java.display if java else "未找到 Java（请到设置页指定）"
            self.labels["运行环境"].setText(java_text)
        except Exception as exc:  # noqa: BLE001
            self.labels["版本信息"].setText(f"解析失败：{exc}")
            self.labels["运行环境"].setText("-")

        max_mb = ins.max_memory_mb or int(cfg.get("memory.max_mb", 4096))
        min_mb = ins.min_memory_mb or int(cfg.get("memory.min_mb", 512))
        self.labels["内存分配"].setText(f"{min_mb} – {max_mb} MB")

        w = ins.width or int(cfg.get("window.width", 0) or 0)
        h = ins.height or int(cfg.get("window.height", 0) or 0)
        fs = bool(ins.fullscreen) if ins.fullscreen is not None else bool(cfg.get("window.fullscreen", False))
        self.labels["窗口尺寸"].setText("全屏" if fs else (f"{w} × {h}" if w and h else "默认"))

        self.labels["实例目录"].setText(str(self._instance_dir()))
        self.labels["上次游玩"].setText(ins.last_played or "从未")

        self.hero_hint.setText(
            f"{java_text.split('（')[0]} · 内存 {max_mb} MB"
            f" · {'版本隔离' if ins.isolated else '共用游戏目录'}"
            f" · 实例 {loader_text}"
        )
        self.env_badge.set_state("Java 已就绪" if "未找到" not in java_text else "缺少 Java",
                                 "success" if "未找到" not in java_text else "danger")
        self.env_badge.setVisible(True)

    # ---------------------------------------------------------------- 动作

    def on_preview(self) -> None:
        """展示最终命令行，便于排查问题。"""
        vid = self._current_version_id()
        if not vid:
            self.warn("请先选择一个版本")
            return
        ins = self._instance()
        account = self._current_account()
        options = LaunchOptions.from_instance(ins, self.config, account)
        try:
            resolved = resolve_version(self.game_dir(), vid, ins)
            java = resolve_java(options.java_path, resolved.java_required)
            options.java_path = str(java.path) if java else options.java_path
            cmd, resolved, java_info = build_command(
                self.game_dir(), options, resolved, java, ins
            )
            text = command_to_string(cmd)
        except Exception as exc:  # noqa: BLE001
            self.error(f"无法构建启动命令：\n{exc}")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("启动命令预览")
        dialog.resize(920, 460)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        layout.setSpacing(SPACE["md"])
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText(text)
        view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        view.setStyleSheet(f"font-family: {MONO_FAMILY}; font-size: {FONT_SIZE['caption']}px;")
        layout.addWidget(view, 1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        localize_buttons(box)
        box.rejected.connect(dialog.reject)
        layout.addWidget(box)
        dialog.exec()

    def on_launch(self) -> None:
        vid = self._current_version_id()
        account = self._current_account()
        if not vid:
            self.warn("请先安装并选择一个游戏版本")
            return
        if account is None:
            self.warn("请先添加并选择一个账户")
            return

        ins = self.config.get_instance(vid, create=True)
        game_dir = self.game_dir()

        self._launching = True
        self.launch_btn.set_busy(True, "正在准备…")
        self.main.set_busy(True, "正在准备启动环境…")
        try:
            self._do_launch(vid, account, ins, game_dir)
        finally:
            self._launching = False
            self.main.set_busy(False)
            self._sync_launch_button()

    def _do_launch(self, vid, account, ins, game_dir) -> None:  # noqa: C901 - 启动前检查
        try:
            resolved = resolve_version(game_dir, vid, ins)
        except Exception as exc:  # noqa: BLE001
            self.error(f"版本解析失败：\n{exc}")
            return

        if not resolved.jar.is_file():
            self.warn(
                f"缺少客户端 JAR：\n{resolved.jar}\n\n请到「版本」页面重新下载或校验该版本。"
            )
            return

        options = LaunchOptions.from_instance(ins, self.config, account)
        javas = find_java()
        java = resolve_java(options.java_path, resolved.java_required, javas)
        if java is None:
            LOGGER.error("未检测到 Java，无法启动 %s", vid)
            self.error("未找到可用的 Java 运行环境。\n请到「设置」页面检测或手动指定 java.exe。")
            return

        # 版本要求高于本机可用版本时先讲清楚，避免「点了启动什么都没发生」
        problem = describe_java_problem(javas, resolved.java_required)
        if problem:
            LOGGER.warning("启动 %s 前发现 Java 版本问题：%s", vid, problem)
            detail = (
                f"{problem}\n\n"
                f"版本：{vid}\n"
                f"需要：Java {resolved.java_required} 或更高\n"
                f"当前：{java.display}（{java.path}）\n\n"
                "仍要尝试启动吗？（大概率会立刻退出）"
            )
            if not self.ask(detail, title="Java 版本不匹配"):
                self.warn("已取消启动。请安装符合要求的 Java 后重试。")
                return
        options.java_path = str(java.path)

        missing = [p for p in resolved.classpath if not p.is_file()]
        if missing:
            if not self.ask(
                f"检测到 {len(missing)} 个库文件缺失，游戏可能无法正常启动。\n仍要继续吗？"
            ):
                return

        # 日志窗口
        log_window = None
        if bool(self.config.get("launch.show_log_window", True)):
            log_window = LogWindow(f"游戏日志 — {vid}", self)
            log_window.kill_btn.clicked.connect(lambda: self.main.kill_game())
            log_window.show()

        # 回调在读线程里触发，这里只 emit，真正的界面更新交给主线程槽函数
        if log_window is not None:
            self.gameLog.connect(log_window.append)
        self.gameExit.connect(lambda code: self._on_game_exit(code, log_window))

        def on_log(text: str) -> None:
            self.gameLog.emit(text)

        def on_exit(code: int) -> None:
            self.gameExit.emit(code)

        try:
            proc, cmd, resolved, java_info = launch(
                game_dir, options, ins, on_log, on_exit
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("启动游戏失败")
            self.error(f"启动失败：\n{exc}")
            self._detach_game_signals(log_window)
            if log_window:
                log_window.close()
            return

        self.main.register_process(proc, vid)
        ins.last_played = time.strftime("%Y-%m-%d %H:%M")
        self.config.put_instance(ins)
        self.config.set("ui.last_version", vid)
        self.config.set("ui.last_account", account.username)

        self.main.set_status(f"正在运行 {vid}（PID {proc.pid}，Java {java_info.major}）")
        self.main.set_busy(True, f"正在运行 {vid}")
        if not bool(self.config.get("launch.launcher_visible", True)):
            self.main.hide()

    # ---------------------------------------------------------------- 退出处理

    def _detach_game_signals(self, log_window) -> None:
        """断开本次启动的信号连接，避免 lambda 闭包与日志窗口堆积泄漏。"""
        if log_window is not None:
            try:
                self.gameLog.disconnect(log_window.append)
            except (RuntimeError, TypeError):
                pass
        try:
            self.gameExit.disconnect()
        except (RuntimeError, TypeError):
            pass

    def _on_game_exit(self, code: int, log_window) -> None:
        """主线程槽：处理游戏退出（这里才是唯一允许碰界面的地方）。"""
        self._detach_game_signals(log_window)
        if log_window is not None:
            log_window.set_status(f"已退出，代码 {code}")
            log_window.append(f"\n===== 进程结束，退出码 {code} =====")
        self.main.on_game_exit(code)
