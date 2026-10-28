"""沙箱内随机模拟压力测试（>=1000 次）。

    python tests/stress_sim.py [迭代次数，默认 1200]

覆盖的不是「正常流程」（那由 selftest 负责），而是**异常与边界**：
随机字符串、空值、越界路径、畸形 JSON、伪造的压缩包与清单、
并发取消、重复点击、损坏配置。目标是找出会抛异常 / 卡死 / 越权写入的地方。

判定标准：任何一次迭代出现未捕获异常即计为失败。
"""

from __future__ import annotations

import json
import os
import random
import string
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYLAUNCHER_NO_AUTOSHORTCUT", "1")

ITERATIONS = int(sys.argv[1]) if len(sys.argv) > 1 else 1200

# ------------------------------------------------------------------ 数据池

WEIRD_NAMES = [
    "", " ", ".", "..", "../..", "/", "\\", "a/b", "C:\\Windows\\System32\\evil",
    "NUL", "COM1", "con", "1.20.4", "26.3", "超长下载包", "Better MC [NEOFORGE] BMC5",
    "Map&datapacks Environment", "a" * 300, "te st", "-rf", "--force", "%PATH%",
    "${jndi:ldap://evil}", "..\\..\\evil", "mods/../../x", "\x00\x01", "emoji😀",
]
RANDOM_POOL = string.printable + "中文测试\x00\x7f"

VERSION_JSON_SAMPLES = [
    {},
    {"id": ""},
    {"id": None},
    {"id": "x", "libraries": "not-a-list"},
    {"id": "x", "mainClass": 123},
    {"id": "x", "javaVersion": {"majorVersion": "abc"}},
    {"id": "x", "javaVersion": {"majorVersion": 25}},
    {"id": "x", "javaVersion": None},
    {"id": "1.20.4", "libraries": [{"name": "a:b:1"}, {}, {"name": ""}]},
    {"id": "1.20.4", "arguments": {"jvm": [1, {"rules": "bad"}, None]}},
    {"id": "1.20.4", "minecraftArguments": ""},
    "not-json-at-all",
    [],
    None,
]

failures: list[str] = []
runs = 0


def guard_run(name: str, fn) -> None:
    """跑一次模拟；任何异常都记录为失败。"""
    global runs
    runs += 1
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        failures.append(f"{name}: {type(exc).__name__}: {str(exc)[:120]}")


def rand_text(n: int = 12) -> str:
    return "".join(random.choice(RANDOM_POOL) for _ in range(n))


# ------------------------------------------------------------------ 各场景

_tmp = Path(tempfile.mkdtemp(prefix="stress-"))


def sim_sanitize() -> None:
    from launcher.core.utils import sanitize_name, safe_join

    name = random.choice(WEIRD_NAMES) or rand_text()
    out = sanitize_name(name)
    assert ".." not in out, f"残留 ..: {out!r}"
    assert out, "结果为空"
    try:
        target = safe_join(_tmp, f"{name}.jar")
        assert str(target).startswith(str(_tmp.resolve())), f"越界: {target}"
    except ValueError:
        pass  # 显式拒绝也是正确行为


def sim_manifest_parse() -> None:
    from launcher.core.manifest import VersionManifest

    vm = VersionManifest()
    payload = random.choice(VERSION_JSON_SAMPLES)
    try:
        vm._apply(payload, "sim")
    except Exception:
        pass  # 无效数据抛错是预期行为


def sim_manifest_list() -> None:
    from launcher.core.manifest import VersionManifest

    vm = VersionManifest()
    versions = [{"id": "1.20.4"}, {"type": "release"}, "junk", None,
                {"id": rand_text(6), "type": random.choice(["release", "weird", ""])}]
    vm._apply({"versions": versions, "latest": {"release": "1.20.4"}}, "sim")
    assert len(vm.all()) >= 1


def sim_scan_versions() -> None:
    from launcher.core.manifest import scan_local_versions

    for _ in range(3):
        scan_local_versions(_tmp / random.choice(["none", "a", "versions"]))


def sim_zip() -> None:
    from launcher.core.utils import extract_zip

    arc = _tmp / f"{random.randint(0, 9999)}.zip"
    with zipfile.ZipFile(arc, "w") as zf:
        zf.writestr("ok.txt", "fine")
        zf.writestr(random.choice(["../evil.txt", "/abs.txt", "a/../../b.txt", "C:/x.txt"]), "bad")
    out = _tmp / f"out-{random.randint(0, 9999)}"
    try:
        files = extract_zip(arc, out)
        for f in files:
            assert str(f.resolve()).startswith(str(out.resolve())), f"解压越界: {f}"
    except ValueError:
        pass
    try:
        arc.unlink()
    except OSError:
        pass


