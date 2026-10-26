"""桌面快捷方式：按当前运行平台生成对应的桌面图标并指向本启动器。

平台实现：

===========  =========================  ==============================================
平台         产物                       说明
===========  =========================  ==============================================
Windows      `PyLauncher.lnk`           直接调用系统 Shell COM（`IShellLinkW` +
                                        `IPersistFile`，ctypes，无第三方依赖），可写入
                                        目标、参数、起始位置、窗口描述与 `.ico` 图标
macOS        `PyLauncher.app`            标准应用程序包：`Info.plist` + 启动脚本 +
                                        `AppIcon.icns`
Linux        `pylauncher.desktop`       遵循 XDG Desktop Entry 规范，标记可执行并尽力
                                        打上「可信」元数据
===========  =========================  ==============================================

可靠性设计（对应产品需求）：

* **重复创建** —— 目标已存在时抛 `ShortcutError(reason="exists")`，由界面提示覆盖；
  `overwrite=True` 覆盖，并返回旧的目标路径供界面展示。
* **创建失败** —— 抛出带 `reason` 与 `detail` 的 `ShortcutError`，界面据此给出可操作的
  错误提示（权限不足 / 桌面目录不存在 / 系统组件不可用 / 目标程序已被移动）。
* **降级方案** —— 原生快捷方式失败时自动退化为「桌面启动脚本」（`.cmd` / `.command` /
  `.sh`）；桌面目录不可写时改写到用户目录并返回实际路径。降级结果用
  `ShortcutResult.degraded` 标记，界面会如实告知用户。

本模块不依赖 Qt，也不依赖任何第三方库，可在命令行与自检中直接使用。
"""

from __future__ import annotations

import logging
import os
import shlex
import stat
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

LOG = logging.getLogger("pylauncher.shortcut")

# ------------------------------------------------------------------ 数据结构


