"""端到端测试：真实下载安装一个版本，构建命令并启动游戏进程。

    python tests/e2e_launch.py [version] [seconds]

会真的下载游戏文件、真的启动《我的世界》窗口，运行 `seconds` 秒后结束进程。
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from launcher.core.auth import create_offline_account  # noqa: E402
from launcher.core.config import Config, Instance  # noqa: E402
from launcher.core.constants import BMCLAPI  # noqa: E402
from launcher.core.http import DownloadManager  # noqa: E402
from launcher.core.installer import install_version  # noqa: E402
from launcher.core.java import resolve_java  # noqa: E402
from launcher.core.launcher_core import (  # noqa: E402
    LaunchOptions,
    build_command,
    command_to_string,
    launch,
    resolve_version,
)
from launcher.core.utils import setup_logging  # noqa: E402

VERSION = sys.argv[1] if len(sys.argv) > 1 else "1.20.4"
SECONDS = int(sys.argv[2]) if len(sys.argv) > 2 else 20


def main() -> int:
    setup_logging()
    tmp = Path(tempfile.mkdtemp(prefix="pylauncher-e2e-"))
    game_dir = tmp / "game"
    game_dir.mkdir(parents=True)

    print(f"[1/5] 安装 {VERSION}（含资源文件，请耐心等待）…")
    stats_box = {"files": 0}

    def on_progress(stage: str, done: int, total: int) -> None:
        stats_box["files"] = total
        if total and (done % max(1, total // 20) == 0 or done == total):
            print(f"       {stage}: {done}/{total}")

    ok = install_version(
        game_dir, VERSION, BMCLAPI,
        with_assets=True,
        download_manager=DownloadManager(10),
        on_stage=lambda s: print(f"       → {s}"),
        on_progress=on_progress,
    )
    if not ok:
        print("安装失败")
        return 1
    print(f"       共处理 {stats_box['files']} 个文件")

    print("[2/5] 准备账户与实例…")
    cfg = Config(tmp / "config.json")
    cfg.set("game_dir", str(game_dir))
    account = create_offline_account("PyLauncher")
    instance = Instance(version_id=VERSION, isolated=True, max_memory_mb=2048)

    resolved = resolve_version(game_dir, VERSION, instance)
    missing = [p for p in resolved.classpath if not p.is_file()]
    print(f"       classpath 条目 {len(resolved.classpath)} 个，缺失 {len(missing)} 个")
    natives = sorted(f.name for f in resolved.natives_dir.iterdir() if f.is_file())
    print(f"       natives 目录：{len(natives)} 个文件 → {natives[:5]}")

    java = resolve_java("", resolved.java_required)
    print(f"       Java：{java.display if java else '未找到'}")
    if java is None:
        return 1

    print("[3/5] 构建启动命令…")
    options = LaunchOptions.from_instance(instance, cfg, account)
    options.java_path = str(java.path)
    options.width, options.height = 640, 480
    cmd, resolved, java_info = build_command(game_dir, options, resolved, java, instance)
    text = command_to_string(cmd)
    print(f"       命令长度 {len(text)} 字符")
    print(f"       前 180 字符：\n       {text[:180]}…")
    assert "${" not in text, "存在未替换的占位符"

    print(f"[4/5] 启动游戏，运行 {SECONDS} 秒后结束…")
    logs: list[str] = []

    proc, cmd_text, resolved, java_info = launch(
        game_dir,
        options,
        instance,
        on_log=logs.append,
        on_exit=lambda code: print(f"       进程退出，代码 {code}"),
    )
    print(f"       PID {proc.pid}")

    deadline = time.time() + SECONDS
    while time.time() < deadline and proc.running:
        time.sleep(0.5)

    running = proc.running
    proc.kill()

    print(f"[5/5] 日志（共 {len(logs)} 行，最后 25 行）：")
    for line in logs[-25:]:
        print("       |", line)

    success = running and len(logs) > 0
    print("\n" + "=" * 60)
    if success:
        print(f"端到端测试通过：{VERSION} 安装并启动成功（进程存活 {SECONDS}s）")
        return 0
    print("端到端测试未通过：进程提前退出或没有输出")
    return 1


if __name__ == "__main__":
    sys.exit(main())