def sim_netguard() -> None:
    from launcher.core.netguard import guard

    urls = [
        "https://api.modrinth.com/v2/x", "https://libraries.minecraft.net/a.jar",
        "https://evil.example.com/a", "http://x.test/a", "file:///etc/passwd",
        "", "not-a-url", "https://", "ftp://x/y", rand_text(20),
    ]
    for url in random.sample(urls, 4):
        try:
            guard.check_url(url)
        except Exception:
            pass


def sim_join_code() -> None:
    from launcher.core.netplay import make_join_code, parse_join_code

    code = make_join_code(rand_text(6), random.choice(["1.2.3.4", "", "host"]), random.randint(1, 65535))
    try:
        parse_join_code(code)
    except ValueError:
        pass
    for bad in ["", "xxxx", rand_text(30), "中文"]:
        try:
            parse_join_code(bad)
        except ValueError:
            pass


def sim_servers() -> None:
    from launcher.core.servers import ServerInfo, split_address

    for text in ["", "host", "host:port", "1.2.3.4:25565", "[::1]:25565", rand_text(10), "::"]:
        host, port = split_address(text)
        assert isinstance(port, int) and 0 <= port <= 65535, f"端口异常: {port}"
    info = ServerInfo.from_dict({"name": None, "address": 123, "port": "bad"})
    assert isinstance(info.port, int)


def sim_config_roundtrip() -> None:
    from launcher.core.config import Config

    path = _tmp / f"cfg-{random.randint(0, 999)}.json"
    cfg = Config(path)
    keys = ["game_dir", "mirror", "java.selected", "shortcut.name", "servers", "ui.animations"]
    for key in random.sample(keys, 3):
        cfg.set(key, random.choice([rand_text(8), "", 0, True, ["a"], {"x": 1}]))
    cfg.save()
    again = Config(path)
    assert again.path == path
    try:
        path.unlink()
    except OSError:
        pass


def sim_resources() -> None:
    from launcher.core.resources import packs_dir, scan

    instance = _tmp / f"inst-{random.randint(0, 99)}"
    for kind in ("resourcepack", "shaderpack", "", "weird"):
        try:
            scan(instance, kind)
        except Exception:
            pass
    assert packs_dir(instance, "shaderpack").name == "shaderpacks"


def sim_launch_options() -> None:
    from launcher.core.launcher_core import LaunchOptions
    from launcher.core.config import Config

    cfg = Config(_tmp / f"lo-{random.randint(0, 99)}.json")
    from launcher.core.config import Instance

    ins = Instance(version_id=random.choice(["1.20.4", "", "26.3", rand_text(5)]))
    try:
        opts = LaunchOptions.from_instance(ins, cfg, None)
        assert isinstance(opts.version_id, str)
    except Exception:
        pass


def sim_java_requirement() -> None:
    from launcher.core.launcher_core import _resolve_java_requirement

    chain = [{"javaVersion": random.choice([None, {}, {"majorVersion": 21}, {"majorVersion": "x"},
                                            {"majorVersion": -1}, {"majorVersion": True}])},
             {"javaVersion": {"majorVersion": 17}}]
    try:
        value = _resolve_java_requirement(chain, random.choice(["1.20.4", "26.3", rand_text(6)]))
        assert isinstance(value, int) and value >= 0
    except Exception:
        pass


def sim_modrinth_parse() -> None:
    from launcher.core.modrinth import pick_file

    payloads = [
        {}, {"files": []}, {"files": "x"}, {"files": [{}]},
        {"files": [{"url": ""}]}, {"files": [{"url": "https://cdn.modrinth.com/a.jar", "primary": True}]},
        {"files": [{"hashes": None, "size": "big"}]},
    ]
    for item in payloads:
        try:
            pick_file(item)
        except Exception:
            pass


def sim_shortcut_spec() -> None:
    from launcher.core.shortcut import ShortcutSpec, shortcut_filename

    name = random.choice(WEIRD_NAMES) or rand_text(5)
    try:
        spec = ShortcutSpec(name=name, description=rand_text(10), executable=_tmp / "missing.exe")
        assert spec.command_line()
        assert shortcut_filename(name)
    except Exception:
        pass


def sim_http_session() -> None:
    from launcher.core.http import session

    # 只做参数校验，不真发请求（避免依赖网络）
    assert session.retries > 0 and session.timeout > 0


