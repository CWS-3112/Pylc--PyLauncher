"""界面过渡动画（可中断、绝不残留）。

两条铁律，都是踩过坑之后补的：

1. **不允许把控件留在 opacity=0 的状态**。以前给标签页挂 `QGraphicsOpacityEffect(0)`
   再做淡入，控件不可见时 Qt 不会推进动画，效果就永远停在 0 ——
   表现正是「版本列表一片空白，但页签和工具栏都在」。
2. **快照叠加层必须可回收**。每次切换都 `grab()` 一张快照盖在上层，如果动画没跑完
   （频繁切换 / 无事件循环），叠加层会不断累积 → 内存上涨、最终卡死崩溃。

因此这里：
* 动画对象与叠加层只在 `_dispose_overlay()` 里释放，`finished` 与**定时器兜底**双保险；
* 切换时先收尾上一次动画，任何时刻最多一个叠加层；
* 控件不可见 / 尺寸异常时直接切换，不做动画；
* 任何异常都吞掉（动画不该影响功能）。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEasingCurve, QObject, QPropertyAnimation, QTimer, Qt
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel, QStackedWidget, QWidget

DEFAULT_DURATION = 150
# 兜底清理延迟：动画正常时早于它完成，异常时由它兜底，避免控件卡在透明状态
_FALLBACK_EXTRA = 120


def animations_enabled() -> bool:
    import os

    if os.environ.get("PYLAUNCHER_NO_ANIM", "") == "1":
        return False
    try:
        from ..core.config import get_config

        return bool(get_config().get("ui.animations", True))
    except Exception:  # noqa: BLE001
        return True


def _clear_effect(widget: QWidget) -> None:
    """确保控件不残留透明度效果（最坏情况下把不透明度拉满再摘掉）。"""
    try:
        effect = widget.graphicsEffect()
        if isinstance(effect, QGraphicsOpacityEffect):
            effect.setOpacity(1.0)
            widget.setGraphicsEffect(None)
    except RuntimeError:
        pass


def fade_in(widget: QWidget, duration: int = DEFAULT_DURATION) -> Optional[QPropertyAnimation]:
    """淡入。控件不可见或动画关闭时直接返回 None（不做任何效果）。

    无论动画是否真的跑完，都会在 `duration + 120ms` 内摘掉效果，
    因此控件不可能长期停留在透明状态。
    """
    if not animations_enabled() or widget is None:
        return None
    try:
        if not widget.isVisible():
            return None
        _clear_effect(widget)

        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.0)
        widget.setGraphicsEffect(effect)

        anim = QPropertyAnimation(effect, b"opacity", widget)
        anim.setDuration(duration)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        def finish() -> None:
            _clear_effect(widget)

        anim.finished.connect(finish)
        QTimer.singleShot(duration + _FALLBACK_EXTRA, finish)
        anim.start()
        return anim
    except RuntimeError:
        _clear_effect(widget)
        return None


class CrossFadeStack(QStackedWidget):
    """页面切换：旧页快照淡出，新页直接全不透明显示。

    快照只作为**临时叠加层**存在，真实页面从头到尾不挂透明效果，
    所以即使动画被中断，用户看到的也永远是完整内容。
    """

    def __init__(self, parent=None, duration: int = DEFAULT_DURATION) -> None:
        super().__init__(parent)
        self._duration = duration
        self._overlay: Optional[QLabel] = None
        self._anim: Optional[QPropertyAnimation] = None

    # ---------------------------------------------------------- 内部

    def _ensure_overlay(self) -> QLabel:
        """复用同一个叠加层：反复 new/deleteLater 会让待删对象堆积（实测 500 次切换
        累积 350+ 个 QLabel），既涨内存又拖慢切换。"""
        if self._overlay is None:
            overlay = QLabel(self)
            overlay.setObjectName("TransitionOverlay")
            overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            overlay.hide()
            self._overlay = overlay
        return self._overlay

    def _dispose_overlay(self) -> None:
        anim = self._anim
        self._anim = None
        if anim is not None:
            try:
                anim.stop()
            except RuntimeError:
                pass
            anim.deleteLater()
        overlay = self._overlay
        if overlay is not None:
            try:
                overlay.hide()
                overlay.setGraphicsEffect(None)
                overlay.clear()
            except RuntimeError:
                self._overlay = None

    @property
    def overlay_alive(self) -> bool:
        """测试用：是否还有未回收的过渡叠加层。"""
        return self._overlay is not None

    def _switch(self, widget: QWidget) -> None:
        # 先收尾上一次动画，保证任何时刻只有一个叠加层
        self._dispose_overlay()

        previous = self.currentWidget()
        snapshot = None
        if (
            animations_enabled()
            and previous is not None
            and previous.isVisible()
            and self.width() > 0
            and self.height() > 0
        ):
            try:
                snapshot = previous.grab()
            except RuntimeError:
                snapshot = None

        super().setCurrentWidget(widget)
        _clear_effect(widget)

        if snapshot is None or snapshot.isNull():
            return

        try:
            overlay = self._ensure_overlay()
            overlay.setPixmap(snapshot)
            overlay.setGeometry(self.rect())
            overlay.raise_()
            overlay.show()

            effect = QGraphicsOpacityEffect(overlay)
            effect.setOpacity(1.0)
            overlay.setGraphicsEffect(effect)

            anim = QPropertyAnimation(effect, b"opacity", self)
            anim.setDuration(self._duration)
            anim.setStartValue(1.0)
            anim.setEndValue(0.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)

            holder = {"done": False}

            def finish() -> None:
                if holder["done"]:
                    return
                holder["done"] = True
                self._dispose_overlay()

            anim.finished.connect(finish)
            # 双保险：动画没跑完（快速切走 / 无事件循环）也会被清理
            QTimer.singleShot(self._duration + _FALLBACK_EXTRA, finish)

            self._anim = anim
            anim.start()
        except RuntimeError:
            self._dispose_overlay()

    # ---------------------------------------------------------- 对外

    def setCurrentWidget(self, widget: QWidget) -> None:  # noqa: N802
        if widget is None or widget is self.currentWidget():
            return
        try:
            self._switch(widget)
        except Exception:  # noqa: BLE001 - 动画失败绝不能影响切换
            self._dispose_overlay()
            super().setCurrentWidget(widget)
            _clear_effect(widget)

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        widget = self.widget(index)
        if widget is None or widget is self.currentWidget():
            return
        self.setCurrentWidget(widget)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._overlay is not None:
            try:
                self._overlay.setGeometry(self.rect())
            except RuntimeError:
                self._overlay = None

    def closeEvent(self, event) -> None:  # noqa: N802
        self._dispose_overlay()
        super().closeEvent(event)


class TabFade(QObject):
    """标签页切换的轻量淡入（带强制兜底，不会把页面留在透明状态）。"""

    def __init__(self, tabs, duration: int = DEFAULT_DURATION) -> None:
        super().__init__(tabs)
        self._tabs = tabs
        self._duration = duration
        tabs.currentChanged.connect(self._on_changed)

    def _on_changed(self, _index: int) -> None:
        widget = self._tabs.currentWidget()
        if widget is None:
            return
        # 切换瞬间控件可能还没显示，交给下一轮事件循环再判断
        QTimer.singleShot(0, lambda: fade_in(widget, self._duration))


def attach_tab_fade(tab_widget, duration: int = DEFAULT_DURATION) -> TabFade:
    """给 QTabWidget 挂淡入；返回的 `TabFade` 由 tab_widget 持有，随控件销毁。"""
    return TabFade(tab_widget, duration)


__all__ = ["CrossFadeStack", "TabFade", "attach_tab_fade", "animations_enabled", "fade_in"]
