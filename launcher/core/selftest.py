"""无界面的核心自检：在临时目录里构造最小版本，验证参数构建全链路。

运行：`python main.py --selftest`
不需要下载任何游戏文件，也不需要图形界面。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from .auth import create_offline_account, offline_uuid
from .config import Config, Instance
from .constants import BMCLAPI, MIRRORS, OFFICIAL
from .installer import collect_library_tasks, library_url
from .java import find_java, parse_version_output
from .launcher_core import LaunchOptions, build_command, command_to_string, resolve_version
from .rules import artifact_to_path, is_allowed, native_candidates, os_name, pick_native
from .utils import normal_arch, sanitize_name, setup_logging

# -------------------------------------------------------------------- 夹具

PARENT_JSON = {
    "id": "1.20.4",
    "type": "release",
    "mainClass": "net.minecraft.client.main.Main",
    "assetIndex": {"id": "12", "url": "https://example.invalid/index.json", "sha1": "x" * 40},
    "downloads": {
        "client": {
            "url": "https://example.invalid/1.20.4.jar",
            "sha1": "a" * 40,
            "size": 1024,
        }
    },
    "libraries": [
        {"name": "com.mojang:authlib:6.0.54", "downloads": {"artifact": {"path": "com/mojang/authlib/6.0.54/authlib-6.0.54.jar"}}},
        {
            "name": "org.lwjgl:lwjgl:3.3.3",
            "downloads": {"artifact": {"path": "org/lwjgl/lwjgl/3.3.3/lwjgl-3.3.3.jar"}},
        },
        {
            "name": "org.lwjgl:lwjgl:3.3.3:natives-windows",
            "downloads": {"artifact": {"path": "org/lwjgl/lwjgl/3.3.3/lwjgl-3.3.3-natives-windows.jar"}},
            "rules": [{"action": "allow", "os": {"name": "windows"}}, {"action": "disallow", "os": {"name": "osx"}}],
        },
        {
            "name": "org.lwjgl:lwjgl:3.3.3:natives-linux",
            "downloads": {"artifact": {"path": "org/lwjgl/lwjgl/3.3.3/lwjgl-3.3.3-natives-linux.jar"}},
            "rules": [{"action": "allow", "os": {"name": "linux"}}],
        },
    ],
    "arguments": {
        "jvm": [
            {"rules": [{"action": "allow", "os": {"name": "osx"}}], "value": "-XstartOnFirstThread"},
            "-Djava.library.path=${natives_directory}",
            "-cp",
            "${classpath}",
        ],
        "game": [
            "--username", "${auth_player_name}",
            "--version", "${version_name}",
            "--gameDir", "${game_directory}",
            "--assetsDir", "${assets_root}",
            "--assetIndex", "${assets_index_name}",
            "--uuid", "${auth_uuid}",
            "--accessToken", "${auth_access_token}",
            "--userType", "${user_type}",
        ],
    },
}

CHILD_JSON = {
    "id": "fabric-loader-0.15.10-1.20.4",
    "inheritsFrom": "1.20.4",
    "type": "release",
    "mainClass": "net.fabricmc.loader.impl.launch.knot.KnotClient",
    "arguments": {
        "jvm": ["-DFabricMcEmu=net.minecraft.client.main.Main"],
        "game": ["--versionType", "${version_type}"],
    },
    "libraries": [
        {"name": "net.fabricmc:fabric-loader:0.15.10", "downloads": {"artifact": {"path": "net/fabricmc/fabric-loader/0.15.10/fabric-loader-0.15.10.jar"}}}
    ],
}

LEGACY_JSON = {
    "id": "1.12.2",
    "type": "release",
    "mainClass": "net.minecraft.client.main.Main",
    "minecraftArguments": (
        "--username ${auth_player_name} --version ${version_name} "
        "--gameDir ${game_directory} --assetsDir ${assets_root} "
        "--assetIndex ${assets_index_name} --uuid ${auth_uuid} "
        "--accessToken ${auth_access_token} --userType ${user_type} --versionType ${version_type}"
    ),
    "libraries": [{"name": "com.mojang:authlib:1.5.25"}],
}


def _write_version(game_dir: Path, data: dict) -> None:
    target = game_dir / "versions" / data["id"]
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{data['id']}.json").write_text(json.dumps(data), encoding="utf-8")


# -------------------------------------------------------------------- 断言

_PASSED = []
_FAILED = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        _PASSED.append(name)
        print(f"  [OK]   {name}")
    else:
        _FAILED.append(name)
        print(f"  [FAIL] {name}" + (f" —— {detail}" if detail else ""))


# -------------------------------------------------------------------- 自检


def run_selftest() -> int:
    setup_logging()
    print("=" * 68)
    print("PyLauncher 核心自检")
    print("=" * 68)

    tmp = Path(tempfile.mkdtemp(prefix="pylauncher-selftest-"))
    game_dir = tmp / "game"
    (game_dir / "versions").mkdir(parents=True)

    try:
        # ---------------------------------------------------- 规则解析
        print("\n[1] 规则与 Maven 坐标")
        check("os_name() 返回合法值", os_name() in ("windows", "linux", "osx", "unknown"), os_name())
        check(
            "Maven 坐标转路径",
            artifact_to_path("com.mojang:authlib:6.0.54")
            == "com/mojang/authlib/6.0.54/authlib-6.0.54.jar",
            artifact_to_path("com.mojang:authlib:6.0.54"),
        )
        check(
            "带 classifier 的坐标",
            artifact_to_path("org.lwjgl:lwjgl:3.3.3:natives-windows")
            == "org/lwjgl/lwjgl/3.3.3/lwjgl-3.3.3-natives-windows.jar",
        )
        check(
            "allow(windows) 在本平台求值正确",
            is_allowed([{"action": "allow", "os": {"name": "windows"}}]) == (os_name() == "windows"),
        )
        check("无 rules 时视为允许（Mojang 语义）", is_allowed([]) is True)
        check(
            "disallow 覆盖 allow",
            is_allowed(
                [
                    {"action": "allow", "os": {"name": "windows"}},
                    {"action": "disallow", "os": {"name": "windows"}},
                ]
            ) is False,
        )
        lib = {"name": "com.mojang:authlib:6.0.54"}
        check(
            "镜像源重写库地址",
            library_url(lib, MIRRORS[BMCLAPI]).startswith("https://bmclapi2.bangbang93.com/maven/"),
            library_url(lib, MIRRORS[BMCLAPI]),
        )
        check("sanitize_name 过滤非法字符", sanitize_name('a/b:c*d?e"f') == "a_b_c_d_e_f", sanitize_name('a/b:c*d?e"f'))

        # ---------------------------------------------------- 账户
        print("\n[2] 离线账户")
        acc = create_offline_account("Steve")
        check("UUID 为 32 位无横线", len(acc.uuid) == 32 and "-" not in acc.uuid, acc.uuid)
        check("UUID 可加横线", len(acc.dashed_uuid) == 36, acc.dashed_uuid)
        check("同名离线账户 UUID 稳定", offline_uuid("Steve") == acc.uuid)
        check("不同名 UUID 不同", offline_uuid("Alex") != acc.uuid)
        try:
            create_offline_account("a")
            check("过短昵称被拒绝", False)
        except ValueError:
            check("过短昵称被拒绝", True)

        # ---------------------------------------------------- 配置
        print("\n[3] 配置持久化")
        cfg_path = tmp / "config.json"
        cfg = Config(cfg_path)
        cfg.set("game_dir", str(game_dir))
        cfg.set("memory.max_mb", 6144)
        ins = Instance(version_id="1.20.4", isolated=True, max_memory_mb=8192, jvm_args="-Xshare:auto")
        cfg.put_instance(ins)
        cfg2 = Config(cfg_path)
        check("标量配置往返一致", cfg2.get("memory.max_mb") == 6144)
        check("实例配置往返一致", cfg2.get_instance("1.20.4").max_memory_mb == 8192)
        check("实例隔离目录正确", cfg2.get_instance("1.20.4").game_directory(game_dir).name == "1.20.4")
        check(
            "隔离目录落在 isolates 下",
            cfg2.get_instance("1.20.4").game_directory(game_dir).parent.name == "isolates",
        )

        # ---------------------------------------------------- 版本链
        print("\n[4] 版本继承链解析")
        _write_version(game_dir, PARENT_JSON)
        _write_version(game_dir, CHILD_JSON)
        _write_version(game_dir, LEGACY_JSON)
        (game_dir / "versions" / "1.20.4" / "1.20.4.jar").write_bytes(b"fake-jar")

        instance = Instance(version_id=CHILD_JSON["id"], isolated=True)
        resolved = resolve_version(game_dir, CHILD_JSON["id"], instance)
        check("子版本主类覆盖父版本", resolved.main_class == "net.fabricmc.loader.impl.launch.knot.KnotClient", resolved.main_class)
        check("继承链 JAR 定位到父版本", resolved.jar.name == "1.20.4.jar", str(resolved.jar))
        check("资源索引继承", resolved.asset_index == "12", resolved.asset_index)
        cp_names = [p.name for p in resolved.classpath]
        check("客户端 JAR 位于 classpath 首位", cp_names[0] == "1.20.4.jar", str(cp_names[:3]))
        check("子版本库排在父版本库之前", "fabric-loader-0.15.10.jar" in cp_names)
        check(
            "跨平台 native 按规则过滤",
            ("lwjgl-3.3.3-natives-linux.jar" in cp_names) == (os_name() == "linux"),
            str([n for n in cp_names if "natives" in n]),
        )
        check("游戏目录为隔离目录", resolved.game_directory.name == CHILD_JSON["id"])
        check("JVM 参数含 Fabric 标志", any("FabricMcEmu" in a for a in resolved.jvm_args))

        legacy = resolve_version(game_dir, "1.12.2", Instance(version_id="1.12.2"))
        check("老版本 minecraftArguments 被拆分", len(legacy.game_args) == 18, str(len(legacy.game_args)))
        check("老版本首个参数为 --username", legacy.game_args[0] == "--username", legacy.game_args[0])

        # ---------------------------------------------------- 命令构建
        print("\n[5] 启动命令构建")
        options = LaunchOptions.from_instance(instance, cfg, acc)
        options.java_path = str(Path(sys.executable))   # 用当前解释器占位
        options.max_memory_mb = 4096
        options.min_memory_mb = 512
        options.width, options.height = 1920, 1080
        cmd, resolved2, java_info = build_command(game_dir, options, resolved, None, instance)

        joined = command_to_string(cmd)
        check("含最大内存参数", "-Xmx4096M" in cmd, joined[:120])
        check("含初始内存参数", "-Xms512M" in cmd)
        check("含主类", "net.fabricmc.loader.impl.launch.knot.KnotClient" in cmd)
        check("含 natives 路径", str(resolved.natives_dir) in joined)
        check("占位符全部被替换", not any("${" in a for a in cmd), str([a for a in cmd if "${" in a]))
        check("玩家名已注入", acc.username in cmd)
        check("UUID 已注入", acc.dashed_uuid in cmd, acc.dashed_uuid)
        check("游戏目录指向隔离目录", str(resolved.game_directory) in joined)
        check("窗口尺寸已注入", "--width" in cmd and "1920" in cmd)
        check("classpath 分隔符与平台一致", (";" if os.name == "nt" else ":") in joined)

        # 额外参数
        options.extra_jvm_args = "-Dtest=value"
        options.extra_game_args = "--server mc.example.com"
        cmd2, _, _ = build_command(game_dir, options, resolved, None, instance)
        check("额外 JVM 参数生效", "-Dtest=value" in cmd2)
        check("额外游戏参数生效", "mc.example.com" in cmd2)

        options.fullscreen = True
        cmd3, _, _ = build_command(game_dir, options, resolved, None, instance)
        check("全屏参数生效", "--fullscreen" in cmd3)

        # ---------------------------------------------------- Java
        print("\n[6] Java 运行时")
        check(
            "解析 Java 8 版本号",
            parse_version_output('java version "1.8.0_402"')["major"] == 8,
        )
        check(
            "解析 Java 17 版本号",
            parse_version_output('openjdk version "17.0.10" 2024-01-16')["major"] == 17,
        )
        check(
            "解析 64 位标记",
            parse_version_output('openjdk version "21.0.2"\nOpenJDK 64-Bit Server VM')["bitness"] == 64,
        )
        javas = find_java()
        check(f"本机 Java 检测完成（找到 {len(javas)} 个）", True)
        for j in javas[:3]:
            print(f"         · {j.display} — {j.path}")

        # ---------------------------------------------------- Natives
        print("\n[7] Natives 识别（新旧两种格式）")
        cands = native_candidates()
        check("本平台 native 候选非空", len(cands) > 0, str(cands))

        def make_modern(classifier: str) -> dict:
            return {
                "name": f"org.lwjgl:lwjgl-glfw:3.3.2:{classifier}",
                "downloads": {"artifact": {"path": f"org/lwjgl/lwjgl-glfw/3.3.2/lwjgl-glfw-3.3.2-{classifier}.jar", "size": 1}},
            }

        on_windows = os_name() == "windows"
        on_linux = os_name() == "linux"
        on_macos = os_name() == "osx"
        check(
            "新格式：本平台基础 native 被识别",
            pick_native(make_modern("natives-windows")) is not None if on_windows
            else (pick_native(make_modern("natives-linux")) is not None if on_linux
                  else pick_native(make_modern("natives-macos")) is not None),
            str(cands),
        )
        check(
            "新格式：x86 native 只在 32 位平台命中",
            (pick_native(make_modern("natives-windows-x86")) is not None) == (normal_arch() == "x86"),
        )
        check(
            "新格式：arm64 native 只在 arm64 平台命中",
            (pick_native(make_modern("natives-windows-arm64")) is not None) == (normal_arch() == "arm64"),
        )

        legacy_lib = {
            "name": "org.lwjgl:lwjgl:2.9.4",
            "natives": {"windows": "natives-windows", "linux": "natives-linux", "osx": "natives-macos"},
            "downloads": {
                "classifiers": {
                    "natives-windows": {"path": "org/lwjgl/lwjgl/2.9.4/lwjgl-2.9.4-natives-windows.jar", "size": 1}
                }
            },
        }
        lr = pick_native(legacy_lib)
        check("旧格式：natives 映射可识别", lr is not None and lr["standalone"] is False, str(lr))

        tasks, native_pairs = collect_library_tasks(game_dir, CHILD_JSON["id"], MIRRORS[BMCLAPI])
        check(f"继承链可收集到 native 解压项（{len(native_pairs)} 个）", len(native_pairs) > 0)
        check(
            "native 解压目标为版本 natives 目录",
            all(pair[1].name == "natives" for pair in native_pairs),
        )
        check(
            "解压目标均为 native 归档",
            all("natives-" in pair[0].name for pair in native_pairs),
            str([pair[0].name for pair in native_pairs]),
        )
        check(
            "native 归档落在 libraries 目录下",
            all(str(pair[0]).replace("\\", "/").count("/libraries/") == 1 for pair in native_pairs),
            str([str(pair[0]) for pair in native_pairs]),
        )

        # ---------------------------------------------------- 解压
        print("\n[8] Natives 解压（LWJGL 3 子目录布局）")
        import zipfile

        from .utils import extract_zip

        jar = tmp / "fake-natives.jar"
        with zipfile.ZipFile(jar, "w") as zf:
            zf.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
            zf.writestr("windows/x64/org/lwjgl/glfw/glfw.dll", b"GLFW-DLL")
            zf.writestr("windows/x64/org/lwjgl/lwjgl.dll", b"LWJGL-DLL")
            zf.writestr("docs/notes.txt", b"hello")
        out_dir = tmp / "natives-out"
        files = extract_zip(
            jar,
            out_dir,
            exclude=lambda n: n.startswith("META-INF/"),
            flatten_natives=True,
        )
        names = {p.relative_to(out_dir).as_posix() for p in files}
        check("保留原始目录结构", "windows/x64/org/lwjgl/glfw/glfw.dll" in names, str(sorted(names)))
        check("原生库在根目录另有扁平副本", {"glfw.dll", "lwjgl.dll"} <= names, str(sorted(names)))
        check("META-INF 被排除", not any(n.startswith("META-INF") for n in names))
        check("非原生文件不会被扁平化", "docs/notes.txt" in names and "notes.txt" not in names, str(sorted(names)))
        check(
            "扁平副本内容一致",
            (out_dir / "glfw.dll").read_bytes() == b"GLFW-DLL",
        )

        # ---------------------------------------------------- 桌面快捷方式
        print("\n[9] 桌面快捷方式（纯逻辑，不写桌面）")
        from . import shortcut as SC

        check("平台识别", SC.platform_kind() in ("windows", "macos", "linux"), SC.platform_kind())
        kind = SC.platform_kind()
        expect_suffix = {"windows": ".lnk", "macos": ".app", "linux": ".desktop"}[kind]
        check(
            f"文件名后缀为 {expect_suffix}",
            SC.shortcut_filename("PyLauncher").endswith(expect_suffix),
            SC.shortcut_filename("PyLauncher"),
        )
        check("平台说明含格式", expect_suffix in SC.platform_label(), SC.platform_label())
        check("图标键匹配平台", SC.icon_asset_key() in ("ico", "icns", "png"))
        check("Windows 参数按需加引号", SC._quote_win("a b") == '"a b"' and SC._quote_win("ab") == "ab")
        check("Desktop Entry 参数转义", SC._quote_desktop("a b").startswith('"'))

        # 用一个占位脚本模拟 main.py，便于校验参数拼装
        (tmp / "main.py").write_text("# placeholder\n", encoding="utf-8")
        spec = SC.ShortcutSpec(
            name="PyLauncher",
            description="PyLauncher 自检",
            executable=Path(sys.executable),
            arguments=[str(tmp / "main.py"), "--launcher-home", str(tmp)],
            working_dir=tmp,
        )
        check("命令行包含解释器", str(Path(sys.executable)) in spec.command_line())
        check("命令行保留参数", "--launcher-home" in spec.command_line())
        SC.validate_spec(spec)
        check("目标存在时校验通过", True)
        try:
            SC.validate_spec(
                SC.ShortcutSpec(name="x", description="x", executable=tmp / "missing.exe")
            )
            check("目标缺失应报错", False)
        except SC.ShortcutError as exc:
            check("目标缺失报 target-missing", exc.reason == SC.ShortcutError.TARGET_MISSING, exc.reason)

        check("桌面目录可解析", isinstance(SC.desktop_directory(must_exist=False), Path))
        check("未创建时查不到", SC.find_existing_shortcut("PyLauncher-自检-不存在", tmp) is None)
        from .config import DEFAULTS

        check("配置默认值含快捷方式段", "shortcut" in DEFAULTS, str(list(DEFAULTS)))

        fallback = SC._fallback_script(tmp, spec)
        body = fallback.read_text(encoding="utf-8", errors="ignore")
        check("降级脚本落盘", fallback.is_file(), str(fallback))
        check("降级脚本含启动命令", "launcher-home" in body, body[:120])
        fallback.unlink()

        # ---------------------------------------------------- 运行期健壮性回归
        print("\n[10] 启动健壮性与下载源（回归用例）")
        from .java import JavaInfo, describe_java_problem, parse_minor_version
        from .launcher_core import _resolve_java_requirement
        from .manifest import scan_local_versions

        # version.json 的 javaVersion.majorVersion 才是权威来源；
        # 过去靠版本号字符串猜，`26.3` 这类整合包会被当成需要 Java 8，
        # 结果用 Java 21 去跑要求 Java 25 的游戏，0.5 秒就 UnsupportedClassVersionError。
        chain = [
            {"id": "26.3", "mainClass": "X", "javaVersion": {"majorVersion": 25}},
            {"id": "parent", "javaVersion": {"majorVersion": 17}},
        ]
        check("Java 需求取自版本 JSON", _resolve_java_requirement(chain, "26.3") == 25)
        check(
            "无 javaVersion 时按版本号兜底",
            _resolve_java_requirement([{"id": "1.20.4"}], "1.20.4") == 17,
        )
        check("现代命名也能解析", parse_minor_version("26.3") >= 21, str(parse_minor_version("26.3")))
        check("旧命名仍然正确", parse_minor_version("1.20.4") == 20)

        low = [JavaInfo(path=Path("java"), major=21)]
        check("本机版本不足会被指出", "需要 Java 25" in describe_java_problem(low, 25))
        check("版本足够时不报警", describe_java_problem(low, 17) == "")
        check("没有 Java 时给出提示", "没有检测到" in describe_java_problem([], 8))

        # 版本目录里混入的 usercache.json / modrinth.index.json 不该变成假版本
        vroot = tmp / "gd" / "versions" / "My Pack"
        vroot.mkdir(parents=True, exist_ok=True)
        (vroot / "My Pack.json").write_text(
            json.dumps({"id": "My Pack", "mainClass": "X", "javaVersion": {"majorVersion": 21}}),
            encoding="utf-8",
        )
        (vroot / "usercache.json").write_text('[{"name":"Steve"}]', encoding="utf-8")
        (vroot / "modrinth.index.json").write_text('{"formatVersion":1}', encoding="utf-8")
        found = scan_local_versions(tmp / "gd")
        check("只识别真正的版本文件", len(found) == 1, str([v.id for v in found]))
        check("保留的版本名正确", bool(found) and found[0].id == "My Pack")

        from .installer import ensure_backup_urls, get_mirror, swap_url_root
        from .http import DownloadTask, other_mirror

        check("另一个源互为补充", other_mirror(BMCLAPI) == OFFICIAL and other_mirror(OFFICIAL) == BMCLAPI)
        official_lib = "https://libraries.minecraft.net/com/mojang/authlib/6.0.54/authlib.jar"
        to_mirror = swap_url_root(official_lib, get_mirror(BMCLAPI))
        check("库地址可换到镜像", to_mirror.startswith(MIRRORS[BMCLAPI].libraries), to_mirror)
        check("镜像地址可换回官方", swap_url_root(to_mirror, get_mirror(OFFICIAL)) == official_lib)

        task = DownloadTask(name="authlib", url=official_lib, dest=tmp / "x.jar")
        ensure_backup_urls([task], get_mirror(OFFICIAL))
        check("任务自动带上备用源", len(task.urls) == 2 and task.urls[1].startswith(MIRRORS[BMCLAPI].libraries),
              str(task.urls))

        print("\n" + "=" * 68)
        print(f"通过 {len(_PASSED)} 项，失败 {len(_FAILED)} 项")
        if _FAILED:
            print("失败项：" + "、".join(_FAILED))
            return 1
        print("全部通过 ✓")
        return 0

    finally:
        shutil.rmtree(tmp, ignore_errors=True)
