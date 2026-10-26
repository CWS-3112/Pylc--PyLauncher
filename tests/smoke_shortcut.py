"""桌面快捷方式端到端测试（真实写入临时目录，不会碰你的桌面）。

    python tests/smoke_shortcut.py

覆盖：图标资产格式、.lnk 落盘与回读、覆盖/删除、降级脚本、错误分类。
"""

from __future__ import annotations

import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from launcher.core import shortcut  # noqa: E402
from launcher.core.shortcut import (  # noqa: E402
    ShortcutError,
    ShortcutSpec,
    create_shortcut,
    existing_target,
    find_existing_shortcut,
    remove_shortcut,
)

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  [OK]   {name}")
    else:
        failures.append(name)
        print(f"  [FAIL] {name}  {detail}")


def make_spec(tmp: Path, icon: Path | None) -> ShortcutSpec:
    return ShortcutSpec(
        name="PyLauncher",
        description="PyLauncher 测试用快捷方式",
        executable=Path(sys.executable),
        arguments=[str(Path(__file__).resolve()), "--selftest"],
        working_dir=tmp,
        icon=icon,
    )


def test_icons(tmp: Path) -> Path:
    from PySide6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication([])
    from launcher.ui.icons import _png_size, write_icon_assets

    assets = write_icon_assets(tmp, force=True)
    print("\n== 图标资产 ==")
    check("PNG 已生成", assets["png"].is_file())
    check("PNG 尺寸为 256", _png_size(assets["png"].read_bytes()) == (256, 256), str(_png_size(assets["png"].read_bytes())))

    ico = assets["ico"].read_bytes()
    count = struct.unpack("<H", ico[4:6])[0]
    sizes = [(ico[6 + i * 16] or 256) for i in range(count)]
    check("ICO 头合法", ico[:4] == b"\x00\x00\x01\x00", ico[:4].hex())
    check("ICO 含 7 个尺寸", count == 7, str(count))
    check("ICO 含 16/32/256", {16, 32, 256} <= set(sizes), str(sizes))

    icns = assets["icns"].read_bytes()
    total = struct.unpack(">I", icns[4:8])[0]
    check("ICNS 头合法", icns[:4] == b"icns")
    check("ICNS 长度自洽", total == len(icns), f"{total} vs {len(icns)}")
    check("hicolor PNG 存在", assets["png_hicolor"].is_file())
    return assets[shortcut.icon_asset_key()]


def test_create(tmp: Path, icon: Path) -> Path:
    print("\n== 创建 / 回读 ==")
    target = tmp / "desktop"
    target.mkdir()
    spec = make_spec(tmp, icon)
    result = create_shortcut(spec, dest_dir=target)
    check("创建成功", result.ok, result.message)
    check("产物存在", bool(result.path and result.path.exists()), str(result.path))
    check("平台类型正确", result.kind == {"windows": "lnk", "linux": "desktop", "macos": "app"}[shortcut.platform_kind()], result.kind)
    check("非降级", not result.degraded)

    if shortcut.platform_kind() == "windows":
        info = shortcut._windows_lnk_read(result.path)
        check("目标指向解释器", Path(info["target"]).resolve() == Path(sys.executable).resolve(), info["target"])
        check("参数包含 --selftest", "--selftest" in info["arguments"], info["arguments"])
        check("起始位置正确", Path(info["working_dir"]).resolve() == tmp.resolve(), info["working_dir"])
        check("描述写入", "PyLauncher" in info["description"], info["description"])
        check("图标指向 .ico", info["icon"].lower().endswith(".ico"), info["icon"])
    elif shortcut.platform_kind() == "linux":
        text = result.path.read_text(encoding="utf-8")
        check("Name 写入", "Name=PyLauncher" in text)
        check("Exec 含脚本", "--selftest" in text)
        check("可执行位", bool(result.path.stat().st_mode & 0o111))
    else:
        check("Info.plist 存在", (result.path / "Contents" / "Info.plist").is_file())
        check("AppIcon 存在", (result.path / "Contents" / "Resources" / "AppIcon.icns").is_file())

    # 重复创建
    try:
        create_shortcut(spec, dest_dir=target)
        check("重复创建应报 exists", False)
    except ShortcutError as exc:
        check("重复创建报 exists", exc.reason == ShortcutError.EXISTS, exc.reason)

    # 覆盖
    result2 = create_shortcut(spec, dest_dir=target, overwrite=True)
    check("覆盖成功", result2.ok and result2.overwritten, f"{result2.ok}/{result2.overwritten}")
    check("记录旧目标", bool(result2.previous_target), result2.previous_target)

    # 查找 / 删除
    check("能找到已创建项", find_existing_shortcut("PyLauncher", target) is not None)
    removed = remove_shortcut(result2.path)
    check("删除成功", removed.ok and not result2.path.exists())
    check("目标可读", bool(existing_target(result2.path)) is False)
    return result2.path


def test_fallback(tmp: Path, icon: Path) -> None:
    print("\n== 降级与错误分类 ==")
    target = tmp / "desktop"
    spec = make_spec(tmp, icon)

    original = shortcut._windows_lnk_write if shortcut.platform_kind() == "windows" else None
    if original is not None:
        def boom(dest, spec):  # noqa: ANN001
            raise ShortcutError("模拟 Shell 组件不可用", reason=ShortcutError.SYSTEM_UNAVAILABLE)

        shortcut._windows_lnk_write = boom
    try:
        result = create_shortcut(spec, dest_dir=target)
    finally:
        if original is not None:
            shortcut._windows_lnk_write = original

    check("自动降级", result.degraded, result.message)
    check("降级产物存在", bool(result.path and result.path.exists()), str(result.path))
    if result.path:
        text = result.path.read_text(encoding="utf-8", errors="ignore")
        check("降级脚本含启动命令", "main.py" in text or "--selftest" in text or "selftest" in text)
        result.path.unlink()

    # 目标缺失
    bad = ShortcutSpec(name="Ghost", description="x", executable=Path("Z:/definitely/missing.exe"))
    try:
        create_shortcut(bad, dest_dir=target)
        check("目标缺失应报错", False)
    except ShortcutError as exc:
        check("目标缺失报 target-missing", exc.reason == ShortcutError.TARGET_MISSING, exc.reason)

    # 目录不可写（用一个文件冒充目录）
    file_as_dir = tmp / "not-a-dir"
    file_as_dir.write_text("x", encoding="utf-8")
    try:
        create_shortcut(spec, dest_dir=file_as_dir)
        check("不可写应报错", False)
    except ShortcutError as exc:
        check("不可写报 permission-denied", exc.reason == ShortcutError.PERMISSION_DENIED, exc.reason)


def main() -> int:
    print(f"平台：{shortcut.platform_kind()}（{shortcut.platform_label()}）")
    tmp = Path(tempfile.mkdtemp(prefix="pl-shortcut-"))
    icon = test_icons(tmp)
    test_create(tmp, icon)
    test_fallback(tmp, icon)

    print("\n" + "=" * 60)
    if failures:
        print(f"快捷方式测试失败 {len(failures)} 项：{', '.join(failures)}")
        return 1
    print("快捷方式测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
