"""可复用界面组件：卡片、徽标、忙碌按钮、加载指示器、空状态、进度/日志对话框。

组件全部从 `theme` 的令牌取值，页面里不再出现魔法数字，保证视觉一致。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QRectF, QSize, Qt, QTime, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .theme import COLORS, FONT_SIZE, MONO_FAMILY, RADIUS, SPACE, mono_font


# -------------------------------------------------------------------- 卡片


class Card(QFrame):
    """带标题的白色卡片：标题 + 可选副标题 + 分隔线 + 内容区。"""

    def __init__(
        self,
        title: str = "",
        parent: Optional[QWidget] = None,
        subtitle: str = "",
        object_name: str = "Card",
        padding: Optional[int] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        pad = SPACE["xl"] if padding is None else padding

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(pad, pad - 2, pad, pad)
        self._outer.setSpacing(SPACE["md"])

        # 标题行：标题/副标题在左，可选的操作或状态徽标在右
        self._header_slot: Optional[QHBoxLayout] = None
        self.title_label: Optional[QLabel] = None
        self.subtitle_label: Optional[QLabel] = None
        if title or subtitle:
            header = QHBoxLayout()
            header.setContentsMargins(0, 0, 0, 0)
            header.setSpacing(SPACE["md"])

            titles = QVBoxLayout()
            titles.setContentsMargins(0, 0, 0, 0)
            titles.setSpacing(2)

            if title:
                self.title_label = QLabel(title)
                self.title_label.setObjectName("CardTitle")
                titles.addWidget(self.title_label)
            if subtitle:
                self.subtitle_label = QLabel(subtitle)
                self.subtitle_label.setObjectName("CardSubtitle")
                self.subtitle_label.setWordWrap(True)
                titles.addWidget(self.subtitle_label)

            header.addLayout(titles, 1)
            self._header_slot = QHBoxLayout()
            self._header_slot.setContentsMargins(0, 0, 0, 0)
            self._header_slot.setSpacing(SPACE["sm"])
            header.addLayout(self._header_slot)
            self._outer.addLayout(header)

        self.body = QWidget()
        self.body.setObjectName("CardBody")
        self._layout = QVBoxLayout(self.body)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(SPACE["lg"])
        self._outer.addWidget(self.body)

    # ------------------------------------------------------------ 内容

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self._layout.addWidget(widget, stretch)
        return widget

    def add_layout(self, layout, stretch: int = 0) -> None:
        self._layout.addLayout(layout, stretch)

    def add_divider(self) -> None:
        line = QFrame()
        line.setObjectName("CardDivider")
        line.setFixedHeight(1)
        self._layout.addWidget(line)

    def set_subtitle(self, text: str) -> None:
        if self.subtitle_label is not None:
            self.subtitle_label.setText(text)
            self.subtitle_label.setVisible(bool(text))

    def add_header_widget(self, widget: QWidget) -> QWidget:
        """把徽标/操作按钮放到标题行右侧。"""
        if self._header_slot is not None:
            self._header_slot.addWidget(widget, 0, Qt.AlignmentFlag.AlignTop)
        return widget


class HeroCard(Card):
    """首页主操作卡片：更大的内边距与更明显的层次。"""

    def __init__(self, title: str = "", parent: Optional[QWidget] = None, subtitle: str = "") -> None:
        super().__init__(title, parent, subtitle=subtitle, object_name="HeroCard", padding=SPACE["xxl"])


# -------------------------------------------------------------------- 徽标


class Badge(QLabel):
    """小号状态徽标：primary / success / warning / danger / neutral。"""

    TONES = ("primary", "success", "warning", "danger", "neutral")

    def __init__(self, text: str = "", tone: str = "neutral", parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setProperty("badge", tone)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def set_tone(self, tone: str) -> None:
        if tone not in self.TONES:
            tone = "neutral"
        self.setProperty("badge", tone)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_state(self, text: str, tone: str) -> None:
        self.setText(text)
        self.set_tone(tone)


# -------------------------------------------------------------------- 加载指示


class BusySpinner(QWidget):
    """旋转弧形加载指示器（QSS 无法做动画，因此这里自绘）。"""

    def __init__(self, diameter: int = 16, parent: Optional[QWidget] = None, color: str = COLORS["primary"]) -> None:
        super().__init__(parent)
        self._diameter = diameter
        self._angle = 0
        self._color = QColor(color)
        self._timer = QTimer(self)
        self._timer.setInterval(70)
        self._timer.timeout.connect(self._advance)
        self.setFixedSize(diameter + 4, diameter + 4)
        self.setVisible(False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._diameter + 4, self._diameter + 4)

    def _advance(self) -> None:
        self._angle = (self._angle + 30) % 360
        self.update()

    def start(self) -> None:
        self.setVisible(True)
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self.setVisible(False)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(self._color)
        pen.setWidthF(max(2.0, self._diameter / 8))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        inner = self._diameter - 3
        rect = QRectF((self.width() - inner) / 2, (self.height() - inner) / 2, inner, inner)
        painter.drawArc(rect, -self._angle * 16, 110 * 16)
        painter.end()


class LoadingButton(QPushButton):
    """带加载态的按钮：忙碌时禁用、保留原文案、可选择提示文字。"""

    def __init__(self, text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self._idle_text = text
        self._busy = False

    def set_busy(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        if busy:
            self._idle_text = self.text()
            self.setText(text or "处理中…")
            self.setEnabled(False)
            self.setProperty("busy", True)
        else:
            self.setText(text or self._idle_text)
            self.setEnabled(True)
            self.setProperty("busy", False)
        self.style().unpolish(self)
        self.style().polish(self)

    @property
    def busy(self) -> bool:
        return self._busy


# -------------------------------------------------------------------- 空状态


class EmptyState(QWidget):
    """列表为空时的占位块，避免大块空白让人误以为加载失败。"""

    def __init__(
        self,
        title: str,
        description: str = "",
        parent: Optional[QWidget] = None,
        action: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("EmptyState")
        # 纯 QWidget 子类必须显式开启，QSS 的 background / border 才会绘制
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE["xxxl"], SPACE["xxl"], SPACE["xxxl"], SPACE["xxl"])
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        inner = QWidget()
        inner.setObjectName("CardBody")
        inner.setMaximumWidth(460)
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(SPACE["sm"])
        inner_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.title_label = QLabel(title)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = self.title_label.font()
        font.setBold(True)
        font.setPixelSize(FONT_SIZE["body_strong"])
        self.title_label.setFont(font)
        inner_layout.addWidget(self.title_label)

        self.desc_label = QLabel(description)
        self.desc_label.setProperty("muted", True)
        self.desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.desc_label.setWordWrap(True)
        self.desc_label.setVisible(bool(description))
        inner_layout.addWidget(self.desc_label)

        self._action_row = QHBoxLayout()
        self._action_row.setContentsMargins(0, SPACE["md"], 0, 0)
        self._action_row.setSpacing(SPACE["md"])
        self._action_row.addStretch(1)
        if action is not None:
            self._action_row.addWidget(action)
        self._action_row.addStretch(1)
        inner_layout.addLayout(self._action_row)

        layout.addWidget(inner, 0, Qt.AlignmentFlag.AlignCenter)

    def set_text(self, title: str, description: str = "") -> None:
        self.title_label.setText(title)
        self.desc_label.setText(description)
        self.desc_label.setVisible(bool(description))


# -------------------------------------------------------------------- 路径选择


class PathPicker(QWidget):
    """文本框 + 浏览按钮。"""

    pathChanged = Signal(str)

    def __init__(
        self,
        text: str = "",
        directory: bool = True,
        file_filter: str = "",
        parent: Optional[QWidget] = None,
        placeholder: str = "",
    ) -> None:
        super().__init__(parent)
        self._directory = directory
        self._filter = file_filter

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE["md"])

        self.edit = QLineEdit(text)
        self.edit.setPlaceholderText(placeholder)
        self.edit.textChanged.connect(self.pathChanged.emit)
        self.button = QPushButton("浏览…")
        self.button.setFixedWidth(76)
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.clicked.connect(self._browse)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.button)

    # ------------------------------------------------------------ 行为

    def _browse(self) -> None:
        start = self.edit.text().strip() or str(Path.home())
        if self._directory:
            chosen = QFileDialog.getExistingDirectory(self, "选择目录", start)
        else:
            chosen, _ = QFileDialog.getOpenFileName(self, "选择文件", start, self._filter)
        if chosen:
            self.edit.setText(chosen)

    @property
    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, value: str) -> None:  # noqa: N802
        self.edit.setText(value)


# -------------------------------------------------------------------- 进度对话框


class ProgressDialog(QDialog):
    """模态进度对话框：阶段标题 + 加载指示 + 进度条 + 详细日志；支持取消。"""

    cancelled = Signal()

    def __init__(self, title: str = "正在处理", parent: Optional[QWidget] = None, cancelable: bool = True) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(580, 300)
        self.setMinimumWidth(480)
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE["xxl"], SPACE["xl"], SPACE["xxl"], SPACE["lg"])
        layout.setSpacing(SPACE["lg"])

        head = QHBoxLayout()
        head.setSpacing(SPACE["md"])
        self.spinner = BusySpinner(18)
        self.spinner.start()
        head.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignVCenter)

        self.stage_label = QLabel("准备中…")
        font = self.stage_label.font()
        font.setBold(True)
        font.setPixelSize(FONT_SIZE["body_strong"])
        self.stage_label.setFont(font)
        head.addWidget(self.stage_label, 1)
        layout.addLayout(head)

        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFixedHeight(18)
        layout.addWidget(self.progress)

        self.detail_label = QLabel("")
        self.detail_label.setProperty("muted", True)
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.log_view.setMaximumHeight(96)
        self.log_view.setFont(mono_font(FONT_SIZE["micro"]))
        self.log_view.setStyleSheet(
            f"font-family: {MONO_FAMILY}; font-size: {FONT_SIZE['micro']}px;"
            f" border-radius: {RADIUS['md']}px; padding: 6px;"
        )
        layout.addWidget(self.log_view, 1)

        buttons = QDialogButtonBox()
        self.cancel_btn = QPushButton("取消")
        buttons.addButton(self.cancel_btn, QDialogButtonBox.ButtonRole.RejectRole)
        buttons.rejected.connect(self._on_cancel)
        if not cancelable:
            self.cancel_btn.setEnabled(False)
        layout.addWidget(buttons, 0, Qt.AlignmentFlag.AlignRight)

    # ------------------------------------------------------------ 更新

    def set_stage(self, text: str) -> None:
        self.stage_label.setText(text)
        self._append_log(text)

    def set_progress(self, done: int, total: int) -> None:
        if total <= 0:
            self.progress.setRange(0, 0)
            return
        self.progress.setRange(0, 100)
        self.progress.setValue(min(100, int(done / total * 100)))
        self.detail_label.setText(f"{done} / {total} 个文件完成")

    def set_indeterminate(self) -> None:
        self.progress.setRange(0, 0)

    def append_log(self, text: str) -> None:
        self._append_log(text)

    def _append_log(self, text: str) -> None:
        stamp = QTime.currentTime().toString("HH:mm:ss")
        self.log_view.appendPlainText(f"[{stamp}] {text}")
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log_view.setTextCursor(cursor)

    def _on_cancel(self) -> None:
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setText("正在取消…")
        self.stage_label.setText("正在取消…")
        self.cancelled.emit()

    def done_with_error(self, message: str) -> None:
        self.spinner.stop()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setProperty("state", "danger")
        self.progress.style().unpolish(self.progress)
        self.progress.style().polish(self.progress)
        self.stage_label.setText("操作失败")
        self.detail_label.setText(message)
        self.cancel_btn.setText("关闭")
        self.cancel_btn.setEnabled(True)
        self._append_log(f"失败：{message.splitlines()[0] if message else ''}")


# -------------------------------------------------------------------- 日志窗口


class LogWindow(QDialog):
    """游戏启动后的实时日志窗口。"""

    closed_by_user = Signal()

    def __init__(self, title: str = "游戏日志", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(920, 580)

        self._max_lines = 3000
        self._line_count = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE["lg"], SPACE["lg"], SPACE["lg"], SPACE["lg"])
        layout.setSpacing(SPACE["md"])

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.view.setFont(mono_font(FONT_SIZE["micro"]))
        self.view.setStyleSheet(
            f"font-family: {MONO_FAMILY}; font-size: {FONT_SIZE['micro']}px;"
            f" border-radius: {RADIUS['md']}px; padding: 8px;"
        )
        layout.addWidget(self.view, 1)

        footer = QHBoxLayout()
        footer.setSpacing(SPACE["md"])
        self.spinner = BusySpinner(14)
        footer.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignVCenter)
        self.status = QLabel("运行中")
        self.status.setProperty("muted", True)
        footer.addWidget(self.status, 1)
        self.clear_btn = QPushButton("清空")
        self.clear_btn.setProperty("quiet", True)
        self.clear_btn.clicked.connect(self.view.clear)
        self.kill_btn = QPushButton("结束进程")
        self.kill_btn.setProperty("danger", True)
        self.copy_btn = QPushButton("复制全部")
        self.copy_btn.setProperty("quiet", True)
        self.copy_btn.clicked.connect(self._copy_all)
        self.hide_btn = QPushButton("关闭")
        self.hide_btn.clicked.connect(self._close)
        for button in (self.clear_btn, self.kill_btn, self.copy_btn, self.hide_btn):
            button.setProperty("inTableCell", True)
            footer.addWidget(button)
        layout.addLayout(footer)

        self.spinner.start()

    # ------------------------------------------------------------ 行为

    def append(self, text: str) -> None:
        self.view.appendPlainText(text)
        self._line_count += 1
        if self._line_count > self._max_lines:
            cursor = self.view.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            cursor.select(QTextCursor.SelectionType.LineUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()
            self._line_count -= 1

    def set_status(self, text: str) -> None:
        self.status.setText(text)
        if "运行中" in text or "启动" in text:
            self.spinner.start()
        else:
            self.spinner.stop()

    def _copy_all(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(self.view.toPlainText())
        self.status.setText("已复制全部日志到剪贴板")
        QTimer.singleShot(2200, lambda: self.status.setText("运行中"))

    def _close(self) -> None:
        self.closed_by_user.emit()
        self.hide()


# -------------------------------------------------------------------- 文本辅助


def make_heading(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("heading", True)
    return label


def make_subheading(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("subheading", True)
    return label


def make_info(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("muted", True)
    label.setWordWrap(True)
    return label


def make_micro(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setProperty("micro", True)
    label.setWordWrap(True)
    return label


def caption(text: str) -> QLabel:
    """字段上方的说明性小标题（左对齐，弱化）。"""
    label = QLabel(text)
    label.setProperty("micro", True)
    return label


def field_label(text: str) -> QLabel:
    """表单左侧标签：统一颜色与最小宽度，让各页面对齐一致。"""
    label = QLabel(text)
    label.setProperty("muted", True)
    label.setMinimumWidth(84)
    label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return label


def add_shadow(widget: QWidget, blur: int = 24, dy: int = 2, alpha: int = 24) -> None:
    """给卡片加轻微投影，拉开层次（不需要阴影时可随时关闭）。"""
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QGraphicsDropShadowEffect

    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, dy)
    color = QColor(COLORS["text"])
    color.setAlpha(alpha)
    effect.setColor(color)
    widget.setGraphicsEffect(effect)


def hline() -> QFrame:
    line = QFrame()
    line.setObjectName("HLine")
    line.setFixedHeight(1)
    return line


def localize_buttons(box: QDialogButtonBox, **texts: str) -> None:
    """把标准按钮改成中文（Qt 翻译缺失时的兜底，保证界面不出现英文按钮）。"""
    mapping = {
        QDialogButtonBox.StandardButton.Ok: texts.get("ok", "确定"),
        QDialogButtonBox.StandardButton.Save: texts.get("save", "保存"),
        QDialogButtonBox.StandardButton.Cancel: texts.get("cancel", "取消"),
        QDialogButtonBox.StandardButton.Close: texts.get("close", "关闭"),
        QDialogButtonBox.StandardButton.Yes: texts.get("yes", "是"),
        QDialogButtonBox.StandardButton.No: texts.get("no", "否"),
        QDialogButtonBox.StandardButton.Apply: texts.get("apply", "应用"),
        QDialogButtonBox.StandardButton.Reset: texts.get("reset", "重置"),
    }
    for standard, label in mapping.items():
        button = box.button(standard)
        if button is not None:
            button.setText(label)
