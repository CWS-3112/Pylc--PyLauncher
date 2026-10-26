"""统一的设计系统：配色、间距、字号、圆角令牌 + 全局样式表。

界面上所有颜色、间距、字号、圆角都从这里的令牌派生，页面代码不再写死数值，
从而保证「统一配色 / 间距 / 字体 / 组件样式」以及 hover、选中、禁用、加载等
状态反馈在各页面之间表现一致。

布局层次遵循三档：

    标题区(24px)  →  内容区(16px 卡片间距, 卡内 14px 内边距)  →  操作区(12/16px)
"""

from __future__ import annotations

from PySide6.QtGui import QFont

# ------------------------------------------------------------------ 配色令牌

COLORS = {
    # 中性背景
    "bg": "#eef1f6",
    "surface": "#ffffff",
    "surface_alt": "#f7f9fc",
    "surface_sunken": "#eff2f7",
    "sidebar": "#f7f8fb",
    "sidebar_hover": "#e8edf8",
    "statusbar": "#f7f8fb",
    # 描边
    "border": "#e1e5ec",
    "border_strong": "#c8cfda",
    # 品牌
    "primary": "#2563eb",
    "primary_hover": "#1d4ed8",
    "primary_pressed": "#1739ae",
    "primary_soft": "#e6eeff",
    "primary_text": "#ffffff",
    # 语义
    "success": "#16a34a",
    "success_soft": "#e6f6ec",
    "warning": "#d97706",
    "warning_soft": "#fdf3e3",
    "danger": "#dc2626",
    "danger_soft": "#fdeaea",
    "info": "#0284c7",
    "info_soft": "#e4f2fd",
    # 文本
    "text": "#1b2230",
    "text_secondary": "#4d5769",
    "text_muted": "#7a8598",
    "text_disabled": "#a6aeba",
    "text_on_primary": "#ffffff",
    # 其它
    "selection": "#dbe6fe",
    "selection_active": "#c7d9fd",
    "focus_ring": "#93b4fd",
    "row_hover": "#f1f5fb",
    "row_alt": "#fafbfd",
    "scrollbar": "#c9d1de",
    "scrollbar_hover": "#aab4c4",
    "shadow": "#1018280f",
}

# ------------------------------------------------------------------ 间距令牌

SPACE = {
    "xs": 4,     # 控件内部贴合
    "sm": 6,     # 同组控件
    "md": 8,     # 表单项之间
    "lg": 12,    # 卡片内的分组之间
    "xl": 16,    # 卡片之间 / 卡片内边距
    "xxl": 20,   # 页面边距
    "xxxl": 24,  # 页面边距（宽屏）
}

# ------------------------------------------------------------------ 圆角令牌

RADIUS = {
    "sm": 4,     # 徽标 / 标签
    "md": 6,     # 输入框 / 普通按钮
    "lg": 8,     # 卡片 / 下拉面板
    "xl": 10,    # 大卡片
    "pill": 12,  # 胶囊
}

# ------------------------------------------------------------------ 字号令牌

FONT_SIZE = {
    "micro": 11,   # 辅助说明、时间戳
    "caption": 12, # 次要信息
    "body": 13,    # 正文
    "body_strong": 14,
    "subtitle": 16,
    "title": 20,
    "display": 26,
}

FONT_FAMILY = (
    '"Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", '
    '"Hiragino Sans GB", "Segoe UI", "Noto Sans CJK SC", sans-serif'
)
MONO_FAMILY = '"Cascadia Mono", "Consolas", "JetBrains Mono", "Courier New", monospace'

# ------------------------------------------------------------------ 尺寸令牌

METRICS = {
    "card_padding": SPACE["xl"],
    "page_margin_h": SPACE["xxxl"],
    "page_margin_v": SPACE["xxl"],
    "card_gap": SPACE["xl"],
    "control_height": 32,
    "control_height_large": 40,
    "primary_button_height": 48,
    "sidebar_width": 208,
    "row_height": 36,
    "header_height": 34,
    "window_min_width": 900,
    "window_min_height": 600,
    "window_min_width_small": 820,
    "window_min_height_small": 540,
}

