"""Download videos with :mod:`yt_dlp`.

The module deliberately contains no GUI code.  ``main.py`` runs
``download_videos`` on a worker thread and uses the events emitted by this
module to update its widgets.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import threading
import time
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlsplit


@dataclass
class DownloadRequest:
    """A single download request."""

    url: str
    output_dir: str
    download_playlist: bool = False
    browser_scan: bool = False
    max_videos: int = 30
    original_download: bool = False


@dataclass
class DownloadEvent:
    """A status, log, progress, or completed-file notification."""

    kind: str
    message: str = ""
    percent: float | None = None
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    speed: float | None = None
    eta: float | None = None
    filename: str = ""


@dataclass
class DownloadResult:
    """Result of a download operation."""

    files: tuple[str, ...]
    cancelled: bool = False
    failed: tuple[str, ...] = ()


class DownloadError(Exception):
    """An expected, user-facing download error."""


class DownloadCancelled(Exception):
    """Internal exception used to stop yt-dlp from a progress hook."""


EventEmitter = Callable[[DownloadEvent], None]


def validate_url(value: str) -> str:
    """Return a trimmed HTTP(S) URL, or raise :class:`DownloadError`.

    yt-dlp supports many URL forms, but accepting non-network schemes here is
    surprising in a desktop downloader and can result in confusing errors.
    A host is required as well as an HTTP or HTTPS scheme.
    """

    if not isinstance(value, str):
        raise DownloadError("请输入视频网址。")
    url = value.strip()
    if not url:
        raise DownloadError("请输入视频网址。")
    if any(char.isspace() or char == "\x00" for char in url):
        raise DownloadError("网址格式不正确。")
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise DownloadError(f"网址格式不正确：{exc}") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise DownloadError("网址必须以 http:// 或 https:// 开头，并包含有效域名。")
    try:
        # Accessing ``hostname`` catches malformed bracketed IPv6 hosts.
        hostname = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise DownloadError(f"网址格式不正确：{exc}") from exc
    if not hostname:
        raise DownloadError("网址必须包含有效域名。")
    return url


def _as_number(value: Any) -> float | None:
    """Convert yt-dlp's numeric values (including ``"12.3%"``) safely."""

    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip().replace("%", "")
        try:
            number = float(text)
        except (TypeError, ValueError):
            return None
    return number if math.isfinite(number) else None


def _as_int(value: Any) -> int | None:
    number = _as_number(value)
    if number is None:
        return None
    return max(0, int(number))


def _filename_from_info(info: Mapping[str, Any] | None) -> str:
    """Get the best available output filename from a yt-dlp info mapping."""

    if not isinstance(info, Mapping):
        return ""
    # ``filepath`` is set by postprocessors.  ``filename``/``_filename`` are
    # useful during the normal download phase; tmpfilename is a last resort
    # for a progress update when a server changed the extension.
    for key in ("filepath", "filename", "_filename", "requested_filename", "tmpfilename"):
        value = info.get(key)
        if value:
            return str(value)
    requested = info.get("requested_downloads")
    if isinstance(requested, Iterable) and not isinstance(requested, (str, bytes, Mapping)):
        for item in requested:
            if isinstance(item, Mapping):
                filename = _filename_from_info(item)
                if filename:
                    return filename
    nested = info.get("info_dict")
    if isinstance(nested, Mapping) and nested is not info:
        return _filename_from_info(nested)
    return ""


def _iter_info_entries(info: Any) -> Iterable[Mapping[str, Any]]:
    """Yield individual video entries from a video or playlist result."""

    if not isinstance(info, Mapping):
        return
    entries = info.get("entries")
    if entries is not None and not isinstance(entries, (str, bytes, Mapping)):
        try:
            for entry in entries:
                yield from _iter_info_entries(entry)
            return
        except TypeError:
            pass
    yield info