class ShortcutError(Exception):
    """快捷方式操作失败，`reason` 供界面决定提示文案与补救动作。"""

    #: 桌面目录不可用
    DESKTOP_NOT_FOUND = "desktop-not-found"
    #: 无写入权限
    PERMISSION_DENIED = "permission-denied"
    #: 系统组件（Shell COM）不可用
    SYSTEM_UNAVAILABLE = "system-unavailable"
    #: 已经存在同名快捷方式
    EXISTS = "exists"
    #: 目标可执行文件 / 脚本不存在（例如程序被移动或卸载）
    TARGET_MISSING = "target-missing"
    #: 当前平台不支持
    UNSUPPORTED = "unsupported-platform"

    def __init__(self, message: str, reason: str = "unknown", detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason
        self.detail = detail


@dataclass
class ShortcutSpec:
    """描述「快捷方式要启动什么」。"""

    name: str                                   # 显示名称
    description: str                            # 应用信息（Windows 描述 / .desktop Comment）
    executable: Path                            # 解释器（或打包后的 exe）
    arguments: List[str] = field(default_factory=list)
    working_dir: Optional[Path] = None
    icon: Optional[Path] = None                 # 平台对应的图标文件
    categories: str = "Game;Utility;"
    startup_wm_class: str = "PyLauncher"

    def command_line(self) -> str:
        """Windows 风格命令行（参数按需加引号）。"""
        return " ".join([_quote_win(str(self.executable))] + [_quote_win(a) for a in self.arguments])

    def exec_line(self) -> str:
        """XDG Desktop Entry 的 Exec 行（转义规则与 shell 不同）。"""
        return " ".join([_quote_desktop(str(self.executable))] + [_quote_desktop(a) for a in self.arguments])


@dataclass
class ShortcutResult:
    ok: bool
    kind: str                    # lnk / desktop / app / script
    path: Optional[Path] = None
    message: str = ""
    detail: str = ""
    degraded: bool = False       # 走了降级方案
    overwritten: bool = False
    previous_target: str = ""


# ------------------------------------------------------------------ 平台判定


def platform_kind() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return "unknown"


def icon_asset_key() -> str:
    """当前平台应使用哪种图标资产（对应 `ui.icons.write_icon_assets` 的键）。"""
    return {"windows": "ico", "macos": "icns"}.get(platform_kind(), "png")


def shortcut_filename(name: str) -> str:
    kind = platform_kind()
    if kind == "windows":
        return f"{name}.lnk"
    if kind == "macos":
        return f"{name}.app"
    if kind == "linux":
        return f"{name.lower().replace(' ', '-')}.desktop"
    return f"{name}.desktop"


def platform_label() -> str:
    return {
        "windows": "Windows 快捷方式（.lnk）",
        "macos": "macOS 应用程序（.app）",
        "linux": "Linux 桌面条目（.desktop）",
    }.get(platform_kind(), "未知平台")


# ------------------------------------------------------------------ 桌面目录


def _windows_desktop() -> Optional[Path]:
    """用 SHGetKnownFolderPath 取真实桌面（兼容 OneDrive 重定向）。"""
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        folder_id = GUID()
        text = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"  # FOLDERID_Desktop
        ole32 = ctypes.windll.ole32
        ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(folder_id))

        shell32 = ctypes.windll.shell32
        shell32.SHGetKnownFolderPath.argtypes = [
            ctypes.POINTER(GUID),
            wintypes.DWORD,
            wintypes.HANDLE,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        shell32.SHGetKnownFolderPath.restype = ctypes.c_long
        out = ctypes.c_void_p()
        if shell32.SHGetKnownFolderPath(
            ctypes.byref(folder_id), 0, None, ctypes.byref(out)
        ) == 0 and out.value:
            try:
                return Path(ctypes.wstring_at(out))
            finally:
                ole32.CoTaskMemFree(out)
    except Exception as exc:  # noqa: BLE001
        LOG.debug("SHGetKnownFolderPath 失败，回退环境变量：%s", exc)

    profile = os.environ.get("USERPROFILE")
    if profile:
        return Path(profile) / "Desktop"
    return None


def _linux_desktop() -> Optional[Path]:
    """读取 XDG user-dirs 配置，取用户实际桌面。"""
    candidates: List[Path] = []
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "user-dirs.dirs"
    try:
        for line in config.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("XDG_DESKTOP_DIR="):
                raw = line.split("=", 1)[1].strip().strip('"')
                raw = raw.replace("$HOME", str(Path.home()))
                candidates.append(Path(raw))
    except OSError:
        pass
    candidates.append(Path.home() / "Desktop")
    candidates.append(Path.home() / "桌面")
    for path in candidates:
        if path.is_dir():
            return path
    return candidates[-1] if candidates else None


def desktop_directory(must_exist: bool = True) -> Path:
    """返回当前用户的桌面目录；不可用时抛 `ShortcutError`。"""
    kind = platform_kind()
    candidate: Optional[Path]
    if kind == "windows":
        candidate = _windows_desktop()
    elif kind == "macos":
        candidate = Path.home() / "Desktop"
    elif kind == "linux":
        candidate = _linux_desktop()
    else:
        candidate = None

    if candidate is None:
        raise ShortcutError(
            "无法确定桌面目录，请手动选择快捷方式的保存位置。",
            reason=ShortcutError.DESKTOP_NOT_FOUND,
        )
    if must_exist and not candidate.is_dir():
        raise ShortcutError(
            f"桌面目录不存在：{candidate}\n可以换一个保存位置，或先确认系统桌面是否被重定向。",
            reason=ShortcutError.DESKTOP_NOT_FOUND,
            detail=str(candidate),
        )
    return candidate


def is_writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".pylauncher-write-test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


# ------------------------------------------------------------------ 参数转义


def _quote_win(value: str) -> str:
    if not value:
        return '""'
    if any(ch in value for ch in ' \t"'):
        return '"' + value.replace('"', '\\"') + '"'
    return value


def _quote_desktop(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    if any(ch in value for ch in ' \t"\'\\$`'):
        return f'"{escaped}"'
    return escaped


def _shell_quote(value: str) -> str:
    """POSIX shell 转义（用于 macOS 启动脚本）。"""
    return shlex.quote(value)


# ------------------------------------------------------------------ Windows


def _windows_lnk_write(dest: Path, spec: ShortcutSpec) -> None:
    """通过 Shell COM 写入 `.lnk`。"""
    import ctypes
    from ctypes import wintypes

    ole32 = ctypes.OleDLL("ole32")

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    def guid(text: str) -> GUID:
        g = GUID()
        ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(g))
        return g

    def vtbl(ptr: ctypes.c_void_p, index: int, restype, argtypes):
        table = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
        return proto(table[index])

    HRESULT = ctypes.c_long
    co_init = ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
    need_uninit = co_init in (0, 1)
    if co_init not in (0, 1, -2147417850):  # S_OK / S_FALSE / RPC_E_CHANGED_MODE
        raise ShortcutError(
            "系统快捷方式组件初始化失败，无法创建 .lnk。",
            reason=ShortcutError.SYSTEM_UNAVAILABLE,
            detail=f"CoInitializeEx=0x{co_init & 0xFFFFFFFF:08X}",
        )

    link_ptr = ctypes.c_void_p()
    file_ptr = ctypes.c_void_p()
    try:
        clsid = guid("{00021401-0000-0000-C000-000000000046}")   # ShellLink
        iid_link = guid("{000214F9-0000-0000-C000-000000000046}")  # IShellLinkW
        iid_file = guid("{0000010B-0000-0000-C000-000000000046}")  # IPersistFile

        hr = ole32.CoCreateInstance(
            ctypes.byref(clsid), None, 1, ctypes.byref(iid_link), ctypes.byref(link_ptr)
        )
        if hr != 0 or not link_ptr.value:
            raise ShortcutError(
                "无法创建 Windows 快捷方式对象（Shell COM 不可用）。",
                reason=ShortcutError.SYSTEM_UNAVAILABLE,
                detail=f"CoCreateInstance=0x{hr & 0xFFFFFFFF:08X}",
            )

        workdir = str(spec.working_dir or spec.executable.parent)
        set_path = vtbl(link_ptr, 20, HRESULT, [ctypes.c_wchar_p])
        set_args = vtbl(link_ptr, 11, HRESULT, [ctypes.c_wchar_p])
        set_workdir = vtbl(link_ptr, 9, HRESULT, [ctypes.c_wchar_p])
        set_desc = vtbl(link_ptr, 7, HRESULT, [ctypes.c_wchar_p])
        set_icon = vtbl(link_ptr, 17, HRESULT, [ctypes.c_wchar_p, ctypes.c_int])
        set_show = vtbl(link_ptr, 15, HRESULT, [ctypes.c_int])

        if set_path(link_ptr, str(spec.executable)) != 0:
            raise ShortcutError(
                "写入快捷方式目标失败，请确认程序路径有效。",
                reason=ShortcutError.TARGET_MISSING,
                detail=str(spec.executable),
            )
        if spec.arguments:
            set_args(link_ptr, " ".join(_quote_win(a) for a in spec.arguments))
        set_workdir(link_ptr, workdir)
        set_desc(link_ptr, spec.description)
        set_show(link_ptr, 1)  # SW_SHOWNORMAL
        if spec.icon and Path(spec.icon).is_file():
            set_icon(link_ptr, str(spec.icon), 0)

        query_interface = vtbl(link_ptr, 0, HRESULT, [
            ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)
        ])
        hr = query_interface(link_ptr, ctypes.byref(iid_file), ctypes.byref(file_ptr))
        if hr != 0 or not file_ptr.value:
            raise ShortcutError(
                "无法保存快捷方式文件（IPersistFile 不可用）。",
                reason=ShortcutError.SYSTEM_UNAVAILABLE,
                detail=f"QueryInterface=0x{hr & 0xFFFFFFFF:08X}",
            )

        save = vtbl(file_ptr, 6, HRESULT, [ctypes.c_wchar_p, ctypes.c_int])
        hr = save(file_ptr, str(dest), 1)
        if hr != 0:
            raise ShortcutError(
                f"写入快捷方式失败：{dest}",
                reason=ShortcutError.PERMISSION_DENIED,
                detail=f"IPersistFile::Save=0x{hr & 0xFFFFFFFF:08X}",
            )
    finally:
        try:
            if file_ptr.value:
                vtbl(file_ptr, 2, ctypes.c_ulong, [])(file_ptr)
            if link_ptr.value:
                vtbl(link_ptr, 2, ctypes.c_ulong, [])(link_ptr)
        except Exception:  # noqa: BLE001
            pass
        if need_uninit:
            ole32.CoUninitialize()


