"""应用图标渲染与图标文件生成（桌面快捷方式 / 窗口图标共用同一套视觉）。

Logo 构成：圆角渐变底板 + 白色等轴立方体（三面明暗），纯 Qt 绘制，
不依赖 QtSvg / Pillow，可在任意尺寸矢量级重绘，因此 16px 与 256px 都保持清晰。

生成的格式：

* Windows —— `.ico`（内含 16/24/32/48/64/128/256 多尺寸，PNG 压缩）
* macOS   —— `.icns`（icp4/icp5/icp6/ic11/ic12/ic07/ic08/ic09）
* Linux   —— `.png`（16…256，按 hicolor 规范命名）
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from PySide6.QtCore import QBuffer, QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QGradient,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
)

# ------------------------------------------------------------------ 视觉常量

BRAND_TOP = QColor("#2b62ec")
BRAND_BOTTOM = QColor("#0f1f66")
FACE_TOP = QColor("#ffffff")
FACE_LEFT = QColor("#cddfff")
FACE_RIGHT = QColor("#7ba6ff")
EDGE = QColor("#ffffff")

PNG_SIZES = (16, 24, 32, 48, 64, 128, 256)
WIN_SIZES = (16, 24, 32, 48, 64, 128, 256)
MAC_PNG_TYPES = ((16, "icp4"), (32, "icp5"), (64, "icp6"), (128, "ic07"), (256, "ic08"))
MAC_HIDPI_TYPES = ((512, "ic09"),)

ICON_BASENAME = "pylauncher"

# 立方体在底板中的占比：留白让 16px 下轮廓依然清晰
CUBE_SCALE = 0.84


# ------------------------------------------------------------------ 绘制


def render_logo(size: int, *, board_opacity: float = 1.0) -> QImage:
    """渲染 `size × size` 的 logo 位图（透明背景）。"""
    size = max(8, int(size))
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.setOpacity(board_opacity)

    # ——— 底板
    radius = 0.22 * size
    board = QRectF(0, 0, size, size)
    gradient = QLinearGradient(QPointF(size * 0.15, 0), QPointF(size * 0.85, size))
    gradient.setSpread(QGradient.Spread.PadSpread)
    gradient.setColorAt(0.0, BRAND_TOP)
    gradient.setColorAt(1.0, BRAND_BOTTOM)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(gradient)
    painter.drawRoundedRect(board, radius, radius, Qt.SizeMode.RelativeSize)

    # ——— 内描边（浅色高光，提升小尺寸下的轮廓辨识度）
    inset = max(1.0, size * 0.012)
    hairline = QPen(EDGE)
    hairline.setWidthF(max(1.0, size * 0.02))
    hairline.setColor(QColor(255, 255, 255, 70))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(hairline)
    painter.drawRoundedRect(
        QRectF(inset, inset, size - 2 * inset, size - 2 * inset),
        radius - inset,
        radius - inset,
        Qt.SizeMode.RelativeSize,
    )

    # ——— 立方体（坐标以 64×64 画布定义，按实际尺寸缩放并居中留边）
    s = size / 64.0 * CUBE_SCALE
    offset = (size - 64.0 * s) / 2

    def pt(x: float, y: float) -> QPointF:
        return QPointF(offset + x * s, offset + y * s)

    top = QPolygonF([pt(32, 11.5), pt(53, 22.5), pt(32, 33.5), pt(11, 22.5)])
    left = QPolygonF([pt(11, 22.5), pt(32, 33.5), pt(32, 53.5), pt(11, 42.5)])
    right = QPolygonF([pt(53, 22.5), pt(53, 42.5), pt(32, 53.5), pt(32, 33.5)])

    painter.setPen(Qt.PenStyle.NoPen)
    for polygon, color in ((top, FACE_TOP), (left, FACE_LEFT), (right, FACE_RIGHT)):
        painter.setBrush(color)
        painter.drawPolygon(polygon)

    painter.end()
    return image


def app_icon(sizes: Iterable[int] = (16, 24, 32, 48, 64, 128, 256)) -> QIcon:
    """给窗口 / 对话框使用的多分辨率图标。"""
    icon = QIcon()
    for size in sizes:
        icon.addPixmap(QPixmap.fromImage(render_logo(size)))
    return icon


def dot_icon(color: str, size: int = 12) -> QIcon:
    """列表项前面的小圆点（用于区分账户类型等）。"""
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    inset = max(1.0, size * 0.12)
    painter.drawEllipse(QRectF(inset, inset, size - 2 * inset, size - 2 * inset))
    painter.end()
    return QIcon(QPixmap.fromImage(image))


# ------------------------------------------------------------------ 字节处理


def _png(size: int) -> bytes:
    blob = QByteArray()
    buffer = QBuffer(blob)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    ok = render_logo(size).save(buffer, "PNG")
    buffer.close()
    if not ok:
        raise OSError(f"PNG 渲染失败（尺寸 {size}）")
    return bytes(blob.data())


def _png_size(data: bytes) -> Tuple[int, int]:
    """从 PNG 头里读出宽高。"""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是合法的 PNG 数据")
    width, height = struct.unpack(">II", data[16:24])
    return width, height


# ------------------------------------------------------------------ ICO


def build_ico(sizes: Iterable[int] = WIN_SIZES) -> bytes:
    """把多尺寸 PNG 打包成 Windows `.ico`。

    ICO 容器允许每个条目独立存放 PNG 数据（Vista 起支持），
    相比传统 BMP 条目体积更小且带完整 alpha 通道。
    """
    entries: List[Tuple[int, bytes]] = []
    for size in sizes:
        data = _png(int(size))
        entries.append((int(size), data))

    count = len(entries)
    header = struct.pack("<HHH", 0, 1, count)
    offset = 6 + 16 * count
    directory = b""
    payload = b""
    for size, data in entries:
        w = 0 if size >= 256 else size
        directory += struct.pack(
            "<BBBBHHII",
            w,                 # bWidth（256 记为 0）
            w,                 # bHeight
            0,                 # bColorCount
            0,                 # bReserved
            1,                 # wPlanes
            32,                # wBitCount
            len(data),         # dwBytesInRes
            offset + len(payload),
        )
        payload += data
    return header + directory + payload


# ------------------------------------------------------------------ ICNS


def build_icns() -> bytes:
    """把多尺寸 PNG 打包成 macOS `.icns`（全部使用 PNG 条目）。"""
    chunks: List[Tuple[str, bytes]] = []
    for size, kind in MAC_PNG_TYPES + MAC_HIDPI_TYPES:
        chunks.append((kind, _png(size)))

    body = b""
    for kind, data in chunks:
        body += kind.encode("ascii") + struct.pack(">I", len(data) + 8) + data
    return b"icns" + struct.pack(">I", len(body) + 8) + body


# ------------------------------------------------------------------ 资产输出


def write_icon_assets(
    dest_dir: Path,
    *,
    force: bool = False,
    sizes: Iterable[int] = PNG_SIZES,
) -> Dict[str, Path]:
    """把图标资产写入 `dest_dir`，返回各类文件路径。

    * Windows：`icon-kit/pylauncher.ico`
    * macOS  ：`icon-kit/pylauncher.icns`
    * Linux  ：`icon-kit/hicolor/<size>x<size>/apps/pylauncher.png`
    """
    dest_dir = Path(dest_dir)
    kit = dest_dir / "icon-kit"
    kit.mkdir(parents=True, exist_ok=True)

    result: Dict[str, Path] = {}

    # 通用 PNG 集合
    png_paths: Dict[int, Path] = {}
    for size in sizes:
        path = kit / f"{ICON_BASENAME}-{int(size)}.png"
        if force or not path.is_file():
            path.write_bytes(_png(int(size)))
        png_paths[int(size)] = path
    result["png"] = png_paths[max(png_paths)]

    # hicolor 规范目录（Linux）
    for size, path in png_paths.items():
        target_dir = kit / "hicolor" / f"{size}x{size}" / "apps"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{ICON_BASENAME}.png"
        if force or not target.is_file():
            target.write_bytes(path.read_bytes())
        if size == 256:
            result["png_hicolor"] = target

    # Windows
    ico_path = kit / f"{ICON_BASENAME}.ico"
    if force or not ico_path.is_file():
        ico_path.write_bytes(build_ico())
    result["ico"] = ico_path

    # macOS
    icns_path = kit / f"{ICON_BASENAME}.icns"
    if force or not icns_path.is_file():
        icns_path.write_bytes(build_icns())
    result["icns"] = icns_path

    return result


# ------------------------------------------------------------------ 默认目录


def default_icon_dir() -> Path:
    """图标资产默认存放位置（启动器数据目录下的 icons/）。"""
    from ..core import paths as P

    return P.launcher_root() / "icons"