# ------------------------------------------------------------------ 样式表

QSS = f"""
/* ============================================================ 全局基础 */
/* 注意：这里刻意不给 QWidget 设置 background-color。
   否则 QLabel / 表格单元格等子控件会被刷上底色，卡片里出现灰块；
   页面底色统一由 apply_theme() 设置的 QPalette 决定，卡片再显式覆盖。 */
QWidget {{
    color: {COLORS['text']};
    font-family: {FONT_FAMILY};
    font-size: {FONT_SIZE['body']}px;
}}
QMainWindow, QDialog, QWidget#RootPage {{
    background-color: {COLORS['bg']};
}}
QToolTip {{
    background-color: {COLORS['surface']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
    border-radius: {RADIUS['sm']}px;
    padding: 5px 8px;
    font-size: {FONT_SIZE['caption']}px;
}}
QMenu {{
    background-color: {COLORS['surface']};
    border: 1px solid {COLORS['border']};
    border-radius: {RADIUS['lg']}px;
    padding: {SPACE['xs']}px;
}}
QMenu::item {{
    padding: 6px 24px 6px 12px;
    border-radius: {RADIUS['sm']}px;
}}
QMenu::item:selected {{
    background-color: {COLORS['primary_soft']};
    color: {COLORS['primary_hover']};
}}
QMenu::separator {{
    height: 1px;
    background: {COLORS['border']};
    margin: {SPACE['xs']}px {SPACE['md']}px;
}}

/* ============================================================ 文本层级 */
QLabel[heading="true"] {{
    font-size: {FONT_SIZE['title']}px;
    font-weight: 700;
    letter-spacing: 0.2px;
}}
QLabel[subheading="true"] {{
    font-size: {FONT_SIZE['subtitle']}px;
    font-weight: 600;
}}
QLabel[muted="true"] {{
    color: {COLORS['text_muted']};
    font-size: {FONT_SIZE['caption']}px;
}}
QLabel[micro="true"] {{
    color: {COLORS['text_muted']};
    font-size: {FONT_SIZE['micro']}px;
}}
QLabel[mono="true"] {{
    font-family: {MONO_FAMILY};
    font-size: {FONT_SIZE['caption']}px;
}}

/* ============================================================ 侧边栏 */
#Sidebar {{
    background-color: {COLORS['sidebar']};
    border-right: 1px solid {COLORS['border']};
}}
#SidebarBrand {{
    background-color: {COLORS['sidebar']};
    padding: 0px;
}}
#SidebarTitle {{
    font-size: {FONT_SIZE['subtitle']}px;
    font-weight: 700;
    color: {COLORS['primary_hover']};
    padding: 0px;
}}
#SidebarSub {{
    font-size: {FONT_SIZE['micro']}px;
    color: {COLORS['text_muted']};
    padding: 0px;
}}
QPushButton[navButton="true"] {{
    background-color: transparent;
    border: none;
    border-radius: {RADIUS['lg']}px;
    padding: {SPACE['md']}px {SPACE['lg']}px;
    text-align: left;
    font-size: {FONT_SIZE['body_strong']}px;
    color: {COLORS['text_secondary']};
    min-height: 20px;
}}
QPushButton[navButton="true"]:hover {{
    background-color: {COLORS['sidebar_hover']};
    color: {COLORS['text']};
}}
QPushButton[navButton="true"]:checked {{
    background-color: {COLORS['primary']};
    color: {COLORS['text_on_primary']};
    font-weight: 600;
}}
QPushButton[navButton="true"]:pressed {{
    background-color: {COLORS['primary_hover']};
}}
#SidebarFooter {{
    background-color: {COLORS['surface']};
    border: 1px solid {COLORS['border']};
    border-radius: {RADIUS['lg']}px;
    padding: {SPACE['md']}px {SPACE['lg']}px;
}}
#SidebarFooterLabel {{
    font-size: {FONT_SIZE['micro']}px;
    color: {COLORS['text_secondary']};
}}

/* ============================================================ 卡片 */
#Card {{
    background-color: {COLORS['surface']};
    border: 1px solid {COLORS['border']};
    border-radius: {RADIUS['xl']}px;
}}
#CardFlat {{
    background-color: {COLORS['surface']};
    border: none;
    border-radius: {RADIUS['lg']}px;
}}
#CardTitle {{
    font-size: {FONT_SIZE['body_strong']}px;
    font-weight: 600;
    color: {COLORS['text']};
    padding: 0px;
    background: transparent;
}}
#CardSubtitle {{
    font-size: {FONT_SIZE['micro']}px;
    color: {COLORS['text_muted']};
    padding: 0px;
    background: transparent;
}}
#CardDivider {{
    background-color: {COLORS['border']};
    min-height: 1px;
    max-height: 1px;
}}
#HeroCard {{
    background-color: {COLORS['surface']};
    border: 1px solid {COLORS['border']};
    border-radius: {RADIUS['xl']}px;
}}
#EmptyState {{
    background-color: {COLORS['surface_sunken']};
    border: 1px dashed {COLORS['border_strong']};
    border-radius: {RADIUS['lg']}px;
    padding: {SPACE['xxxl']}px;
}}
/* 仅对命名的容器生效，避免像通配样式那样波及子控件 */
#CardBody, #CellHost {{
    background: transparent;
}}

/* ============================================================ 徽标 */
QLabel[badge="primary"] {{
    background-color: {COLORS['primary_soft']};
    color: {COLORS['primary_hover']};
    border-radius: {RADIUS['sm']}px;
    padding: 2px 8px;
    font-size: {FONT_SIZE['micro']}px;
    font-weight: 600;
}}
QLabel[badge="success"] {{
    background-color: {COLORS['success_soft']};
    color: {COLORS['success']};
    border-radius: {RADIUS['sm']}px;
    padding: 2px 8px;
    font-size: {FONT_SIZE['micro']}px;
    font-weight: 600;
}}
QLabel[badge="warning"] {{
    background-color: {COLORS['warning_soft']};
    color: {COLORS['warning']};
    border-radius: {RADIUS['sm']}px;
    padding: 2px 8px;
    font-size: {FONT_SIZE['micro']}px;
    font-weight: 600;
}}
QLabel[badge="danger"] {{
    background-color: {COLORS['danger_soft']};
    color: {COLORS['danger']};
    border-radius: {RADIUS['sm']}px;
    padding: 2px 8px;
    font-size: {FONT_SIZE['micro']}px;
    font-weight: 600;
}}
QLabel[badge="neutral"] {{
    background-color: {COLORS['surface_sunken']};
    color: {COLORS['text_secondary']};
    border-radius: {RADIUS['sm']}px;
    padding: 2px 8px;
    font-size: {FONT_SIZE['micro']}px;
    font-weight: 600;
}}

/* ============================================================ 按钮 */
QPushButton {{
    background-color: {COLORS['surface']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border_strong']};
    border-radius: {RADIUS['md']}px;
    padding: 6px 14px;
    min-height: 18px;
    font-size: {FONT_SIZE['body']}px;
}}
QPushButton:hover {{
    background-color: {COLORS['surface_sunken']};
    border-color: {COLORS['primary']};
    color: {COLORS['primary_hover']};
}}
QPushButton:pressed {{
    background-color: {COLORS['selection']};
    border-color: {COLORS['primary_hover']};
}}
QPushButton:disabled {{
    background-color: {COLORS['surface_sunken']};
    color: {COLORS['text_disabled']};
    border-color: {COLORS['border']};
}}
QPushButton:default, QPushButton[autosave="true"] {{
    border-color: {COLORS['primary']};
}}

QPushButton[primary="true"] {{
    background-color: {COLORS['primary']};
    color: {COLORS['text_on_primary']};
    border: 1px solid {COLORS['primary']};
    font-size: {FONT_SIZE['subtitle']}px;
    font-weight: 600;
    border-radius: {RADIUS['lg']}px;
    padding: 12px 22px;
}}
QPushButton[primary="true"]:hover {{
    background-color: {COLORS['primary_hover']};
    border-color: {COLORS['primary_hover']};
    color: {COLORS['text_on_primary']};
}}
QPushButton[primary="true"]:pressed {{
    background-color: {COLORS['primary_pressed']};
    border-color: {COLORS['primary_pressed']};
}}
QPushButton[primary="true"]:disabled {{
    background-color: #dce3f2;
    border-color: #dce3f2;
    color: #8d99ae;
}}

QPushButton[accent="true"] {{
    background-color: {COLORS['primary_soft']};
    color: {COLORS['primary_hover']};
    border-color: #c7d9fd;
    font-weight: 500;
}}
QPushButton[accent="true"]:hover {{
    background-color: {COLORS['selection']};
    border-color: {COLORS['primary']};
}}
QPushButton[accent="true"]:disabled {{
    background-color: {COLORS['surface_sunken']};
    color: {COLORS['text_disabled']};
    border-color: {COLORS['border']};
}}

QPushButton[danger="true"] {{
    color: {COLORS['danger']};
    border-color: #eab6b6;
}}
QPushButton[danger="true"]:hover {{
    background-color: {COLORS['danger_soft']};
    border-color: {COLORS['danger']};
    color: {COLORS['danger']};
}}
QPushButton[danger="true"]:pressed {{
    background-color: #f8d4d4;
}}
QPushButton[danger="true"]:disabled {{
    background-color: {COLORS['surface_sunken']};
    color: {COLORS['text_disabled']};
    border-color: {COLORS['border']};
}}

QPushButton[quiet="true"] {{
    background-color: transparent;
    border: 1px solid transparent;
    color: {COLORS['text_secondary']};
}}
QPushButton[quiet="true"]:hover {{
    background-color: {COLORS['surface_sunken']};
    border-color: {COLORS['border']};
    color: {COLORS['text']};
}}
QPushButton[quiet="true"]:disabled {{
    color: {COLORS['text_disabled']};
    background-color: transparent;
    border-color: transparent;
}}

QPushButton[busy="true"] {{
    background-color: {COLORS['surface_sunken']};
    color: {COLORS['text_muted']};
    border-color: {COLORS['border']};
}}
QPushButton[busy="true"][primary="true"] {{
    background-color: #9db5ee;
    border-color: #9db5ee;
    color: {COLORS['surface']};
}}
QPushButton[inTableCell="true"] {{
    padding: 3px 10px;
    font-size: {FONT_SIZE['caption']}px;
    border-radius: {RADIUS['sm']}px;
}}

/* ============================================================ 输入控件 */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit, QDateEdit {{
    background-color: {COLORS['surface']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border_strong']};
    border-radius: {RADIUS['md']}px;
    padding: 6px 10px;
    min-height: 18px;
    selection-background-color: {COLORS['selection']};
    selection-color: {COLORS['text']};
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QTextEdit:hover, QPlainTextEdit:hover,
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border-color: {COLORS['primary']};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {{
    background-color: {COLORS['surface_sunken']};
    color: {COLORS['text_disabled']};
    border-color: {COLORS['border']};
}}
QLineEdit[invalid="true"] {{
    border-color: {COLORS['danger']};
    background-color: {COLORS['danger_soft']};
}}
QLineEdit::placeholder, QTextEdit::placeholder, QPlainTextEdit::placeholder {{
    color: {COLORS['text_disabled']};
}}
QComboBox {{
    padding-right: 6px;
}}
/* 数字输入框右侧留出上下翻按钮的位置，避免箭头被圆角裁掉 */
QSpinBox, QDoubleSpinBox {{
    padding-right: 24px;
}}
QComboBox QAbstractItemView {{
    background-color: {COLORS['surface']};
    border: 1px solid {COLORS['border']};
    border-radius: {RADIUS['md']}px;
    padding: {SPACE['xs']}px;
    outline: 0;
}}
QComboBox QAbstractItemView::item {{
    padding: 6px 8px;
    border-radius: {RADIUS['sm']}px;
    min-height: 18px;
}}
QComboBox QAbstractItemView::item:hover {{
    background-color: {COLORS['row_hover']};
}}
QComboBox QAbstractItemView::item:selected {{
    background-color: {COLORS['primary_soft']};
    color: {COLORS['primary_hover']};
}}

/* ============================================================ 复选 / 单选 */
QCheckBox, QRadioButton {{
    spacing: {SPACE['md']}px;
    padding: 2px 0px;
}}
QCheckBox:hover, QRadioButton:hover {{
    color: {COLORS['primary_hover']};
}}
QCheckBox:disabled, QRadioButton:disabled {{
    color: {COLORS['text_disabled']};
}}
/* ============================================================ 表格 / 列表 */
QTableWidget, QTableView, QTreeWidget, QListWidget {{
    background-color: {COLORS['surface']};
    border: 1px solid {COLORS['border']};
    border-radius: {RADIUS['lg']}px;
    gridline-color: {COLORS['border']};
    selection-background-color: {COLORS['selection']};
    selection-color: {COLORS['text']};
    alternate-background-color: {COLORS['row_alt']};
    outline: 0;
}}
QTableWidget::item, QTableView::item, QTreeWidget::item, QListWidget::item {{
    padding: 0px {SPACE['md']}px;
}}
QTableWidget::item:hover, QTableView::item:hover, QTreeWidget::item:hover {{
    background-color: {COLORS['row_hover']};
}}
QTableWidget::item:selected, QTableView::item:selected,
QTreeWidget::item:selected, QListWidget::item:selected {{
    background-color: {COLORS['selection']};
    color: {COLORS['text']};
}}
QTableWidget::item:selected:!active, QTableView::item:selected:!active {{
    background-color: {COLORS['selection_active']};
}}
QTableView QTableCornerButton::section {{
    background-color: {COLORS['surface_alt']};
    border: none;
    border-right: 1px solid {COLORS['border']};
    border-bottom: 1px solid {COLORS['border']};
}}
QHeaderView::section {{
    background-color: {COLORS['surface_alt']};
    color: {COLORS['text_secondary']};
    border: none;
    border-right: 1px solid {COLORS['border']};
    border-bottom: 1px solid {COLORS['border']};
    padding: {SPACE['md']}px 10px;
    font-weight: 600;
    font-size: {FONT_SIZE['caption']}px;
}}
QHeaderView::section:hover {{
    background-color: {COLORS['surface_sunken']};
}}
QHeaderView::section:first {{
    border-top-left-radius: {RADIUS['lg']}px;
}}
QTableWidget QWidget[inTableCell="true"] {{
    background-color: transparent;
}}

/* ============================================================ 分组 / 标签页 */
QGroupBox {{
    border: 1px solid {COLORS['border']};
    border-radius: {RADIUS['lg']}px;
    margin-top: 16px;
    padding: {SPACE['lg']}px {SPACE['lg']}px {SPACE['md']}px {SPACE['lg']}px;
    background-color: {COLORS['surface']};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: {SPACE['lg']}px;
    padding: 0px {SPACE['sm']}px;
    font-weight: 600;
    color: {COLORS['text']};
}}
QTabWidget::pane {{
    border: 1px solid {COLORS['border']};
    border-top: none;
    border-bottom-left-radius: {RADIUS['lg']}px;
    border-bottom-right-radius: {RADIUS['lg']}px;
    background-color: {COLORS['surface']};
}}
QTabWidget::tab-bar {{
    left: 0px;
}}
QTabBar {{
    qproperty-drawBase: 0;
    border: none;
}}
QTabBar::tab {{
    background-color: transparent;
    color: {COLORS['text_secondary']};
    border: none;
    border-bottom: 2px solid transparent;
    padding: {SPACE['md']}px {SPACE['xl']}px;
    margin-right: {SPACE['xs']}px;
    font-size: {FONT_SIZE['body_strong']}px;
}}
QTabBar::tab:hover {{
    color: {COLORS['primary_hover']};
    background-color: {COLORS['surface_alt']};
    border-top-left-radius: {RADIUS['md']}px;
    border-top-right-radius: {RADIUS['md']}px;
}}
QTabBar::tab:selected {{
    color: {COLORS['primary_hover']};
    font-weight: 600;
    border-bottom: 2px solid {COLORS['primary']};
    background-color: {COLORS['surface']};
}}
QTabBar::tab:disabled {{
    color: {COLORS['text_disabled']};
}}

/* ============================================================ 进度 / 滑块 */
QProgressBar {{
    background-color: {COLORS['surface_sunken']};
    border: 1px solid {COLORS['border']};
    border-radius: {RADIUS['md']}px;
    height: 16px;
    text-align: center;
    color: {COLORS['text_secondary']};
    font-size: {FONT_SIZE['caption']}px;
}}
QProgressBar::chunk {{
    background-color: {COLORS['primary']};
    border-radius: {RADIUS['sm']}px;
}}
QProgressBar[state="success"]::chunk {{
    background-color: {COLORS['success']};
}}
QProgressBar[state="danger"]::chunk {{
    background-color: {COLORS['danger']};
}}
QSlider::groove:horizontal {{
    height: 5px;
    background: {COLORS['surface_sunken']};
    border-radius: 3px;
}}
QSlider::sub-page:horizontal {{
    background: {COLORS['primary']};
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    width: 14px;
    height: 14px;
    margin: -5px 0px;
    border-radius: 7px;
    background: {COLORS['primary']};
}}
QSlider::handle:horizontal:hover {{
    background: {COLORS['primary_hover']};
}}

/* ============================================================ 滚动条 */
QScrollArea {{
    background-color: transparent;
    border: none;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0px;
}}
QScrollBar::handle:vertical {{
    background: {COLORS['scrollbar']};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {COLORS['scrollbar_hover']};
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
}}
QScrollBar::handle:horizontal {{
    background: {COLORS['scrollbar']};
    border-radius: 5px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {COLORS['scrollbar_hover']};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0px;
    width: 0px;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

/* ============================================================ 状态栏 / 分隔线 */
QStatusBar {{
    background-color: {COLORS['statusbar']};
    border-top: 1px solid {COLORS['border']};
    color: {COLORS['text_secondary']};
    font-size: {FONT_SIZE['caption']}px;
}}
QStatusBar::item {{
    border: none;
}}
QStatusBar QLabel {{
    background-color: transparent;
    color: {COLORS['text_secondary']};
    padding: 2px 6px;
}}
QFrame#HLine {{
    background-color: {COLORS['border']};
    min-height: 1px;
    max-height: 1px;
    border: none;
}}
QSplitter::handle {{
    background-color: {COLORS['border']};
}}
"""