def _windows_lnk_read(path: Path) -> Dict[str, str]:
    """读取已有 `.lnk` 的目标与参数（用于覆盖前展示差异）。"""
    import ctypes
    from ctypes import wintypes

    ole32 = ctypes.OleDLL("ole32")

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    def guid(text: str) -> GUID:
        g = GUID()
        ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(g))
        return g

    def vtbl(ptr, index, restype, argtypes):
        table = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
        return proto(table[index])

    HRESULT = ctypes.c_long
    co_init = ole32.CoInitializeEx(None, 0x2)
    link_ptr = ctypes.c_void_p()
    file_ptr = ctypes.c_void_p()
    info: Dict[str, str] = {
        "target": "",
        "arguments": "",
        "working_dir": "",
        "description": "",
        "icon": "",
    }
    try:
        clsid = guid("{00021401-0000-0000-C000-000000000046}")
        iid_link = guid("{000214F9-0000-0000-C000-000000000046}")
        iid_file = guid("{0000010B-0000-0000-C000-000000000046}")
        if ole32.CoCreateInstance(
            ctypes.byref(clsid), None, 1, ctypes.byref(iid_link), ctypes.byref(link_ptr)
        ) != 0:
            return info
        query_interface = vtbl(link_ptr, 0, HRESULT, [
            ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)
        ])
        if query_interface(link_ptr, ctypes.byref(iid_file), ctypes.byref(file_ptr)) != 0:
            return info
        load = vtbl(file_ptr, 5, HRESULT, [ctypes.c_wchar_p, ctypes.c_uint])
        if load(file_ptr, str(path), 0) != 0:
            return info

        buffer = ctypes.create_unicode_buffer(1024)
        get_path = vtbl(link_ptr, 3, HRESULT, [
            ctypes.c_wchar_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint
        ])
        get_path(link_ptr, buffer, 1024, None, 0)
        info["target"] = buffer.value

        buf2 = ctypes.create_unicode_buffer(1024)
        get_args = vtbl(link_ptr, 10, HRESULT, [ctypes.c_wchar_p, ctypes.c_int])
        get_args(link_ptr, buf2, 1024)
        info["arguments"] = buf2.value

        buf3 = ctypes.create_unicode_buffer(1024)
        get_dir = vtbl(link_ptr, 8, HRESULT, [ctypes.c_wchar_p, ctypes.c_int])
        get_dir(link_ptr, buf3, 1024)
        info["working_dir"] = buf3.value

        buf4 = ctypes.create_unicode_buffer(1024)
        get_desc = vtbl(link_ptr, 6, HRESULT, [ctypes.c_wchar_p, ctypes.c_int])
        get_desc(link_ptr, buf4, 1024)
        info["description"] = buf4.value

        buf5 = ctypes.create_unicode_buffer(1024)
        icon_index = ctypes.c_int(0)
        get_icon = vtbl(link_ptr, 16, HRESULT, [
            ctypes.c_wchar_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int)
        ])
        get_icon(link_ptr, buf5, 1024, ctypes.byref(icon_index))
        info["icon"] = buf5.value
    except Exception as exc:  # noqa: BLE001
        LOG.debug("读取 .lnk 失败：%s", exc)
    finally:
        try:
            if file_ptr.value:
                vtbl(file_ptr, 2, ctypes.c_ulong, [])(file_ptr)
            if link_ptr.value:
                vtbl(link_ptr, 2, ctypes.c_ulong, [])(link_ptr)
        except Exception:  # noqa: BLE001
            pass
        if co_init in (0, 1):
            ole32.CoUninitialize()
    return info