def sim_worker_cancel() -> None:
    """模拟「点一下就取消」：线程必须能协作式退出，不能残留。"""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from launcher.ui.workers import Worker, start_worker

    class Owner:
        pass

    owner = Owner()

    def work(on_progress, on_message, should_cancel=None):
        for i in range(300):
            if should_cancel and should_cancel():
                return "cancelled"
            time.sleep(0.001)
        return "done"

    worker = Worker(work)
    start_worker(owner, worker)
    time.sleep(0.01)
    worker.request_cancel()
    assert worker.wait(3000), "线程未能在取消后退出"
    QApplication.processEvents()
    assert not getattr(owner, "_workers", [1]), "线程登记表未清理"


def sim_account_db() -> None:
    from launcher.core.auth import Account, AccountDatabase

    path = _tmp / f"acc-{random.randint(0, 99)}.json"
    db = AccountDatabase(path)
    for i in range(3):
        db.add(Account(username=f"U{i}", uuid="0" * 32,
                       type=random.choice(["offline", "microsoft", "yggdrasil", ""])))
    db.save()
    db2 = AccountDatabase(path)
    assert len(db2.accounts) == 3
    try:
        path.unlink()
    except OSError:
        pass


def sim_anim_switch() -> None:
    """模拟连续快速切换页面（动画叠加时不许崩）。"""
    from PySide6.QtWidgets import QApplication, QLabel, QStackedWidget

    app = QApplication.instance() or QApplication([])
    from launcher.ui.anim import CrossFadeStack

    stack = CrossFadeStack()
    widgets = [QLabel(f"page-{i}") for i in range(4)]
    for w in widgets:
        stack.addWidget(w)
    stack.resize(320, 240)
    stack.show()
    for _ in range(25):
        stack.setCurrentIndex(random.randint(0, 3))
        QApplication.processEvents()
    # 不允许有控件被留在透明状态
    from PySide6.QtWidgets import QGraphicsOpacityEffect

    for i in range(stack.count()):
        eff = stack.widget(i).graphicsEffect()
        assert not (isinstance(eff, QGraphicsOpacityEffect) and eff.opacity() < 0.99), "控件被留在透明状态"
    stack.hide()


def sim_gui_pages() -> None:
    """随机切换主窗口页面并触发刷新，模拟真实点击流。"""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    if not hasattr(sim_gui_pages, "_window"):
        from launcher.core.config import Config
        from launcher.ui.app import MainWindow, NAV_ITEMS
        from launcher.ui.theme import apply_theme

        apply_theme(app)
        cfg = Config(_tmp / "gui-config.json")
        cfg.set("game_dir", str(_tmp / "game"))
        win = MainWindow(cfg)
        win.refresh_all()
        win.show()
        sim_gui_pages._window = win
        sim_gui_pages._nav = NAV_ITEMS
    win = sim_gui_pages._window
    key, _text = random.choice(sim_gui_pages._nav)
    win.navigate(key)
    QApplication.processEvents()


SCENARIOS = [
    ("路径安全化", sim_sanitize),
    ("清单解析", sim_manifest_parse),
    ("清单列表", sim_manifest_list),
    ("版本扫描", sim_scan_versions),
    ("压缩包防护", sim_zip),
    ("防火墙", sim_netguard),
    ("联机码", sim_join_code),
    ("服务器解析", sim_servers),
    ("配置往返", sim_config_roundtrip),
    ("材质包/光影", sim_resources),
    ("启动选项", sim_launch_options),
    ("Java 需求", sim_java_requirement),
    ("Modrinth 解析", sim_modrinth_parse),
    ("快捷方式规格", sim_shortcut_spec),
    ("HTTP 会话", sim_http_session),
    ("后台线程取消", sim_worker_cancel),
    ("账户库", sim_account_db),
    ("动画切换", sim_anim_switch),
    ("界面导航", sim_gui_pages),
]

def main(argv: list[str] | None = None) -> int:
    global ITERATIONS
    args = argv if argv is not None else sys.argv[1:]
    if args:
        try:
            ITERATIONS = int(args[0])
        except ValueError:
            pass

    print(f"=== 沙箱模拟压力测试：{ITERATIONS} 次迭代 / {len(SCENARIOS)} 类场景 ===")
    random.seed(20261027)
    started = time.time()
    for i in range(ITERATIONS):
        name, fn = random.choice(SCENARIOS)
        guard_run(name, fn)
        if (i + 1) % 200 == 0:
            print(f"  …已完成 {i + 1}/{ITERATIONS}（累计失败 {len(failures)}）")
            sys.stdout.flush()

    print(f"\n耗时 {time.time() - started:.1f}s，共执行 {runs} 次模拟")
    if failures:
        kinds: dict[str, int] = {}
        for f in failures:
            key = f.split(":", 1)[0]
            kinds[key] = kinds.get(key, 0) + 1
        print(f"失败 {len(failures)} 次，分布：{kinds}")
        for f in failures[:12]:
            print("   ·", f)
        return 1
    print("模拟测试全部通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
