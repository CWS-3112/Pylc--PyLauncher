"""PyLauncher — 一个模块化的《我的世界》Java 版启动器。

版本号**只在一处定义**（`launcher.core.constants.LAUNCHER_VERSION`）。
此前 `__init__.py` 与 `core/constants.py` 各存一份，出现过界面标题显示 1.0.0
而快捷方式描述 / 启动品牌参数写 1.0.0 的不一致，现在统一从核心常量转发。
"""

from .core.constants import LAUNCHER_NAME, LAUNCHER_VERSION

__version__ = LAUNCHER_VERSION

__all__ = ["LAUNCHER_NAME", "LAUNCHER_VERSION", "__version__"]
