"""网络层：带重试的会话、多线程下载器、断点与校验。"""

from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import requests

from .constants import DEFAULT_MIRROR, HTTP_TIMEOUT, MIRRORS
from .utils import LOG, ensure_dir, sha1_of_file

ProgressCallback = Callable[[int, int, int], None]
# (已完成数, 总任务数, 本次完成数) -> None


# -------------------------------------------------------------------- 会话


# (connect, read) 分开设置：连接要快fail，读取大文件时才允许久一点
CONNECT_TIMEOUT = 10

# 源可用性探测结果缓存
_SOURCE_CACHE: Dict[str, Tuple[bool, float]] = {}
_SOURCE_CACHE_TTL = 60.0


class Session:
    """带自动重试的 HTTP 会话。"""

    def __init__(self, timeout: int = HTTP_TIMEOUT, retries: int = 3) -> None:
        self.timeout = timeout
        self.retries = retries
        self._s = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=32, pool_maxsize=32, max_retries=0
        )
        self._s.mount("https://", adapter)
        self._s.mount("http://", adapter)
        self._s.headers.update({
            "User-Agent": "PyLauncher/1.0 (+https://github.com/)",
            "Accept": "application/json, */*",
        })

    def get_json(self, url: str, **kwargs) -> dict:
        resp = self.request("GET", url, **kwargs)
        try:
            return resp.json()
        finally:
            self._close(resp)

    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        """发起请求，失败自动重试；`stream=True` 时调用方负责关闭。"""
        last_err: Optional[Exception] = None
        stream = bool(kwargs.get("stream"))
        kwargs.setdefault("timeout", (CONNECT_TIMEOUT, self.timeout))
        for attempt in range(self.retries):
            resp: Optional[requests.Response] = None
            try:
                resp = self._s.request(method, url, **kwargs)
                if resp.status_code >= 500 or resp.status_code == 429:
                    raise requests.HTTPError(f"HTTP {resp.status_code} @ {url}")
                resp.raise_for_status()
                return resp
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                if resp is not None and not stream:
                    self._close(resp)
                if attempt + 1 < self.retries:
                    delay = min(0.5 * (2 ** attempt), 4.0)
                    LOG.debug("请求失败(%s)，%.1fs 后重试：%s", attempt + 1, delay, exc)
                    time.sleep(delay)
        raise RuntimeError(f"请求失败：{url} —— {last_err}")

    @staticmethod
    def _close(resp: requests.Response) -> None:
        try:
            resp.close()
        except Exception:  # noqa: BLE001
            pass

    def download(self, url: str, dest: Path, sha1: str = "", size: int = 0) -> Path:
        """下载到 `dest`，可选校验 SHA1 / 大小；成功返回路径。

        任何失败都会清掉 `.part` 半成品，避免残留文件被误判为「已下载」。
        """
        if dest.exists():
            if (not size or dest.stat().st_size == size) and (not sha1 or sha1_of_file(dest) == sha1):
                return dest
        ensure_dir(dest.parent)
        tmp = dest.with_suffix(dest.suffix + ".part")
        resp = None
        written = 0
        try:
            resp = self.request("GET", url, stream=True)
            expected = size or int(resp.headers.get("Content-Length") or 0)
            written = 0
            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(1 << 16):
                    if chunk:
                        f.write(chunk)
                        written += len(chunk)
            # 长度不符通常意味着连接被中途掐断，直接判失败触发换源重试
            if expected and written != expected:
                raise ValueError(
                    f"下载不完整：{dest.name}（收到 {written} 字节，应为 {expected} 字节）"
                )
            if sha1 and sha1_of_file(tmp) != sha1:
                raise ValueError(f"文件校验失败（SHA1 不匹配）：{dest.name}")
            if not size and not sha1 and written == 0:
                raise ValueError(f"下载内容为空：{dest.name}")
            tmp.replace(dest)
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        finally:
            if resp is not None:
                self._close(resp)
        return dest


session = Session()


# -------------------------------------------------------------------- 源健康检查


def other_mirror(mirror_key: str) -> str:
    """返回与给定源相对的另一个下载源。"""
    return "official" if mirror_key == DEFAULT_MIRROR else DEFAULT_MIRROR


def probe_source(mirror_key: str, timeout: float = 8.0) -> bool:
    """轻量探测某个下载源是否可用（只拉版本清单，结果缓存 60 秒）。

    BMCLAPI 这类公益镜像经常整体不可用，安装前先探一次就能自动绕开，
    而不是让用户面对一堆「任务失败」。
    """
    now = time.time()
    cached = _SOURCE_CACHE.get(mirror_key)
    if cached and now - cached[1] < _SOURCE_CACHE_TTL:
        return cached[0]

    mirror = MIRRORS.get(mirror_key) or MIRRORS[DEFAULT_MIRROR]
    ok = False
    try:
        resp = requests.get(
            mirror.version_manifest,
            timeout=(5.0, timeout),
            headers={"User-Agent": "PyLauncher/1.0"},
        )
        ok = resp.status_code == 200 and len(resp.content) > 1000
    except Exception as exc:  # noqa: BLE001
        LOG.debug("下载源 %s 探测失败：%s", mirror_key, exc)
        ok = False
    _SOURCE_CACHE[mirror_key] = (ok, now)
    LOG.info("下载源 %s 探测结果：%s", mirror_key, "可用" if ok else "不可用")
    return ok


def invalidate_source_cache() -> None:
    """源状态变化（例如用户手动切源）后清空缓存。"""
    _SOURCE_CACHE.clear()