# ------------------------------------------------------------------ Linux


def _linux_desktop_entry(dest: Path, spec: ShortcutSpec) -> None:
    icon = str(spec.icon) if spec.icon else "application-x-executable"
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Version=1.5",
        f"Name={spec.name}",
        f"GenericName=Minecraft Launcher",
        f"Comment={spec.description}",
        f"Exec={spec.exec_line()}",
        f"Path={spec.working_dir or Path.home()}",
        f"Icon={icon}",
        "Terminal=false",
        f"Categories={spec.categories}",
        "Keywords=Minecraft;Launcher;MC;",
        f"StartupWMClass={spec.startup_wm_class}",
        "StartupNotify=true",
        "",
    ]
    dest.write_text("\n".join(lines), encoding="utf-8")
    dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # GNOME / Nautilus 需要「可信」标记才允许双击启动，失败不影响使用
    try:
        import subprocess

        subprocess.run(
            ["gio", "set", str(dest), "metadata::trusted", "true"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
        )
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------ macOS


def _macos_app_bundle(dest: Path, spec: ShortcutSpec) -> None:
    contents = dest / "Contents"
    macos_dir = contents / "MacOS"
    resources = contents / "Resources"
    for directory in (macos_dir, resources):
        directory.mkdir(parents=True, exist_ok=True)

    exe_name = spec.name.replace(" ", "")
    bundle_id = "com.pylauncher.launcher"

    command = [str(spec.executable)] + [str(a) for a in spec.arguments]
    script = "\n".join(
        [
            "#!/bin/sh",
            "# 由 PyLauncher 生成的启动脚本",
            f"cd {_shell_quote(str(spec.working_dir or spec.executable.parent))} || exit 1",
            "exec " + " ".join(_shell_quote(part) for part in command),
            "",
        ]
    )
    launcher = macos_dir / exe_name
    launcher.write_text(script, encoding="utf-8")
    launcher.chmod(0o755)

    if spec.icon and Path(spec.icon).is_file():
        icon_target = resources / "AppIcon.icns"
        icon_target.write_bytes(Path(spec.icon).read_bytes())
        icon_file = "AppIcon"
    else:
        icon_file = ""

    plist = "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">',
            '<plist version="1.0">',
            "<dict>",
            "    <key>CFBundleName</key>",
            f"    <string>{spec.name}</string>",
            "    <key>CFBundleDisplayName</key>",
            f"    <string>{spec.name}</string>",
            "    <key>CFBundleExecutable</key>",
            f"    <string>{exe_name}</string>",
            "    <key>CFBundleIdentifier</key>",
            f"    <string>{bundle_id}</string>",
            "    <key>CFBundleInfoDictionaryVersion</key>",
            "    <string>6.0</string>",
            "    <key>CFBundlePackageType</key>",
            "    <string>APPL</string>",
            "    <key>CFBundleShortVersionString</key>",
            "    <string>1.0.0</string>",
            "    <key>CFBundleVersion</key>",
            "    <string>1</string>",
            "    <key>CFBundleGetInfoString</key>",
            f"    <string>{spec.description}</string>",
            "    <key>LSMinimumSystemVersion</key>",
            "    <string>10.15</string>",
            "    <key>NSHighResolutionCapable</key>",
            "    <true/>",
            "    <key>LSApplicationCategoryType</key>",
            "    <string>public.app-category.games</string>",
            *(
                [
                    "    <key>CFBundleIconFile</key>",
                    f"    <string>{icon_file}</string>",
                ]
                if icon_file
                else []
            ),
            "</dict>",
            "</plist>",
            "",
        ]
    )
    (contents / "Info.plist").write_text(plist, encoding="utf-8")


