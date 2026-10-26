"""运行时健壮性冒烟测试（对应四个已知故障的回归防线）。

    python tests/smoke_runtime.py

覆盖：

1. 崩溃      —— 后台线程协作式取消、线程生命周期托管、游戏日志跨线程安全；
2. 启动游戏  —— Java 版本需求取自版本 JSON、缺 Java 时给出可执行提示；
3. 下载      —— 源健康检查与自动切换、每个任务带跨源备用地址、
                只有 classifiers 的老库不再拼出必然 404 的地址；
4. 桌面快捷方式 —— 首次运行自动创建、幂等不重复、目标指向正确的可执行文件。

不需要游戏，但 3 会做一次真实的小文件下载（约 600KB），因此需要网络。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYLAUNCHER_NO_AUTOSHORTCUT", "1")  # 测试不往真实桌面写东西

_FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}" + (f" —— {detail}" if detail and not ok else ""))
    if not ok:
        _FAILED.append(label)


# ============================================================ 1. 崩溃防线
print("\n[1] 线程与崩溃防线")

_checks_total = 0

# 源码层面：不允许再出现 QThread.terminate()
import re

_TERMINATE_RE = re.compile(r"\b\w*(?:worker|thread)\w*\s*\.\s*terminate\s*\(", re.IGNORECASE)
offenders: list[str] = []
for path in (ROOT / "launcher").rglob("*.py"):
    if path.name == "workers.py":
        continue  # 文档里避免出现示例本身也会命中
    for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
        if _TERMINATE_RE.search(line):
            offenders.append(f"{path.relative_to(ROOT)}:{lineno}")
check("不再使用危险的 QThread.terminate()", not offenders, "、".join(offenders))

from PySide6.QtCore import QThread, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv[:1])
main_thread = QThread.currentThread()

from launcher.ui.workers import Worker, start_worker  # noqa: E402


class _Owner:
    pass


owner = _Owner()

done = {"cancelled": False, "finished": False}


def long_task(on_progress, on_message, should_cancel=None):
    for _ in range(200):
        if should_cancel and should_cancel():
            done["cancelled"] = True
            return "cancelled"
        time.sleep(0.02)
    return "done"


worker = Worker(long_task)
worker.finished.connect(lambda: done.__setitem__("finished", True))
start_worker(owner, worker)
time.sleep(0.1)
worker.request_cancel()
ok = worker.wait(4000)
# `finished` 信号的槽要在事件循环里执行，冒烟场景下手动泵一次
for _ in range(20):
    QApplication.processEvents()
check("协作式取消能让线程自然退出", ok and worker.isFinished())
check("任务确实感知到取消", done["cancelled"])
check("结束后线程对象从登记表移除", not getattr(owner, "_workers", [worker]), str(getattr(owner, "_workers", None)))
try:
    from launcher.ui.workers import wait_worker

    wait_worker(worker, 500)
    check("结束后再调用 wait_worker 不报错", True)
except RuntimeError as exc:
    check("结束后再调用 wait_worker 不报错", False, str(exc))

# 游戏日志跨线程安全：子线程回调必须经由信号回到主线程
from launcher.core.launcher_core import GameProcess  # noqa: E402
from launcher.ui.widgets import LogWindow  # noqa: E402

log_window = LogWindow("测试", None)
window = {"thread": None, "lines": 0}


def on_line(text: str) -> None:
    window["lines"] += 1
    window["thread"] = QThread.currentThread()


probe = GameProcess(
    [sys.executable, "-c", "print('\\u6d4b\\u8bd5\\u4e2d\\u6587\\u65e5\\u5fd7')"],
    Path(tempfile.gettempdir()),
    None,
    None,
    None,
)

# 模拟首页做法：回调 emit 信号，由主线程槽函数操作控件
from PySide6.QtCore import Signal  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402


class _Bridge(QWidget):
    line = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.line.connect(on_line)


bridge = _Bridge()
probe.on_log = bridge.line.emit
probe.start()
deadline = time.time() + 10
while time.time() < deadline and window["lines"] == 0:
    QApplication.processEvents()
    time.sleep(0.05)
QApplication.processEvents()

check("子进程日志能被读出", window["lines"] > 0, str(window["lines"]))
check("日志回调运行在主线程", window["thread"] is main_thread, str(window["thread"]))
probe.kill()

# ============================================================ 2. 启动游戏
print("\n[2] 启动前的 Java 版本判定")

from launcher.core.java import JavaInfo, describe_java_problem  # noqa: E402
from launcher.core.launcher_core import _resolve_java_requirement  # noqa: E402

check(
    "javaVersion.majorVersion 优先",
    _resolve_java_requirement([{"javaVersion": {"majorVersion": 25}}], "26.3") == 25,
)
check(
    "继承链上子版本声明优先",
    _resolve_java_requirement(
        [{"javaVersion": {"majorVersion": 21}}, {"javaVersion": {"majorVersion": 17}}], "x"
    )
    == 21,
)
low = [JavaInfo(path=Path("java"), major=21)]
problem = describe_java_problem(low, 25)
check("版本不足给出安装提示", "需要 Java 25" in problem, problem)
check("版本足够时不打扰", describe_java_problem(low, 21) == "")

# ============================================================ 3. 下载
print("\n[3] 下载链路")

from launcher.core.constants import MIRRORS, OFFICIAL  # noqa: E402
from launcher.core.http import (  # noqa: E402
    DownloadManager,
    DownloadTask,
    available_mirror,
    other_mirror,
    probe_source,
)
from launcher.core.installer import _library_task, ensure_backup_urls, get_mirror  # noqa: E402

check("源健康检查返回合法结果", isinstance(probe_source(OFFICIAL), bool))
usable = available_mirror("bmclapi")
check("可用源一定是已注册的源", usable in MIRRORS, usable)
print(f"        实际选中：{usable}（{MIRRORS[usable].name}）")

task = DownloadTask(
    name="com/mojang/authlib",
    url="https://libraries.minecraft.net/com/mojang/authlib/6.0.54/authlib-6.0.54.jar",
    dest=Path(tempfile.gettempdir()) / f"pylauncher-test-authlib-{os.getpid()}.jar",
    sha1="",
    size=0,
)
ensure_backup_urls([task], get_mirror(OFFICIAL))
check("任务自动获得跨源备用地址", len(task.urls) == 2, str(task.urls))

stats = DownloadManager(workers=2).run([task])
downloaded = task.dest.is_file() and task.dest.stat().st_size > 0
check("真实下载一个小库成功", downloaded, str(stats.failures[:1]))
try:
    task.dest.unlink(missing_ok=True)
except OSError:
    pass

# 只有 classifiers 的老依赖不该产生必然 404 的主构件任务
legacy = {
    "name": "org.lwjgl.lwjgl:lwjgl-platform:2.9.1",
    "downloads": {
        "classifiers": {
            "natives-windows": {
                "path": "org/lwjgl/lwjgl/lwjgl-platform/2.9.1/lwjgl-platform-2.9.1-natives-windows.jar",
                "sha1": "x" * 40,
                "size": 1,
            }
        }
    },
}
check(
    "无主构件的老依赖不再拼 404 地址",
    _library_task(legacy, Path(tempfile.gettempdir()), get_mirror(OFFICIAL)) is None,
)
modern = {
    "name": "com.mojang:authlib:6.0.54",
    "downloads": {"artifact": {"path": "com/mojang/authlib/6.0.54/authlib-6.0.54.jar"}},
}
check(
    "正常依赖仍然生成任务",
    _library_task(modern, Path(tempfile.gettempdir()), get_mirror(OFFICIAL)) is not None,
)

# ============================================================ 4. 桌面快捷方式
print("\n[4] 桌面快捷方式首次运行与幂等")

import launcher.core.shortcut as SC  # noqa: E402
from launcher.ui import shortcut_flow  # noqa: E402

# 本节要验证的正是「首次运行自动创建」，临时放开前面为保护真实桌面设的开关
_prev_suppress = os.environ.pop("PYLAUNCHER_NO_AUTOSHORTCUT", None)

fake_desktop = Path(tempfile.mkdtemp(prefix="pylauncher-sc-")) / "desktop"
fake_desktop.mkdir(parents=True)
_orig_desktop = SC.desktop_directory
SC.desktop_directory = lambda must_exist=True: fake_desktop
try:
    from launcher.core import paths as P  # noqa: E402

    P.set_launcher_root(Path(tempfile.mkdtemp(prefix="pylauncher-sc-home-")))

    class _Cfg:
        """最小配置替身：只实现 ensure_created_once 用到的 get/set。"""

        def __init__(self) -> None:
            self.store = {
                "shortcut": {
                    "name": "",
                    "last_path": "",
                    "last_created": "",
                    "auto_create": True,
                    "auto_done": False,
                }
            }

        def get(self, key, default=None):
            node = self.store
            for part in key.split("."):
                if not isinstance(node, dict):
                    return default
                node = node.get(part)
                if node is None:
                    return default
            return node

        def set(self, key, value):
            parts = key.split(".")
            node = self.store
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value
            return True

    cfg = _Cfg()
    first = shortcut_flow.ensure_created_once(cfg)
    check("首次运行自动创建", first is not None and first.path is not None,
          str(first.detail if first else "未创建"))
    check("创建后标记为已尝试", bool(cfg.get("shortcut.auto_done")))
    before = len(list(fake_desktop.iterdir()))
    check("再次调用不会重复创建", shortcut_flow.ensure_created_once(cfg) is None
          and len(list(fake_desktop.iterdir())) == before)

    # 配置丢失但桌面已有 → 只登记不重建
    cfg.store["shortcut"]["last_path"] = ""
    cfg.store["shortcut"]["auto_done"] = False
    again = shortcut_flow.ensure_created_once(cfg)
    check("桌面已存在时不重复创建", again is None and len(list(fake_desktop.iterdir())) == before,
          str([p.name for p in fake_desktop.iterdir()]))
    check("自动登记已存在的路径", bool(cfg.get("shortcut.last_path")))

    created = next(iter(fake_desktop.iterdir()), None)
    if created is not None:
        target = SC.existing_target(Path(created))
        expect = str(shortcut_flow.launcher_executable()).lower().replace("pythonw.exe", "python.exe")
        check("指向正确的可执行文件",
              str(target).lower().replace("pythonw.exe", "python.exe") == expect, str(target))
        info = {}
        if SC.platform_kind() == "windows" and created.suffix.lower() == ".lnk":
            info = SC._windows_lnk_read(Path(created))
        if info:
            check("快捷方式写入了描述", "PyLauncher" in str(info.get("description", "")),
                  str(info.get("description")))
            check("快捷方式带上 --launcher-home", "--launcher-home" in str(info.get("arguments", "")),
                  str(info.get("arguments")))
            check("快捷方式设置了自定义图标", str(info.get("icon", "")).lower().endswith(".ico"),
                  str(info.get("icon")))
finally:
    SC.desktop_directory = _orig_desktop
    if _prev_suppress is not None:
        os.environ["PYLAUNCHER_NO_AUTOSHORTCUT"] = _prev_suppress

print("\n" + "=" * 66)
if _FAILED:
    print(f"失败 {len(_FAILED)} 项：" + "、".join(_FAILED))
    sys.exit(1)
print("运行时健壮性测试全部通过 ✓")