# ------------------------------------------------------------------ 字体


def body_font(size: int | None = None, bold: bool = False) -> QFont:
    """按令牌生成界面字体。"""
    font = QFont()
    font.setFamilies(
        [
            "Microsoft YaHei UI",
            "Microsoft YaHei",
            "PingFang SC",
            "Hiragino Sans GB",
            "Segoe UI",
            "Noto Sans CJK SC",
        ]
    )
    font.setPixelSize(size or FONT_SIZE["body"])
    font.setBold(bold)
    return font


def mono_font(size: int | None = None) -> QFont:
    font = QFont()
    font.setFamilies(["Cascadia Mono", "Consolas", "JetBrains Mono", "Courier New"])
    font.setPixelSize(size or FONT_SIZE["caption"])
    return font


_TRANSLATOR = None
_SPIN_RULES = ""


def _spin_arrow_rules() -> str:
    """用自绘的小箭头替换 Fusion 在缩放屏上过小的数字框箭头。"""
    try:
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QColor, QImage, QPainter, QPen

        from ..core import paths as P

        folder = P.launcher_root() / "icons"
        folder.mkdir(parents=True, exist_ok=True)
        paths = {}
        for name, up in (("spin-up.png", True), ("spin-down.png", False)):
            path = folder / name
            image = QImage(24, 24, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            pen = QPen(QColor(COLORS["text_secondary"]))
            pen.setWidthF(3.2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            if up:
                painter.drawPolyline([QPointF(7, 15), QPointF(12, 9.5), QPointF(17, 15)])
            else:
                painter.drawPolyline([QPointF(7, 9.5), QPointF(12, 15), QPointF(17, 9.5)])
            painter.end()
            image.save(str(path))
            paths[name] = str(path).replace("\\", "/")

        up_url, down_url = paths["spin-up.png"], paths["spin-down.png"]
        return f"""
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 20px;
    margin: 1px 1px 0px 0px;
    background: transparent;
    border: none;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 20px;
    margin: 0px 1px 1px 0px;
    background: transparent;
    border: none;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {COLORS['surface_sunken']};
    border-radius: 3px;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url("{up_url}");
    width: 12px;
    height: 12px;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url("{down_url}");
    width: 12px;
    height: 12px;
}}
"""
    except Exception:  # noqa: BLE001  # 生成失败就退回原生箭头
        return ""


def install_translations(app) -> bool:
    """加载 Qt 自带的中文翻译，让标准按钮/对话框（保存、取消、是/否…）显示中文。"""
    global _TRANSLATOR
    try:
        from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator

        path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
        translator = QTranslator(app)
        if translator.load("qtbase_zh_CN", path):
            app.installTranslator(translator)
            _TRANSLATOR = translator  # 持有引用，避免被回收
            return True
        _ = QLocale.system().name()
    except Exception:  # noqa: BLE001
        pass
    return False


def apply_theme(app) -> None:
    """把设计系统与样式表装到 QApplication 上（保持向后兼容的入口）。"""
    try:
        app.setStyle("Fusion")
    except Exception:  # noqa: BLE001  # 主题不可用时退回系统风格
        pass

    install_translations(app)

    # 用调色板承载「页面底色 / 输入框底色」等基础色：
    # 这样不需要给 QWidget 写通配背景色，卡片里的子控件自然保持透明。
    from PySide6.QtGui import QColor, QPalette

    palette = QPalette()
    window = QColor(COLORS["bg"])
    surface = QColor(COLORS["surface"])
    text = QColor(COLORS["text"])
    palette.setColor(QPalette.ColorRole.Window, window)
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, surface)
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(COLORS["row_alt"]))
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.Button, surface)
    palette.setColor(QPalette.ColorRole.ButtonText, text)
    palette.setColor(QPalette.ColorRole.Highlight, QColor(COLORS["primary"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, surface)
    palette.setColor(QPalette.ColorRole.ToolTipText, text)
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(COLORS["text_disabled"]))
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(COLORS["text_disabled"]))
    app.setPalette(palette)

    app.setFont(body_font(FONT_SIZE["body"]))
    global _SPIN_RULES
    _SPIN_RULES = _spin_arrow_rules()
    app.setStyleSheet(QSS + _SPIN_RULES)