def available_mirror(preferred: str = "") -> str:
    """返回实际可用的下载源：优先 `preferred`，不通则自动切到另一个。"""
    preferred = preferred or DEFAULT_MIRROR
    if preferred in MIRRORS and probe_source(preferred):
        return preferred
    backup = other_mirror(preferred)
    if backup in MIRRORS and probe_source(backup):
        LOG.warning("下载源 %s 不可用，已自动切换到 %s", preferred, backup)
        return backup
    return preferred


def get_json_with_fallback(urls: Sequence[str]) -> tuple:
    """依次尝试多个地址，返回 `(数据, 实际使用的地址)`；全部失败则抛出最后一个异常。"""
    last: Optional[Exception] = None
    for url in urls:
        try:
            return session.get_json(url), url
        except Exception as exc:  # noqa: BLE001
            last = exc
            LOG.warning("请求失败，尝试下一个源：%s", exc)
    raise last if last else RuntimeError("没有可用的下载地址")


def download_with_fallback(urls: Sequence[str], dest: Path, sha1: str = "", size: int = 0) -> Path:
    """依次尝试多个地址下载，全部失败则抛出最后一个异常。"""
    last: Optional[Exception] = None
    for url in urls:
        try:
            return session.download(url, dest, sha1, size)
        except Exception as exc:  # noqa: BLE001
            last = exc
            LOG.warning("下载失败，尝试下一个源：%s", exc)
    raise last if last else RuntimeError("没有可用的下载地址")


# -------------------------------------------------------------------- 任务


@dataclass
class DownloadTask:
    """一个待下载文件。"""

    name: str
    url: str
    dest: Path
    sha1: str = ""
    size: int = 0
    category: str = "library"   # library / asset / jar / index / mod…
    optional: bool = False      # 失败是否可忽略
    fallback_url: str = ""      # 主地址失败后自动尝试的备用地址
    extra_urls: List[str] = field(default_factory=list)  # 更靠后的候选源

    @property
    def urls(self) -> List[str]:
        """按顺序去重后的候选地址列表。"""
        merged = [self.url]
        if self.fallback_url:
            merged.append(self.fallback_url)
        merged += list(self.extra_urls)
        seen = set()
        out = []
        for u in merged:
            if u and u not in seen:
                seen.add(u)
                out.append(u)
        return out

    @property
    def exists_ok(self) -> bool:
        if not self.dest.exists():
            return False
        if self.size and self.dest.stat().st_size != self.size:
            return False
        if self.sha1:
            try:
                return sha1_of_file(self.dest) == self.sha1
            except OSError:
                return False
        return True


@dataclass
class DownloadResult:
    task: DownloadTask
    ok: bool
    error: str = ""
    skipped: bool = False


@dataclass
class DownloadStats:
    total: int = 0
    done: int = 0
    failed: int = 0
    skipped: int = 0
    bytes_done: int = 0
    bytes_total: int = 0
    failures: List[DownloadResult] = field(default_factory=list)

    @property
    def percent(self) -> float:
        if self.total == 0:
            return 100.0
        return (self.done + self.failed + self.skipped) / self.total * 100.0


class DownloadManager:
    """并发下载器：可被 UI 层取消，实时回调进度。"""

    def __init__(self, workers: int = 8) -> None:
        self.workers = workers
        self._cancel = Event()

    # ---------------------------------------------------------- 控制

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    # ---------------------------------------------------------- 执行

    def run(
        self,
        tasks: Sequence[DownloadTask],
        on_progress: Optional[ProgressCallback] = None,
    ) -> DownloadStats:
        self._cancel.clear()
        stats = DownloadStats(total=len(tasks))
        stats.bytes_total = sum(t.size for t in tasks)
        if not tasks:
            return stats

        LOG.info("开始下载 %d 个文件（%d 并发）", len(tasks), self.workers)
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures: Dict[Future, DownloadTask] = {
                pool.submit(self._worker, t): t for t in tasks
            }
            try:
                for fut in as_completed(futures):
                    task = futures[fut]
                    try:
                        result = fut.result()
                    except Exception as exc:  # noqa: BLE001
                        result = DownloadResult(task=task, ok=False, error=str(exc))
                    if result.skipped:
                        stats.skipped += 1
                    elif result.ok:
                        stats.done += 1
                    else:
                        stats.failed += 1
                        stats.failures.append(result)
                        if task.optional:
                            LOG.debug("可选文件下载失败，忽略：%s", task.name)
                        else:
                            LOG.warning("下载失败：%s —— %s", task.name, result.error)
                    stats.bytes_done += task.size
                    if on_progress:
                        on_progress(stats.done + stats.failed + stats.skipped, stats.total, 1)
                    if self.cancelled:
                        for f in futures:
                            f.cancel()
                        break
            except KeyboardInterrupt:
                self.cancel()
                raise
        if self.cancelled:
            LOG.info("下载已取消，等待进行中的任务退出…")
        return stats

    # ---------------------------------------------------------- 内部

    def _worker(self, task: DownloadTask) -> DownloadResult:
        if self.cancelled:
            return DownloadResult(task=task, ok=False, error="已取消", skipped=False)
        if task.exists_ok:
            return DownloadResult(task=task, ok=True, skipped=True)
        urls = task.urls
        last: Optional[Exception] = None
        for url in urls:
            if self.cancelled:
                return DownloadResult(task=task, ok=False, error="已取消")
            try:
                session.download(url, task.dest, task.sha1, task.size)
                return DownloadResult(task=task, ok=True)
            except Exception as exc:  # noqa: BLE001
                last = exc
                LOG.debug("下载地址失败 %s：%s", url, exc)
        return DownloadResult(task=task, ok=False, error=str(last))