class _YtdlpLogger:
    """Adapt yt-dlp logger calls to the application's log events."""

    def __init__(self, emit: EventEmitter) -> None:
        self._emit = emit

    def debug(self, message: str) -> None:
        # yt-dlp sends verbose diagnostics through ``debug``.  Keep ordinary
        # messages useful while omitting its very noisy ``[debug]`` lines.
        if message and not str(message).startswith("[debug]"):
            self._emit(DownloadEvent(kind="log", message=str(message)))

    def warning(self, message: str) -> None:
        if message:
            self._emit(DownloadEvent(kind="log", message=f"警告：{message}"))

    def error(self, message: str) -> None:
        if message:
            self._emit(DownloadEvent(kind="log", message=f"错误：{message}"))


def download_videos(
    request: DownloadRequest,
    emit: EventEmitter,
    cancel_event: threading.Event,
) -> DownloadResult:
    """Download one URL and emit UI-friendly events while it runs.

    ``yt_dlp`` and ``imageio_ffmpeg`` are imported lazily so that the GUI can
    still start and show a useful error when a dependency was not installed.
    The caller owns ``cancel_event``; setting it causes the next progress hook
    to abort the operation and returns a partial, cancelled result.
    """

    url = validate_url(request.url)
    if not isinstance(request.max_videos, int) or not 1 <= request.max_videos <= 200:
        raise DownloadError("每次扫描数量必须为 1 到 200 之间的整数。")
    output_text = str(request.output_dir).strip() if request.output_dir is not None else ""
    if not output_text:
        raise DownloadError("请选择视频保存文件夹。")
    output_dir = Path(output_text).expanduser()
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DownloadError(f"无法创建保存文件夹：{output_dir}\n{exc}") from exc

    # A callback normally puts events into a queue.  A broken callback should
    # not leave yt-dlp's temporary files in an inconsistent state, so event
    # delivery is intentionally best-effort.
    def send(event: DownloadEvent) -> None:
        try:
            emit(event)
        except Exception:
            pass

    files: list[str] = []
    failures: list[str] = []
    seen_files: set[str] = set()
    processed_stems: set[tuple[str, str]] = set()

    def file_stem_key(filename: str) -> tuple[str, str]:
        path = Path(filename)
        return (str(path.parent), path.stem)

    def remember_file(filename: str, *, processed: bool = False) -> None:
        if not filename or not Path(filename).is_file():
            return
        # Keep the path as supplied by yt-dlp.  In particular, do not resolve
        # it: users may intentionally choose a relative output directory.
        value = str(filename)
        if processed:
            processed_stems.add(file_stem_key(value))
        if value not in seen_files:
            # A merged file often follows a downloaded ``.webm``/``.m4a``
            # file with the same stem.  Keep the final output in the result
            # rather than reporting both intermediate and final paths.
            if processed:
                key = file_stem_key(value)
                for old in list(files):
                    if file_stem_key(old) == key and old != value:
                        files.remove(old)
                        seen_files.discard(old)
            seen_files.add(value)
            files.append(value)

    def is_cancelled() -> bool:
        try:
            return bool(cancel_event and cancel_event.is_set())
        except AttributeError:
            return False

    if is_cancelled():
        send(DownloadEvent(kind="status", message="已取消"))
        return DownloadResult(files=tuple(files), cancelled=True)

    try:
        import yt_dlp
    except ImportError as exc:
        raise DownloadError("缺少 yt-dlp 依赖，请先安装 requirements.txt 中的依赖。") from exc
    try:
        from yt_dlp.utils import DownloadError as YtdlpDownloadError
    except (ImportError, AttributeError):
        # A few embedders expose only ``YoutubeDL``.  Their exceptions still
        # get the generic handling below, while a full yt-dlp install uses its
        # dedicated error type for a cleaner message.
        YtdlpDownloadError = type("YtdlpDownloadError", (Exception,), {})

    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise DownloadError("缺少 imageio-ffmpeg 依赖，无法合并视频和音频。") from exc

    try:
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise DownloadError(f"无法准备 FFmpeg：{exc}") from exc

    from browser_scan import BrowserScanError, ScanCancelled, is_magnific, scan_videos, receive_downloads

    targets = None
    if request.original_download:
        try:
            paths = receive_downloads(
                url, output_dir, request.max_videos,
                lambda message: send(DownloadEvent(kind="log", message=message)),
                lambda path: send(DownloadEvent(kind="file", filename=str(path), message=f"已保存：{path.name}")),
                cancel_event,
            )
        except BrowserScanError as exc:
            raise DownloadError(str(exc)) from exc
        if not paths and not cancel_event.is_set():
            raise DownloadError("未接收到视频文件。请在浏览器中登录后，点击网站提供的视频下载按钮。")
        return DownloadResult(files=tuple(map(str, paths)), cancelled=cancel_event.is_set())
    if request.browser_scan:
        send(DownloadEvent(kind="status", message="正在打开浏览器读取页面…"))
        if is_magnific(url):
            send(DownloadEvent(kind="log", message="Magnific 模式：下载当前页面公开的预览视频；高清原片请按网站权限下载。"))
        try:
            targets = scan_videos(
                url, request.max_videos,
                lambda message: send(DownloadEvent(kind="log", message=message)),
                cancel_event,
            )
        except ScanCancelled:
            return DownloadResult(files=(), cancelled=True)
        except BrowserScanError as exc:
            raise DownloadError(str(exc)) from exc

    last_progress = 0.0

    def progress_hook(info: Mapping[str, Any]) -> None:
        nonlocal last_progress
        if is_cancelled():
            raise DownloadCancelled()
        if not isinstance(info, Mapping):
            return
        status = str(info.get("status", ""))
        filename = _filename_from_info(info)
        downloaded = _as_int(info.get("downloaded_bytes")) or 0
        total = _as_int(info.get("total_bytes"))
        if total is None:
            total = _as_int(info.get("total_bytes_estimate"))
        speed = _as_number(info.get("speed"))
        eta = _as_number(info.get("eta"))
        percent = _as_number(info.get("percent"))
        if percent is None:
            percent = _as_number(info.get("_percent_str"))
        if percent is None and total and total > 0:
            percent = min(100.0, max(0.0, downloaded * 100.0 / total))
        elif percent is not None:
            percent = min(100.0, max(0.0, percent))
        if status == "downloading":
            now = time.monotonic()
            if now - last_progress < 0.12:
                return
            last_progress = now
            send(
                DownloadEvent(
                    kind="progress",
                    percent=percent,
                    downloaded_bytes=downloaded,
                    total_bytes=total,
                    speed=speed,
                    eta=eta,
                    filename=filename,
                )
            )
            if filename:
                send(DownloadEvent(kind="status", message=f"正在下载：{Path(filename).name}"))
        elif status == "finished":
            # For a direct file (or a test double without postprocessors)
            # this is already the final path.  A later postprocessor event
            # replaces same-stem intermediate paths when necessary.
            if percent is None:
                percent = 100.0
            send(
                DownloadEvent(
                    kind="progress",
                    percent=percent,
                    downloaded_bytes=downloaded,
                    total_bytes=total,
                    speed=speed,
                    eta=eta,
                    filename=filename,
                )
            )
            send(DownloadEvent(kind="status", message="下载完成，正在处理视频…"))

    def postprocessor_hook(info: Mapping[str, Any]) -> None:
        if is_cancelled():
            raise DownloadCancelled()
        if not isinstance(info, Mapping):
            return
        status = str(info.get("status", ""))
        filename = _filename_from_info(info)
        if status in {"started", "processing"}:
            send(DownloadEvent(kind="status", message="正在合并或处理视频…"))
        elif status == "finished":
            send(
                DownloadEvent(
                    kind="file",
                    filename=filename,
                    message=f"已处理文件：{Path(filename).name}" if filename else "视频处理完成。",
                )
            )

    options: dict[str, Any] = {
        "outtmpl": str(output_dir / "%(title).100s [%(id).80s].%(ext)s"),
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "noplaylist": not request.download_playlist,
        "continuedl": True,
        "part": True,
        "overwrites": False,
        "windowsfilenames": True,
        "socket_timeout": 15,
        "retries": 2,
        "fragment_retries": 2,
        "color": {"stdout": "never", "stderr": "never"},
        "quiet": True,
        "noprogress": True,
        "ffmpeg_location": ffmpeg_exe,
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [postprocessor_hook],
        "logger": _YtdlpLogger(send),
    }

    send(DownloadEvent(kind="status", message="正在解析网址…"))
    send(DownloadEvent(kind="log", message=f"开始下载：{url}"))
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            from yt_dlp.postprocessor import PostProcessor

            class FinalFileRecorder(PostProcessor):
                def run(self, info):
                    filename = info.get("filepath") or ydl.prepare_filename(info)
                    remember_file(filename, processed=True)
                    send(DownloadEvent(kind="file", filename=filename, message=f"已保存：{Path(filename).name}"))
                    return [], info

            ydl.add_post_processor(FinalFileRecorder(ydl), when="after_move")
            if targets is not None:
                for index, target in enumerate(targets, 1):
                    if is_cancelled():
                        raise DownloadCancelled()
                    send(DownloadEvent(kind="status", message=f"正在下载第 {index}/{len(targets)} 个视频…"))
                    try:
                        info = ydl.extract_info(target.url, download=False)
                        if not info:
                            raise DownloadError("未获取到视频信息。")
                        info.update(title=target.title, id=target.identity)
                        info.setdefault("http_headers", {})["Referer"] = url
                        ydl.process_ie_result(info, download=True)
                    except (YtdlpDownloadError, DownloadError) as exc:
                        if is_cancelled():
                            raise DownloadCancelled() from exc
                        failures.append(target.title)
                        send(DownloadEvent(kind="log", message=f"该视频失败，继续下一个：{target.title}\n{exc}"))
                result_info = None
                if not files and failures:
                    raise DownloadError(f"找到 {len(targets)} 个视频，但均未能下载。请查看日志中的具体原因。")
            else:
                result_info = ydl.extract_info(url, download=True)

            for entry in _iter_info_entries(result_info):
                filename = _filename_from_info(entry)
                if not filename and hasattr(ydl, "prepare_filename"):
                    try:
                        filename = str(ydl.prepare_filename(entry))
                    except Exception:
                        filename = ""
                if filename and file_stem_key(filename) not in processed_stems:
                    remember_file(filename)
    except DownloadCancelled:
        send(DownloadEvent(kind="status", message="已取消"))
        send(DownloadEvent(kind="log", message="下载已取消；已下载的数据已保留。"))
        return DownloadResult(files=tuple(files), cancelled=True, failed=tuple(failures))
    except YtdlpDownloadError as exc:
        if is_cancelled():
            send(DownloadEvent(kind="status", message="已取消"))
            return DownloadResult(files=tuple(files), cancelled=True)
        message = str(exc).strip() or "yt-dlp 无法下载该网址。"
        raise DownloadError(message) from exc
    except DownloadError:
        raise
    except Exception as exc:
        if is_cancelled():
            send(DownloadEvent(kind="status", message="已取消"))
            return DownloadResult(files=tuple(files), cancelled=True)
        raise DownloadError(f"下载失败：{type(exc).__name__}: {exc}") from exc

    if is_cancelled():
        send(DownloadEvent(kind="status", message="已取消"))
        return DownloadResult(files=tuple(files), cancelled=True)
    send(DownloadEvent(kind="status", message="下载完成"))
    if not files:
        raise DownloadError("没有生成视频文件，请检查网址和运行日志。")
    return DownloadResult(files=tuple(files), cancelled=False, failed=tuple(failures))


__all__ = [
    "DownloadError",
    "DownloadEvent",
    "DownloadRequest",
    "DownloadResult",
    "DownloadCancelled",
    "download_videos",
    "validate_url",
]