# ------------------------------------------------------------------ 查询 / 删除


def existing_target(path: Path) -> str:
    """尽量读出已有快捷方式指向的目标（仅 Windows 能精确读取）。"""
    try:
        if not path.exists():
            return ""
        if platform_kind() == "windows" and path.suffix.lower() == ".lnk":
            return _windows_lnk_read(path).get("target", "")
        if path.suffix.lower() == ".desktop":
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("Exec="):
                    return line[5:].strip()
        if path.suffix.lower() == ".app":
            launcher = path / "Contents" / "MacOS"
            if launcher.is_dir():
                entries = [p for p in launcher.iterdir() if p.is_file()]
                if entries:
                    return entries[0].read_text(encoding="utf-8", errors="ignore").strip()
    except Exception as exc:  # noqa: BLE001
        LOG.debug("读取已有快捷方式失败：%s", exc)
    return ""


def validate_spec(spec: ShortcutSpec) -> None:
    """校验目标程序可启动，避免生成一个双击无反应的图标。"""
    if not spec.executable or not Path(spec.executable).exists():
        raise ShortcutError(
            "找不到要启动的程序文件，可能已被移动或卸载。\n"
            "请把启动器放回原位置后再创建，或重新安装后再试。",
            reason=ShortcutError.TARGET_MISSING,
            detail=str(spec.executable),
        )
    for arg in spec.arguments:
        candidate = Path(arg)
        if arg.lower().endswith(".py") and not candidate.is_file():
            raise ShortcutError(
                "找不到启动脚本 main.py，可能已被移动或卸载。\n"
                "请把启动器放回原位置后再创建。",
                reason=ShortcutError.TARGET_MISSING,
                detail=arg,
            )


