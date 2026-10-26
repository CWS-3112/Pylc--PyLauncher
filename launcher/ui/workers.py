"""后台任务线程：把耗时操作移出 UI 线程，避免界面卡死。

两个必须遵守的约束（历史上踩过坑的地方）：

1. **绝不能调用 `QThread.terminate()`**。强行杀死正在执行 Python 字节码的线程
   会让解释器处于未定义状态，表现为随机崩溃（`Fatal Python error` 或静默退出）。
   取消一律走「协作式」：由任务自己检查 `is_cancelled()` 后自行退出。
2. **线程对象必须有活引用**。PySide6 里 `QThread` 被 Python 垃圾回收时尚未结束，
   会直接 abort（`QThread: Destroyed while thread is still running`）。
   因此统一交给 `start_worker()` 托管生命周期。
"""

from __future__ import annotations

import functools
import inspect
from typing import Any, Callable, List, Optional

from PySide6.QtCore import QThread, Signal


class Worker(QThread):
    """通用工作线程。

    传给 `fn` 的两个回调分别是：

    * `on_progress(done: int, total: int)`
    * `on_message(text: str)`

    `fn` 的返回值通过 `succeeded` 信号传出；异常通过 `failed` 传出。

    任务函数应周期性调用第二个回调对象之外的 `cancelled()` 判定来尽早退出；
    也可以接收 `should_cancel` 关键字参数（见 `start_worker`）。
    """

    progress = Signal(int, int)          # done, total
    message = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        fn: Callable[..., Any],
        *args,
        parent=None,
        **kwargs,
    ) -> None:
        super().__init__(parent)
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._result: Any = None
        self._error: Optional[str] = None
        self._cancel_hooks: List[Callable[[], None]] = []
        self._wants_cancel_arg = False
        for variants in _own_functions(fn):
            try:
                sig = inspect.signature(variants)
            except (TypeError, ValueError):  # pragma: no cover - 内建对象
                continue
            params = [
                p for name, p in sig.parameters.items()
                if name not in ("self", "cls")
            ]
            if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params) or any(
                p.name == "should_cancel" for p in params
            ):
                self._wants_cancel_arg = True
                break

    # ------------------------------------------------------------ 取消

    def add_cancel_hook(self, hook: Callable[[], None]) -> None:
        """注册取消回调：`request_cancel()` 时会被调用（协作式，不杀线程）。"""
        self._cancel_hooks.append(hook)

    @property
    def cancel_requested(self) -> bool:
        return self.isInterruptionRequested()

    def request_cancel(self) -> None:
        """协作式取消：只发信号，等待线程自己跑完当前步骤后退出。"""
        if not self.isRunning():
            return
        self.requestInterruption()
        for hook in self._cancel_hooks:
            try:
                hook()
            except Exception:  # noqa: BLE001 - 取消钩子失败不应影响主线程
                import logging

                logging.getLogger("pylauncher").warning("取消钩子执行失败", exc_info=True)

    # ------------------------------------------------------------ 线程体

    def should_cancel(self) -> bool:
        """供任务函数在循环里检查是否需要提前退出。"""
        return self.isInterruptionRequested()

    def run(self) -> None:  # noqa: D102
        def on_progress(done: int, total: int) -> None:
            self.progress.emit(int(done), int(total))

        def on_message(text: str) -> None:
            self.message.emit(str(text))

        kwargs = dict(self._kwargs)
        if self._wants_cancel_arg:
            kwargs["should_cancel"] = self.should_cancel

        try:
            self._result = self._fn(on_progress, on_message, *self._args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)
        else:
            self.succeeded.emit(self._result)

    def _fail(self, exc: BaseException) -> None:
        # 用户主动取消不应当显示成「失败」
        if self.isInterruptionRequested():
            self.succeeded.emit(None)
            return
        import traceback

        self._error = f"{exc}\n\n{traceback.format_exc(limit=6)}"
        self.failed.emit(self._error)


def _own_functions(fn: Callable[..., Any]):
    """展开 `functools.partial` 之类的包装，拿到真正携带签名的函数对象。"""
    seen: List[Any] = []
    current: Any = fn
    for _ in range(6):
        if current is None or any(current is s for s in seen):
            break
        seen.append(current)
        yield current
        if isinstance(current, functools.partial):
            current = current.func
            continue
        current = getattr(current, "__wrapped__", None)
    if hasattr(fn, "__call__"):
        yield fn.__call__


def start_worker(owner, worker: Worker, cancel_hook: Optional[Callable[[], None]] = None) -> Worker:
    """托管 Worker 生命周期，避免「线程还在跑就被 Python 回收」导致的崩溃。

    * 把自己登记到 `owner._workers`，保证引用一直存活；
    * 结束后自动从登记表中移除并 `deleteLater()`；
    * 可选地把 `cancel_hook` 接到协作式取消上（例如 `DownloadManager.cancel`）。
    """
    workers: List[Worker] = getattr(owner, "_workers", None)
    if workers is None:
        workers = []
        owner._workers = workers
    workers.append(worker)

    if cancel_hook is not None:
        worker.add_cancel_hook(cancel_hook)

    def _cleanup() -> None:
        """线程结束后从登记表移除即可。

        不要在这里 `deleteLater()`：结束语之后再碰这个对象（比如 UI 侧接着调用
        `wait()` 做兜底等待）就会变成「C++ object already deleted」运行时错误。
        线程已结束，剩下的交给 Python 引用计数回收就是安全的。
        """
        try:
            workers.remove(worker)
        except ValueError:
            pass

    worker.finished.connect(_cleanup)
    worker.start()
    return worker


def wait_worker(worker: Worker, timeout_ms: int = 3000) -> None:
    """温和地等待线程退出（不做 terminate，避免崩溃）。

    线程可能已经结束甚至被回收，这里必须防御性调用。
    """
    try:
        if worker.isRunning():
            worker.wait(timeout_ms)
    except RuntimeError:
        pass
