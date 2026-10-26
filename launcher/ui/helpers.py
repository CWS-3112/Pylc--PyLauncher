"""界面层小工具：打开目录、格式化时间等。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices


def reveal_path(path: Path | str) -> None:
    """在系统文件管理器中打开（Windows 上为「打开所在文件夹」）。"""
    p = Path(path)
    target = p if p.is_dir() else p.parent
    if not target.exists():
        target.mkdir(parents=True, exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))


def format_timestamp(value) -> str:
    """把 `2024-05-21T09:12:33+00:00` 简化为 `2024-05-21`。"""
    text = str(value or "")
    return text[:10] if len(text) >= 10 else text