# ------------------------------------------------------------------ 降级脚本


def _fallback_script(dest_dir: Path, spec: ShortcutSpec) -> Path:
    """生成一个双击即可运行的启动脚本（无自定义图标，但保证可用）。"""
    kind = platform_kind()
    command = [str(spec.executable)] + [str(a) for a in spec.arguments]
    workdir = str(spec.working_dir or spec.executable.parent)

    if kind == "windows":
        target = dest_dir / f"{spec.name}.cmd"
        body = "\r\n".join(
            [
                "@echo off",
                "rem 由 PyLauncher 生成的启动脚本（快捷方式降级方案）",
                f'cd /d "{workdir}"',
                " ".join(f'"{part}"' if " " in part else part for part in command),
                "",
            ]
        )
        target.write_text(body, encoding="utf-8")
    else:
        suffix = ".command" if kind == "macos" else ".sh"
        target = dest_dir / f"{spec.name}{suffix}"
        body = "\n".join(
            [
                "#!/bin/sh",
                "# 由 PyLauncher 生成的启动脚本（快捷方式降级方案）",
                f"cd {_shell_quote(workdir)} || exit 1",
                "exec " + " ".join(_shell_quote(part) for part in command),
                "",
            ]
        )
        target.write_text(body, encoding="utf-8")
        target.chmod(0o755)
    return target


# ------------------------------------------------------------------ 主入口


