"""设置页：目录 / 下载源、Java、内存、窗口、启动选项、桌面快捷方式、微软登录。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...core import paths as P
from ...core.constants import BMCLAPI, MIRRORS, OFFICIAL
from ...core.java import JavaInfo, find_java, probe_java, suggest_memory_mb, total_system_memory_mb
from ...core.shortcut import ShortcutError, platform_label
from .. import shortcut_flow
from ..helpers import reveal_path
from ..theme import FONT_SIZE, METRICS, MONO_FAMILY, SPACE
from ..widgets import Badge, Card, PathPicker, ProgressDialog, caption
from ..workers import Worker, start_worker, wait_worker
from .base import BasePage


class SettingsPage(BasePage):
    def __init__(self, main) -> None:
        super().__init__(main)
        self._javas: list[JavaInfo] = []
        self._shortcut_path: Path | None = None
        self._build_ui()
        self.refresh()

    # ---------------------------------------------------------------- 界面

    def _build_ui(self) -> None:
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(
            METRICS["page_margin_h"],
            METRICS["page_margin_v"],
            METRICS["page_margin_h"],
            METRICS["page_margin_v"],
        )
        layout.setSpacing(SPACE["xl"])

        header, _sub = self.make_header("设置", "所有改动都会立即写入 config.json，无需手动保存")
        layout.addWidget(header)

        # ---------------- 目录与源
        basic = Card("目录与下载源", subtitle="修改游戏目录后会自动重新扫描已安装版本")
        form = self._form()
        self.game_dir_picker = PathPicker("", placeholder="选择或输入 .minecraft 目录")
        self.mirror_combo = QComboBox()
        for key, mirror in MIRRORS.items():
            self.mirror_combo.addItem(mirror.name, key)
        form.addRow("游戏目录：", self.game_dir_picker)
        form.addRow("下载源：", self.mirror_combo)
        basic.add_layout(form)
        basic.add_divider()
        save_row = QHBoxLayout()
        save_row.addStretch(1)
        self.save_basic_btn = QPushButton("保存目录与下载源")
        self.save_basic_btn.setProperty("primary", True)
        self.save_basic_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_basic_btn.clicked.connect(self._save_basic)
        save_row.addWidget(self.save_basic_btn)
        basic.add_layout(save_row)
        layout.addWidget(basic)

        # ---------------- Java
        java_card = Card("Java 运行环境", subtitle="1.18+ 需要 Java 17，1.20.5+ 需要 Java 21")
        jform = self._form()
        self.java_combo = QComboBox()
        self.java_combo.setMinimumWidth(420)
        self.java_combo.setMinimumHeight(METRICS["control_height"])
        jform.addRow("使用的 Java：", self.java_combo)
        java_card.add_layout(jform)

        jrow = QHBoxLayout()
        jrow.setSpacing(SPACE["md"])
        self.detect_btn = QPushButton("重新检测")
        self.detect_btn.clicked.connect(self._detect_java)
        self.browse_java_btn = QPushButton("手动指定…")
        self.browse_java_btn.clicked.connect(self._browse_java)
        for button in (self.detect_btn, self.browse_java_btn):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            jrow.addWidget(button)
        jrow.addStretch(1)
        java_card.add_layout(jrow)

        self.java_hint = QLabel("")
        self.java_hint.setProperty("muted", True)
        self.java_hint.setWordWrap(True)
        java_card.add(self.java_hint)
        layout.addWidget(java_card)

        # ---------------- 内存
        mem_card = Card("内存分配", subtitle="分配过大会让系统频繁换页，反而更卡")
        mform = self._form()
        self.auto_mem_cb = QCheckBox("自动分配（推荐，按物理内存计算）")
        self.auto_mem_cb.setCursor(Qt.CursorShape.PointingHandCursor)
        self.max_mem = QSpinBox()
        self.max_mem.setRange(512, 65536)
        self.max_mem.setSingleStep(256)
        self.max_mem.setSuffix(" MB")
        self.max_mem.setFixedWidth(170)
        self.min_mem = QSpinBox()
        self.min_mem.setRange(128, 8192)
        self.min_mem.setSingleStep(128)
        self.min_mem.setSuffix(" MB")
        self.min_mem.setFixedWidth(170)
        mform.addRow("", self.auto_mem_cb)
        mform.addRow("最大内存：", self.max_mem)
        mform.addRow("初始内存：", self.min_mem)
        mem_card.add_layout(mform)
        self.mem_hint = QLabel("")
        self.mem_hint.setProperty("muted", True)
        self.mem_hint.setWordWrap(True)
        mem_card.add(self.mem_hint)
        layout.addWidget(mem_card)

        # ---------------- 窗口
        win_card = Card("游戏窗口")
        wform = self._form()
        self.fullscreen_cb = QCheckBox("全屏启动")
        self.fullscreen_cb.setCursor(Qt.CursorShape.PointingHandCursor)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(0, 7680)
        self.width_spin.setSuffix(" px")
        self.width_spin.setFixedWidth(130)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(0, 4320)
        self.height_spin.setSuffix(" px")
        self.height_spin.setFixedWidth(130)
        wh = QHBoxLayout()
        wh.setSpacing(SPACE["md"])
        wh.addWidget(self.width_spin)
        wh.addWidget(QLabel("×"))
        wh.addWidget(self.height_spin)
        wh.addStretch(1)
        wform.addRow("", self.fullscreen_cb)
        wform.addRow("窗口尺寸：", wh)
        win_card.add_layout(wform)
        layout.addWidget(win_card)

        # ---------------- 启动选项
        launch_card = Card("启动选项", subtitle="高级参数会追加到自动生成的启动命令之后")
        lform = self._form()
        self.visible_cb = QCheckBox("启动游戏后保留启动器窗口")
        self.log_cb = QCheckBox("启动时显示日志窗口")
        for cb in (self.visible_cb, self.log_cb):
            cb.setCursor(Qt.CursorShape.PointingHandCursor)
        self.jvm_args_edit = QTextEdit()
        self.jvm_args_edit.setFixedHeight(64)
        self.jvm_args_edit.setPlaceholderText("额外 JVM 参数，例如：\n-Dfml.ignoreInvalidMinecraftCertificates=true")
        self.game_args_edit = QTextEdit()
        self.game_args_edit.setFixedHeight(64)
        self.game_args_edit.setPlaceholderText("额外游戏参数，例如：\n--server mc.example.com")

        lform.addRow("", self.visible_cb)
        lform.addRow("", self.log_cb)
        lform.addRow("额外 JVM 参数：", self.jvm_args_edit)
        lform.addRow("额外游戏参数：", self.game_args_edit)
        launch_card.add_layout(lform)
        layout.addWidget(launch_card)

        # ---------------- 桌面快捷方式
        self.shortcut_card = self._build_shortcut_card()
        layout.addWidget(self.shortcut_card)

        # ---------------- 网络安全（防火墙）
        layout.addWidget(self._build_security_card())

        # ---------------- 微软登录
        ms_card = Card("微软登录（正版）", subtitle="本启动器不内置任何第三方 Client ID，需自行注册 Azure 应用")
        msl = self._form()
        self.client_id_edit = QLineEdit()
        self.client_id_edit.setPlaceholderText("Azure 应用的应用程序(客户端) ID")
        msl.addRow("Client ID：", self.client_id_edit)
        ms_card.add_layout(msl)
        self.ms_help = QLabel(
            "在 Azure 门户 → 应用注册 → 新建应用（任何 Microsoft 账户均可登录），"
            "重定向 URI 填 <a href='https://login.microsoftonline.com/common/oauth2/nativeclient'>"
            "https://login.microsoftonline.com/common/oauth2/nativeclient</a>，"
            "并在「身份验证 → 高级设置」中启用「允许公共客户端流」，再把应用 ID 粘贴到上面。"
        )
        self.ms_help.setProperty("muted", True)
        self.ms_help.setWordWrap(True)
        self.ms_help.setOpenExternalLinks(True)
        ms_card.add(self.ms_help)
        layout.addWidget(ms_card)

        # ---------------- 保存
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.save_all_btn = QPushButton("保存全部设置")
        self.save_all_btn.setProperty("primary", True)
        self.save_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_all_btn.clicked.connect(self._save_all)
        bottom.addWidget(self.save_all_btn)
        layout.addLayout(bottom)

        # ---------------- 关于
        about = Card("关于", subtitle="配置与账户存放在启动器数据目录")
        about.add(QLabel("PyLauncher — 模块化的《我的世界》Java 版启动器"))
        self.paths_label = QLabel("")
        self.paths_label.setProperty("micro", True)
        self.paths_label.setWordWrap(True)
        self.paths_label.setStyleSheet(f"font-family: {MONO_FAMILY}; font-size: {FONT_SIZE['micro']}px;")
        self.paths_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        about.add(self.paths_label)
        about.add_divider()
        row = QHBoxLayout()
        row.setSpacing(SPACE["md"])
        open_cfg = QPushButton("打开启动器目录")
        open_cfg.setProperty("quiet", True)
        open_cfg.clicked.connect(lambda: reveal_path(P.launcher_root()))
        open_log = QPushButton("打开日志目录")
        open_log.setProperty("quiet", True)
        open_log.clicked.connect(lambda: reveal_path(P.logs_dir()))
        readme_btn = QPushButton("查看 README")
        readme_btn.setProperty("quiet", True)
        readme_btn.clicked.connect(self._open_readme)
        for button in (open_cfg, open_log, readme_btn):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            row.addWidget(button)
        row.addStretch(1)
        about.add_layout(row)
        layout.addWidget(about)

        layout.addStretch(1)
        self.scroll.setWidget(container)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.scroll)

    @staticmethod
    def _form() -> QFormLayout:
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(SPACE["lg"])
        form.setVerticalSpacing(SPACE["lg"])
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return form

    # ---------------------------------------------------------------- 快捷方式区

    def _build_shortcut_card(self) -> Card:
        card = Card("桌面快捷方式", subtitle=f"当前平台：{platform_label()}")

        status_row = QHBoxLayout()
        status_row.setSpacing(SPACE["md"])
        status_row.addWidget(caption("状态"))
        self.shortcut_badge = Badge("检测中", "neutral")
        status_row.addWidget(self.shortcut_badge)
        self.shortcut_path_label = QLabel("")
        self.shortcut_path_label.setProperty("micro", True)
        self.shortcut_path_label.setWordWrap(True)
        self.shortcut_path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        status_row.addWidget(self.shortcut_path_label, 1)
        card.add_layout(status_row)

        self.shortcut_target_label = QLabel("")
        self.shortcut_target_label.setProperty("micro", True)
        self.shortcut_target_label.setWordWrap(True)
        self.shortcut_target_label.setStyleSheet(
            f"font-family: {MONO_FAMILY}; font-size: {FONT_SIZE['micro']}px;"
        )
        self.shortcut_target_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card.add(self.shortcut_target_label)

        card.add_divider()

        self.auto_shortcut_cb = QCheckBox("首次运行时自动在桌面创建快捷方式")
        self.auto_shortcut_cb.setChecked(bool(self.config.get("shortcut.auto_create", True)))
        self.auto_shortcut_cb.setToolTip(
            "仅在安装后第一次启动时创建一次；已经创建过或创建失败都不会重复打扰。"
        )
        self.auto_shortcut_cb.setCursor(Qt.CursorShape.PointingHandCursor)
        self.auto_shortcut_cb.stateChanged.connect(self._on_auto_shortcut_changed)
        card.add(self.auto_shortcut_cb)

        row = QHBoxLayout()
        row.setSpacing(SPACE["md"])
        self.create_shortcut_btn = QPushButton("创建桌面快捷方式")
        self.create_shortcut_btn.setProperty("accent", True)
        self.create_shortcut_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.create_shortcut_btn.clicked.connect(self._create_shortcut)
        self.remove_shortcut_btn = QPushButton("删除快捷方式")
        self.remove_shortcut_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.remove_shortcut_btn.clicked.connect(self._remove_shortcut)
        self.reveal_shortcut_btn = QPushButton("打开所在位置")
        self.reveal_shortcut_btn.setProperty("quiet", True)
        self.reveal_shortcut_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reveal_shortcut_btn.clicked.connect(self._reveal_shortcut)
        row.addWidget(self.create_shortcut_btn)
        row.addWidget(self.remove_shortcut_btn)
        row.addWidget(self.reveal_shortcut_btn)
        row.addStretch(1)
        card.add_layout(row)

        hint = QLabel(
            "快捷方式使用应用 logo 作为图标，双击即以当前配置启动启动器（会带上 --launcher-home，"
            "因此不会丢配置）。若系统拒绝创建 .lnk，会自动降级为桌面启动脚本。"
        )
        hint.setProperty("micro", True)
        hint.setWordWrap(True)
        card.add(hint)
        return card

    def _on_auto_shortcut_changed(self, _state: int) -> None:
        enabled = self.auto_shortcut_cb.isChecked()
        self.config.set("shortcut.auto_create", enabled)
        hint = "下次启动时生效。" if enabled else "不再自动创建，可随时手动点击上方按钮。"
        self.flash(f"已{'开启' if enabled else '关闭'}自动创建桌面快捷方式 · {hint}")

    def _build_security_card(self) -> Card:
        """软件防火墙：域名白名单与下载约束的可视化入口。"""
        from ...core.netguard import guard

        card = Card("网络安全（防火墙）", subtitle="所有下载请求与写盘操作都会经过这一层检查")
        count, max_bytes = guard.describe()

        row = QHBoxLayout()
        row.setSpacing(SPACE["md"])
        self.security_badge = Badge(f"白名单 {count} 个域名", "success")
        row.addWidget(self.security_badge)
        self.security_label = QLabel(
            f"仅允许 HTTPS（或明确放行的内网地址）· 单文件上限 {max_bytes // (1024 * 1024)} MB"
        )
        self.security_label.setProperty("micro", True)
        self.security_label.setWordWrap(True)
        row.addWidget(self.security_label, 1)
        card.add_layout(row)

        form = QHBoxLayout()
        form.setSpacing(SPACE["md"])
        self.trust_edit = QLineEdit()
        self.trust_edit.setPlaceholderText("要额外放行的域名，如 mirror.example.com")
        self.trust_edit.setMinimumHeight(METRICS["control_height"])
        form.addWidget(self.trust_edit, 1)
        self.trust_btn = QPushButton("添加信任")
        self.trust_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.trust_btn.clicked.connect(self._add_trusted_host)
        form.addWidget(self.trust_btn)
        card.add_layout(form)

        hint = QLabel(
            "启动器默认只访问 Mojang / 官方镜像 / Modrinth / Fabric / Forge 等域名；"
            "下载目标路径会被限制在游戏目录内，压缩包还会拦截 zip slip 与压缩包炸弹。"
        )
        hint.setProperty("micro", True)
        hint.setWordWrap(True)
        card.add(hint)
        return card

    def _add_trusted_host(self) -> None:
        from ...core.netguard import guard

        host = self.trust_edit.text().strip().lower()
        if not host:
            self.warn("请输入要放行的域名")
            return
        host = host.split("//")[-1].split("/")[0]
        if not host or "." not in host:
            self.warn("域名格式不正确，示例：mirror.example.com")
            return
        guard.add_host(host)
        self.trust_edit.clear()
        count, _ = guard.describe()
        self.security_badge.set_state(f"白名单 {count} 个域名", "success")
        self.flash(f"已放行 {host}")

    def _refresh_shortcut_state(self) -> None:
        try:
            self._shortcut_path = shortcut_flow.current_path(self.config)
        except Exception:  # noqa: BLE001
            self._shortcut_path = None

        spec = None
        try:
            spec = shortcut_flow.build_spec(self.config)
        except Exception:  # noqa: BLE001
            spec = None

        if self._shortcut_path is not None:
            self.shortcut_badge.set_state("已创建", "success")
            self.shortcut_path_label.setText(str(self._shortcut_path))
        else:
            self.shortcut_badge.set_state("未创建", "neutral")
            last = str(self.config.get("shortcut.last_created", "") or "")
            self.shortcut_path_label.setText(
                f"上次创建于 {last}，可再次创建以更新" if last else "创建后即可从桌面双击启动"
            )

        if spec is not None:
            target = shortcut_flow.launcher_executable()
            args = " ".join(shortcut_flow.startup_arguments())
            self.shortcut_target_label.setText(f"启动目标：{target}  {args}")
        else:
            self.shortcut_target_label.setText("")

        self.remove_shortcut_btn.setEnabled(self._shortcut_path is not None)
        self.reveal_shortcut_btn.setEnabled(self._shortcut_path is not None)

    def _create_shortcut(self) -> None:
        self.create_shortcut_btn.setEnabled(False)
        self.main.set_busy(True, "正在创建桌面快捷方式…")
        try:
            self._create_shortcut_inner()
        finally:
            self.main.set_busy(False)
            self.create_shortcut_btn.setEnabled(True)
            self._refresh_shortcut_state()

    def _create_shortcut_inner(self) -> None:
        try:
            result = shortcut_flow.create(self.config)
        except ShortcutError as exc:
            if exc.reason == ShortcutError.EXISTS:
                self._handle_exists(exc)
                return
            if exc.reason in (ShortcutError.PERMISSION_DENIED, ShortcutError.DESKTOP_NOT_FOUND):
                if self._retry_elsewhere(exc):
                    return
            self._show_shortcut_error(exc)
            return
        except OSError as exc:
            self._show_shortcut_error(
                ShortcutError(
                    f"写入快捷方式时发生文件系统错误：{exc}",
                    reason=ShortcutError.PERMISSION_DENIED,
                    detail=str(exc),
                )
            )
            return

        self.flash(result.message)
        if result.degraded:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("已使用降级方案")
            box.setText("系统快捷方式创建失败，已改为生成桌面启动脚本。")
            box.setInformativeText(
                f"{result.message}\n\n"
                "脚本双击即可启动，但没有自定义图标。\n"
                "如需带图标的正式快捷方式，可尝试以管理员身份运行本程序后重试。"
            )
            if result.detail:
                box.setDetailedText(result.detail)
            box.exec()
        else:
            self.info(
                f"{result.message}\n\n双击桌面图标即可启动，名称与图标均已写入。"
                + (f"\n\n已覆盖原有快捷方式（原目标：{result.previous_target}）" if result.previous_target else "")
            )

    def _handle_exists(self, exc: ShortcutError) -> None:
        if self.ask("桌面已存在同名快捷方式，是否覆盖？"):
            try:
                result = shortcut_flow.create(self.config, overwrite=True)
            except ShortcutError as retry_exc:
                self._show_shortcut_error(retry_exc)
                return
            self.flash(result.message)
            self.info(f"{result.message}\n旧快捷方式已被覆盖。")
        else:
            self.flash("已取消创建")

    def _retry_elsewhere(self, exc: ShortcutError) -> bool:
        """桌面不可写 / 不存在时，让用户另选保存位置（降级方案）。"""
        if not self.ask(f"{exc.message}\n\n是否改存到其他位置？"):
            return False
        directory = QFileDialog.getExistingDirectory(self, "选择快捷方式保存位置", str(Path.home()))
        if not directory:
            return False
        try:
            result = shortcut_flow.create(self.config, dest_dir=Path(directory))
        except ShortcutError as retry_exc:
            self._show_shortcut_error(retry_exc)
            return True
        self.flash(result.message)
        self.info(f"{result.message}\n位置：{result.path}")
        return True

    def _show_shortcut_error(self, exc: ShortcutError) -> None:
        suggestion = {
            ShortcutError.PERMISSION_DENIED: "可以尝试：以管理员身份运行；或换一个保存位置；或检查安全软件是否拦截写入。",
            ShortcutError.DESKTOP_NOT_FOUND: "可以手动指定保存位置，或确认系统桌面是否被重定向（如 OneDrive）。",
            ShortcutError.SYSTEM_UNAVAILABLE: "系统 Shell 组件不可用，通常是被安全软件拦截；可改用「另存启动脚本」。",
            ShortcutError.TARGET_MISSING: "程序可能已被移动或卸载；把启动器放回原位置后再试，或重新安装。",
            ShortcutError.UNSUPPORTED: "当前系统暂不支持自动创建快捷方式，可手动创建启动脚本。",
        }.get(exc.reason, "请稍后重试，或换一个保存位置。")

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("创建快捷方式失败")
        box.setText(exc.message)
        box.setInformativeText(suggestion)
        if exc.detail:
            box.setDetailedText(exc.detail)
        box.exec()

    def _remove_shortcut(self) -> None:
        if self._shortcut_path is None:
            return
        if not self.ask(f"删除快捷方式？\n{self._shortcut_path}"):
            return
        try:
            result = shortcut_flow.remove(self._shortcut_path)
        except ShortcutError as exc:
            self._show_shortcut_error(exc)
            return
        self.flash(result.message)
        self._refresh_shortcut_state()

    def _reveal_shortcut(self) -> None:
        if self._shortcut_path is not None:
            reveal_path(self._shortcut_path)

    # ---------------------------------------------------------------- 刷新

    def refresh(self) -> None:
        cfg = self.config
        self.game_dir_picker.setText(str(cfg.get("game_dir", P.default_game_dir())))
        idx = self.mirror_combo.findData(str(cfg.get("mirror", BMCLAPI)))
        self.mirror_combo.setCurrentIndex(max(0, idx))

        # Java 探测要启动子进程（实测约 0.6s），放到后台做，界面先显示「检测中」。
        # 这是打开设置页会明显卡一下的直接原因。
        self._detect_java_async()

        self.auto_mem_cb.setChecked(bool(cfg.get("memory.auto", True)))
        self.max_mem.setValue(int(cfg.get("memory.max_mb", 4096)))
        self.min_mem.setValue(int(cfg.get("memory.min_mb", 512)))
        total = total_system_memory_mb()
        self.mem_hint.setText(
            f"物理内存约 {total} MB，推荐最大分配 {suggest_memory_mb()} MB。"
        )

        self.fullscreen_cb.setChecked(bool(cfg.get("window.fullscreen", False)))
        self.width_spin.setValue(int(cfg.get("window.width", 1280)))
        self.height_spin.setValue(int(cfg.get("window.height", 720)))

        self.visible_cb.setChecked(bool(cfg.get("launch.launcher_visible", True)))
        self.log_cb.setChecked(bool(cfg.get("launch.show_log_window", True)))
        self.jvm_args_edit.setPlainText(str(cfg.get("launch.extra_jvm_args", "") or ""))
        self.game_args_edit.setPlainText(str(cfg.get("launch.extra_game_args", "") or ""))

        self.client_id_edit.setText(str(cfg.get("microsoft.client_id", "") or ""))
        self.paths_label.setText(f"{P.config_file()}\n{P.accounts_file()}")
        cb = getattr(self, "auto_shortcut_cb", None)
        if cb is not None:
            cb.blockSignals(True)
            cb.setChecked(bool(cfg.get("shortcut.auto_create", True)))
            cb.blockSignals(False)
        self._refresh_shortcut_state()

    def _reload_java_combo(self) -> None:
        self.java_combo.blockSignals(True)
        self.java_combo.clear()
        self.java_combo.addItem("自动选择（按版本需求择优）", "")
        for j in self._javas:
            self.java_combo.addItem(f"{j.display} — {j.path}", str(j.path))
        saved = str(self.config.get("java.selected", "") or "")
        if saved:
            idx = self.java_combo.findData(saved)
            if idx < 0:
                self.java_combo.addItem(saved, saved)
                idx = self.java_combo.count() - 1
            self.java_combo.setCurrentIndex(idx)
        self.java_combo.blockSignals(False)
        best = max((j.major for j in self._javas), default=0)
        self.java_hint.setText(
            f"共检测到 {len(self._javas)} 个 Java，最高 Java {best}。"
            "实际所需版本以每个版本 JSON 的 javaVersion 为准（启动前会自动核对）。"
        )

    # ---------------------------------------------------------------- Java

    def _detect_java_async(self) -> None:
        """后台检测 Java：不阻塞页面构建，完成后只更新下拉框。"""
        self._javas = self._javas or []
        self.java_hint.setText("正在检测 Java 运行环境…")
        self.java_combo.blockSignals(True)
        self.java_combo.clear()
        self.java_combo.addItem("检测中…", "")
        self.java_combo.blockSignals(False)

        def work(on_progress, on_message, should_cancel=None):
            return find_java()

        def done(javas):
            self._javas = javas
            self._reload_java_combo()

        worker = Worker(work)
        worker.succeeded.connect(done)
        worker.failed.connect(lambda m: self.java_hint.setText(f"Java 检测失败：{m.splitlines()[0]}"))
        start_worker(self, worker)

    def _detect_java(self) -> None:
        dlg = ProgressDialog("检测 Java", self, cancelable=False)
        dlg.set_indeterminate()

        def work(on_progress, on_message):
            on_message("扫描系统目录…")
            return find_java()

        worker = Worker(work)
        worker.message.connect(dlg.set_stage)

        def done(javas):
            self._javas = javas
            self._reload_java_combo()
            dlg.accept()
            self.info(f"检测到 {len(javas)} 个可用的 Java 运行环境。")

        worker.succeeded.connect(done)
        worker.failed.connect(dlg.done_with_error)
        start_worker(self, worker)
        dlg.exec()
        wait_worker(worker, 2000)

    def _browse_java(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 java 可执行文件", str(Path.home()),
            "Java (java java.exe javaw.exe);;所有文件 (*.*)",
        )
        if not path:
            return
        info = probe_java(Path(path))
        if info is None:
            self.warn(f"该文件不是有效的 Java 可执行文件：\n{path}")
            return
        self._javas.append(info)
        self._javas.sort(key=lambda j: (j.bitness == 64, j.major), reverse=True)
        self._reload_java_combo()
        idx = self.java_combo.findData(str(info.path))
        if idx >= 0:
            self.java_combo.setCurrentIndex(idx)

    # ---------------------------------------------------------------- 保存

    def _save_basic(self) -> None:
        new_dir = str(self.game_dir_picker.text).strip()
        if not new_dir:
            self.warn("游戏目录不能为空")
            return
        changed = new_dir != str(self.config.get("game_dir", ""))
        self.config.set("game_dir", new_dir)
        self.config.set("mirror", str(self.mirror_combo.currentData() or BMCLAPI))
        self.flash("已保存目录与下载源")
        if changed:
            self.main.notify_game_dir_changed()
            self.refresh()

    def _save_all(self) -> None:
        cfg = self.config
        cfg.set("java.selected", str(self.java_combo.currentData() or ""))
        cfg.set("memory.auto", self.auto_mem_cb.isChecked())
        cfg.set("memory.max_mb", self.max_mem.value())
        cfg.set("memory.min_mb", self.min_mem.value())
        cfg.set("window.fullscreen", self.fullscreen_cb.isChecked())
        cfg.set("window.width", self.width_spin.value())
        cfg.set("window.height", self.height_spin.value())
        cfg.set("launch.launcher_visible", self.visible_cb.isChecked())
        cfg.set("launch.show_log_window", self.log_cb.isChecked())
        cfg.set("launch.extra_jvm_args", self.jvm_args_edit.toPlainText().strip())
        cfg.set("launch.extra_game_args", self.game_args_edit.toPlainText().strip())
        cfg.set("microsoft.client_id", self.client_id_edit.text().strip())
        cb = getattr(self, "auto_shortcut_cb", None)
        if cb is not None:
            cfg.set("shortcut.auto_create", cb.isChecked())
        # 切换下载源后必须让「可用性探测」缓存失效，否则还会照着旧结果判断
        try:
            from ...core.http import invalidate_source_cache

            invalidate_source_cache()
        except Exception:  # noqa: BLE001
            pass
        self.flash("全部设置已保存")
        self.main.notify_settings_changed()

    def _open_readme(self) -> None:
        readme = Path(__file__).resolve().parents[3] / "README.md"
        if readme.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(readme)))
