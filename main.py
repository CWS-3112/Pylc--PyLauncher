#!/usr/bin/env python3
"""PyLauncher 入口。

    python main.py                      启动图形界面
    python main.py --selftest           运行核心自检（无需界面与网络）
    python main.py --game-dir D:/mc     指定游戏目录
    python main.py --launcher-home D:/pl 指定启动器数据目录（绿色便携模式）
    python main.py --create-shortcut    创建桌面快捷方式后退出（可配合 --shortcut-dir）
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

# 保证直接运行时能找到 launcher 包
sys.path.insert(0, str(Path(__file__).resolve().parent))

from launcher import LAUNCHER_NAME, LAUNCHER_VERSION  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pylauncher",
        description=f"{LAUNCHER_NAME} —— 模块化的《我的世界》Java 版启动器",
    )
    parser.add_argument("--version", action="version", version=f"{LAUNCHER_NAME} {LAUNCHER_VERSION}")
    parser.add_argument("--launcher-home", help="启动器数据目录（config.json / accounts.json 存放处，默认为当前工作目录）")
    parser.add_argument("--game-dir", help="游戏目录（覆盖配置中的值）")
    parser.add_argument("--mirror", choices=["official", "bmclapi"], help="下载源（覆盖配置中的值）")
    parser.add_argument("--selftest", action="store_true", help="运行核心自检后退出")
    parser.add_argument("--no-gui", action="store_true", help="仅初始化核心，不打开界面")
    parser.add_argument("--create-shortcut", action="store_true", help="创建桌面快捷方式后退出")
    parser.add_argument("--shortcut-dir", help="快捷方式保存目录（默认：系统桌面）")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的同名校验方式")
    return parser


def _create_shortcut(args, config) -> int:
    """命令行创建桌面快捷方式（也可用于脚本化部署）。"""
    from launcher.core.shortcut import ShortcutError
    from launcher.ui import shortcut_flow

    try:
        result = shortcut_flow.create(
            config,
            dest_dir=Path(args.shortcut_dir) if args.shortcut_dir else None,
            overwrite=args.force,
        )
    except ShortcutError as exc:
        print(f"[失败] {exc.message}", file=sys.stderr)
        if exc.detail:
            print(f"       详情：{exc.detail}", file=sys.stderr)
        return 1

    print(f"[完成] {result.message}")
    print(f"       类型：{result.kind}{'（降级方案）' if result.degraded else ''}")
    if result.path:
        print(f"       位置：{result.path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.launcher_home:
        from launcher.core import paths

        paths.set_launcher_root(Path(args.launcher_home).resolve())

    from launcher.core.config import get_config

    config = get_config()
    if args.game_dir:
        config.set("game_dir", str(Path(args.game_dir).resolve()))
    if args.mirror:
        config.set("mirror", args.mirror)

    if args.selftest:
        from launcher.core.selftest import run_selftest

        return run_selftest()

    if args.create_shortcut:
        return _create_shortcut(args, config)

    if args.no_gui:
        from launcher.core.utils import LOG, setup_logging

        setup_logging()
        LOG.info("核心初始化完成：%s", config.path)
        LOG.info("游戏目录：%s", config.get("game_dir"))
        return 0

    from launcher.ui.app import run

    return run(config)


def _report_crash() -> None:
    """崩溃兜底：用 pythonw 启动时没有控制台，必须留下可见的错误信息。"""
    detail = traceback.format_exc()
    try:
        from launcher.core import paths as P

        log_file = P.logs_dir() / "crash.log"
        with open(log_file, "a", encoding="utf-8") as fh:
            fh.write(detail + "\n")
    except Exception:  # noqa: BLE001
        log_file = None

    print(detail, file=sys.stderr)
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication(sys.argv[:1])
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle(f"{LAUNCHER_NAME} 启动失败")
        box.setText("启动器遇到了未处理的错误，无法继续运行。")
        box.setInformativeText(f"详细信息已写入日志：{log_file}" if log_file else "请查看控制台输出。")
        box.setDetailedText(detail)
        box.exec()
        _ = app
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        _report_crash()
        sys.exit(1)