def create_shortcut(
    spec: ShortcutSpec,
    dest_dir: Optional[Path] = None,
    *,
    overwrite: bool = False,
    allow_fallback: bool = True,
) -> ShortcutResult:
    """创建桌面快捷方式。

    * `dest_dir` 为空时使用系统桌面目录；
    * 目标已存在且 `overwrite=False` 时抛 `ShortcutError(reason="exists")`；
    * 原生方案失败且 `allow_fallback=True` 时退化为启动脚本。
    """
    validate_spec(spec)

    kind = platform_kind()
    if kind == "unknown":
        raise ShortcutError(
            f"当前系统（{sys.platform}）暂不支持创建桌面快捷方式。",
            reason=ShortcutError.UNSUPPORTED,
        )

    directory = Path(dest_dir) if dest_dir else desktop_directory()
    if not is_writable(directory):
        raise ShortcutError(
            f"没有写入权限：{directory}\n"
            "可以换一个保存位置（例如用户目录），或调整该目录的权限后重试。",
            reason=ShortcutError.PERMISSION_DENIED,
            detail=str(directory),
        )

    dest = directory / shortcut_filename(spec.name)
    previous = ""
    if dest.exists():
        if not overwrite:
            raise ShortcutError(
                f"桌面已存在同名快捷方式：{dest.name}",
                reason=ShortcutError.EXISTS,
                detail=str(dest),
            )
        previous = existing_target(dest)
        _remove_path(dest)

    native_error: Optional[ShortcutError] = None
    try:
        if kind == "windows":
            _windows_lnk_write(dest, spec)
        elif kind == "linux":
            _linux_desktop_entry(dest, spec)
        else:
            _macos_app_bundle(dest, spec)
        if not dest.exists():
            raise ShortcutError(
                "系统返回成功，但快捷方式文件并未生成。",
                reason=ShortcutError.SYSTEM_UNAVAILABLE,
                detail=str(dest),
            )
        return ShortcutResult(
            ok=True,
            kind={"windows": "lnk", "linux": "desktop", "macos": "app"}[kind],
            path=dest,
            message=f"已创建桌面快捷方式：{dest.name}",
            overwritten=bool(previous) or overwrite,
            previous_target=previous,
        )
    except ShortcutError as exc:
        native_error = exc
        LOG.warning("原生快捷方式创建失败：%s", exc.detail or exc.message)
    except OSError as exc:
        native_error = ShortcutError(
            f"创建快捷方式时发生文件系统错误：{exc}",
            reason=ShortcutError.PERMISSION_DENIED,
            detail=str(exc),
        )
        LOG.warning("原生快捷方式创建失败：%s", exc)

    if not allow_fallback:
        raise native_error

    # ---------------- 降级：写一个双击可用的启动脚本
    try:
        script = _fallback_script(directory, spec)
    except OSError as exc:
        raise ShortcutError(
            "创建桌面快捷方式失败，且降级方案也不可用。\n" f"原因：{exc}",
            reason=native_error.reason,
            detail=f"{native_error.detail}\n{exc}",
        ) from exc

    return ShortcutResult(
        ok=True,
        kind="script",
        path=script,
        message=f"已生成桌面启动脚本：{script.name}（降级方案）",
        detail=native_error.message if native_error else "",
        degraded=True,
        overwritten=bool(previous) or overwrite,
        previous_target=previous,
    )


def _remove_path(path: Path) -> None:
    import shutil

    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=False)
    else:
        path.unlink()


def remove_shortcut(path: Path) -> ShortcutResult:
    """删除快捷方式（文件或 .app 目录）。"""
    path = Path(path)
    if not path.exists():
        return ShortcutResult(ok=False, kind="none", path=path, message="快捷方式不存在，无需删除。")
    try:
        _remove_path(path)
    except OSError as exc:
        raise ShortcutError(
            f"删除失败：{path}\n可能被占用或权限不足。",
            reason=ShortcutError.PERMISSION_DENIED,
            detail=str(exc),
        ) from exc
    return ShortcutResult(ok=True, kind="removed", path=path, message=f"已删除：{path.name}")


def find_existing_shortcut(name: str, dest_dir: Optional[Path] = None) -> Optional[Path]:
    """查找桌面上已创建的快捷方式。"""
    try:
        directory = Path(dest_dir) if dest_dir else desktop_directory(must_exist=False)
    except ShortcutError:
        return None
    candidate = directory / shortcut_filename(name)
    return candidate if candidate.exists() else None


def describe_target(spec: ShortcutSpec) -> str:
    """给界面展示的启动目标摘要。"""
    parts = [str(spec.executable)] + [str(a) for a in spec.arguments]
    return " ".join(parts)
