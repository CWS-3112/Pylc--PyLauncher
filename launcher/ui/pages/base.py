"""所有分页的共同基类：统一页面边距、标题区、提示与忙碌态。

页面布局统一为三档层次：

    页面标题区（标题 + 副标题）  →  内容卡片（间距 16）  →  页脚/操作区
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional, Tuple

from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QVBoxLayout, QWidget

from ...core.auth import Account, AccountDatabase
from ...core.config import Config
from ..theme import METRICS, SPACE

if TYPE_CHECKING:  # pragma: no cover
    from ..app import MainWindow


class BasePage(QWidget):
    def __init__(self, main) -> None:
        super().__init__()
        self.main = main
        self.setObjectName("RootPage")

    # ------------------------------------------------------------ 访问器

    @property
    def config(self) -> Config:
        return self.main.config

    @property
    def accounts_db(self) -> AccountDatabase:
        return self.main.accounts_db

    def game_dir(self) -> Path:
        return self.main.game_dir()

    def refresh(self) -> None:
        """页面显示时调用，由子类实现。"""

    # ------------------------------------------------------------ 布局助手

    @staticmethod
    def page_layout(page: QWidget, spacing: Optional[int] = None) -> QVBoxLayout:
        """统一的页面外边距与卡片间距。"""
        layout = QVBoxLayout(page)
        layout.setContentsMargins(
            METRICS["page_margin_h"],
            METRICS["page_margin_v"],
            METRICS["page_margin_h"],
            METRICS["page_margin_v"],
        )
        layout.setSpacing(SPACE["xl"] if spacing is None else spacing)
        return layout

    @staticmethod
    def make_header(title: str, subtitle: str = "") -> Tuple[QWidget, QLabel]:
        """页面标题区：主标题 + 说明文字，返回容器与副标题标签。"""
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, SPACE["xs"])
        layout.setSpacing(SPACE["xs"])

        label = QLabel(title)
        label.setProperty("heading", True)
        layout.addWidget(label)

        sub = QLabel(subtitle)
        sub.setProperty("muted", True)
        sub.setWordWrap(True)
        sub.setVisible(bool(subtitle))
        layout.addWidget(sub)
        return wrapper, sub

    @staticmethod
    def row(*widgets: QWidget, spacing: Optional[int] = None, stretch_last: bool = False) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE["md"] if spacing is None else spacing)
        for widget in widgets:
            layout.addWidget(widget)
        if stretch_last:
            layout.addStretch(1)
        return layout

    # ------------------------------------------------------------ 反馈

    def info(self, text: str, title: str = "提示") -> None:
        QMessageBox.information(self, title, text)

    def warn(self, text: str, title: str = "警告") -> None:
        QMessageBox.warning(self, title, text)

    def error(self, text: str, title: str = "错误") -> None:
        QMessageBox.critical(self, title, text)

    def ask(self, text: str, title: str = "确认") -> bool:
        return QMessageBox.question(
            self, title, text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.Yes

    def flash(self, text: str) -> None:
        """在窗口状态栏短暂提示，避免频繁弹窗打断操作。"""
        flash = getattr(self.main, "flash", None)
        if callable(flash):
            flash(text)
        else:  # pragma: no cover
            self.main.set_status(text)

    def selected_account(self) -> Optional[Account]:
        return self.accounts_db.selected
