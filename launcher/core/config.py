"""配置与实例（Instance）持久化。

有两层数据：

* **全局配置** —— 游戏目录、镜像源、Java、内存、窗口分辨率等，存于 `config.json`。
* **实例配置** —— 每个版本一条独立记录（`Instance`），支持「多版本隔离」：
  独立游戏目录、独立 Java、独立 JVM 参数 / 内存，存于同一文件的 `instances` 段。

所有写入都是「立即落盘」的，进程崩溃也不会丢配置。
"""

from __future__ import annotations

import copy
import json
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import paths
from .utils import LOG, ensure_dir

# ------------------------------------------------------------------ 默认值

DEFAULTS: Dict[str, Any] = {
    "game_dir": str(paths.default_game_dir()),
    "mirror": "bmclapi",
    "java": {
        "auto_detect": True,
        "custom_paths": [],   # List[str]
        "selected": "",       # "" 表示自动选择最优
    },
    "memory": {
        "auto": True,
        "max_mb": 4096,
        "min_mb": 512,
    },
    "window": {
        "fullscreen": False,
        "width": 1280,
        "height": 720,
    },
    "launch": {
        "launcher_visible": True,   # 启动后是否保留启动器窗口
        "show_log_window": True,
        "wrapper": "",              # 包装命令，如 "primusrun" / "gamemoderun"
        "extra_jvm_args": "",
        "extra_game_args": "",
        "custom_main_class": "",    # 留空则用版本 json 中的 mainClass
    },
    "ui": {
        "theme": "system",
        "last_account": "",
        "last_version": "",
    },
    "microsoft": {
        "client_id": "",   # 自建 Azure 应用的 client_id，留空则微软登录不可用
    },
    "shortcut": {
        "name": "",            # 快捷方式显示名称，空 = 使用启动器名称
        "last_path": "",       # 最近一次创建的快捷方式路径
        "last_created": "",    # 最近一次创建时间
        "auto_create": True,   # 首次运行是否自动创建桌面快捷方式
        "auto_done": False,    # 已尝试过自动创建（成功与否都置位，避免反复打扰）
    },
    "instances": {},   # version_id -> Instance 序列化后的 dict
    "selected_instance": "",
}


# ------------------------------------------------------------------ 实例


@dataclass
class Instance:
    """一个可启动的版本实例（含隔离设置）。"""

    version_id: str
    display_name: str = ""
    isolated: bool = True            # 是否使用独立游戏目录
    java_path: str = ""              # 空 = 跟随全局
    jvm_args: str = ""               # 实例级额外 JVM 参数
    game_args: str = ""              # 实例级额外游戏参数
    max_memory_mb: int = 0           # 0 = 跟随全局
    min_memory_mb: int = 0
    width: int = 0                   # 0 = 跟随全局
    height: int = 0
    fullscreen: Optional[bool] = None  # None = 跟随全局
    wrapper: str = ""
    env: Dict[str, str] = field(default_factory=dict)
    last_played: str = ""

    # -------------------------------------------------------------- 路径

    @property
    def name(self) -> str:
        return self.display_name or self.version_id

    def game_directory(self, base_game_dir: Path) -> Path:
        """实例实际使用的游戏目录（工作目录）。"""
        from . import paths as P

        if self.isolated:
            d = P.isolate_dir(base_game_dir, self.version_id)
            P.mods_dir(d)
            P.resources_dir(d)
            P.saves_dir(d)
            return d
        return base_game_dir

    def mods_directory(self, base_game_dir: Path) -> Path:
        from . import paths as P

        return P.mods_dir(self.game_directory(base_game_dir))

    # -------------------------------------------------------------- 序列化

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Instance":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        cleaned = {k: v for k, v in data.items() if k in known}
        cleaned.setdefault("version_id", data.get("version_id", ""))
        return cls(**cleaned)


# ------------------------------------------------------------------ 配置


class Config:
    """线程安全的 JSON 配置对象，支持点号路径读写。"""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else paths.config_file()
        ensure_dir(self._path.parent)
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = copy.deepcopy(DEFAULTS)
        self._instances: Dict[str, Instance] = {}
        self.load()

    # ------------------------------------------------------------ 读写

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> None:
        with self._lock:
            if self._path.exists():
                try:
                    raw = json.loads(self._path.read_text(encoding="utf-8"))
                    self._deep_update(self._data, raw)
                    LOG.info("已加载配置：%s", self._path)
                except Exception as exc:  # noqa: BLE001
                    LOG.warning("配置文件损坏，使用默认值：%s", exc)
                    bak = self._path.with_suffix(".json.bak")
                    try:
                        self._path.replace(bak)
                    except OSError:
                        pass
            raw_instances = self._data.pop("instances", {}) or {}
            self._instances = {
                vid: Instance.from_dict({**data, "version_id": vid})
                for vid, data in raw_instances.items()
            }
            self._data["instances"] = {}

    def save(self) -> None:
        with self._lock:
            data = copy.deepcopy(self._data)
            data["instances"] = {vid: ins.to_dict() for vid, ins in self._instances.items()}
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._path)

    # ------------------------------------------------------------ 通用访问

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value: Any, autosave: bool = True) -> None:
        with self._lock:
            parts = dotted.split(".")
            node = self._data
            for part in parts[:-1]:
                node = node.setdefault(part, {})
                if not isinstance(node, dict):
                    raise TypeError(f"配置项 {dotted} 的父节点不是对象")
            node[parts[-1]] = value
            if autosave:
                self.save()

    def raw(self) -> Dict[str, Any]:
        return self._data

    # ------------------------------------------------------------ 实例管理

    def instances(self) -> Dict[str, Instance]:
        return self._instances

    def all_instances(self) -> List[Instance]:
        return list(self._instances.values())

    def get_instance(self, version_id: str, create: bool = False) -> Instance:
        with self._lock:
            ins = self._instances.get(version_id)
            if ins is None and create:
                ins = Instance(version_id=version_id)
                self._instances[version_id] = ins
                self.save()
            return ins

    def put_instance(self, instance: Instance) -> None:
        with self._lock:
            self._instances[instance.version_id] = instance
            self.save()

    def remove_instance(self, version_id: str) -> None:
        with self._lock:
            self._instances.pop(version_id, None)
            self.save()

    # ------------------------------------------------------------ 内部

    @staticmethod
    def _deep_update(dst: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
        for key, value in src.items():
            if isinstance(value, dict) and isinstance(dst.get(key), dict):
                Config._deep_update(dst[key], value)
            else:
                dst[key] = value
        return dst


# ------------------------------------------------------------------ 单例


_config: Optional[Config] = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config


def set_config(path: Path | str) -> Config:
    global _config
    _config = Config(Path(path))
    return _config
