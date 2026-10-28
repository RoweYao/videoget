"""Read publicly exposed video previews through an installed browser.

No browser cookies or existing user profiles are imported. A visible, temporary
browser context lets the user complete any site verification themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
import json
import os
from pathlib import Path
import re
import shutil
import threading
import tempfile
import time
from typing import Callable
from urllib.parse import urljoin, urlsplit, urlunsplit


class BrowserScanError(Exception):
    pass


class ScanCancelled(Exception):
    pass


@dataclass(frozen=True)
class VideoCandidate:
    url: str
    title: str
    identity: str


def _launch_browser(runtime, cancel):
    errors = []
    for channel in ("msedge", "chrome"):
        if cancel.is_set():
            raise ScanCancelled()
        try:
            return runtime.chromium.launch(channel=channel, headless=False)
        except Exception as exc:
            errors.append(str(exc).splitlines()[0])
    raise BrowserScanError("未能启动浏览器。请先安装 Microsoft Edge 或 Google Chrome，再重新运行。\n" + "\n".join(errors))


def available_filename(folder: Path, suggested: str) -> Path:
    """Sanitize a server filename for Windows and preserve existing files."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(suggested)).strip(". ")
    name = name or "video.mp4"
    path = Path(name)
    extension = path.suffix
    stem = path.stem[:max(1, 180 - len(extension))].rstrip(". ") or "video"
    if stem.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        stem = "_" + stem
    destination = folder / f"{stem}{extension}"
    suffix = 2
    while destination.exists():
        destination = folder / f"{stem} ({suffix}){extension}"
        suffix += 1
    return destination


def commit_download(partial: Path, folder: Path, suggested: str) -> Path:
    """Publish a completed file without replacing another process's output."""
    while True:
        target = available_filename(folder, suggested)
        try:
            # A link within the same folder commits the finished data atomically
            # and refuses existing names on both Windows and POSIX filesystems.
            os.link(partial, target)
            return target
        except FileExistsError:
            continue
        except OSError:
            # Filesystems without hard links still get exclusive creation.
            try:
                with target.open("xb") as destination:
                    with partial.open("rb") as source:
                        shutil.copyfileobj(source, destination)
                return target
            except FileExistsError:
                continue
            except Exception:
                target.unlink(missing_ok=True)
                raise


def receive_downloads(url, output_dir, limit, notify, on_file, cancel) -> list[Path]:
    """Save downloads initiated by the user through the site's official UI.

    Login and download authorization stay with the website. This mode neither
    finds original URLs nor upgrades preview URLs to restricted originals.
    """
    try:
        from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
    except ImportError as exc:
        raise BrowserScanError("缺少 Playwright，请安装 requirements.txt 后重试。") from exc
    saved: list[Path] = []
    pending = []
    rejected = []
    supported = {".mp4", ".mov", ".webm", ".m4v", ".mkv", ".avi", ".mpg", ".mpeg"}
    async def receive():
        async with async_playwright() as runtime:
            browser = None
            for channel in ("msedge", "chrome"):
                if cancel.is_set():
                    return saved
                try:
                    browser = await runtime.chromium.launch(channel=channel, headless=False)
                    break
                except Exception:
                    pass
            if browser is None:
                raise BrowserScanError("未能启动浏览器，请先安装 Microsoft Edge 或 Google Chrome。")
            try:
                context = await browser.new_context(accept_downloads=True)
                def register(page):
                    def enqueue(download):
                        if Path(download.suggested_filename).suffix.lower() in supported:
                            pending.append(download)
                        else:
                            rejected.append(download.suggested_filename)
                            asyncio.create_task(download.cancel())
                    page.on("download", enqueue)
                context.on("page", register)
                page = await context.new_page()
                notify("请在新浏览器窗口中登录网站，选择画质并点击网站提供的下载按钮。程序会保存收到的视频文件。")
                notify(f"最多接收 {limit} 个文件；关闭此次浏览器的所有窗口可结束接收。")
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                except PlaywrightTimeoutError:
                    notify("页面加载较慢，请在浏览器中确认网站内容。")
                while browser.is_connected() and context.pages and not cancel.is_set() and len(saved) < limit:
                    for name in rejected:
                        notify(f"跳过非视频文件：{name}")
                    rejected.clear()
                    if pending:
                        download = pending.pop(0)
                        target = available_filename(Path(output_dir), download.suggested_filename)
                        notify(f"正在接收：{target.name}（文件较大时请等待下载完成）")
                        # Pump browser events while waiting so a long original
                        # transfer remains cancellable from the desktop window.
                        with tempfile.NamedTemporaryFile(dir=output_dir, prefix=".videoget-", suffix=".part", delete=False) as temp:
                            partial = Path(temp.name)
                        transfer = asyncio.create_task(download.save_as(str(partial)))
                        try:
                            while not transfer.done():
                                if cancel.is_set() or not browser.is_connected():
                                    if browser.is_connected():
                                        await download.cancel()
                                    transfer.cancel()
                                    await asyncio.gather(transfer, return_exceptions=True)
                                    return saved
                                await asyncio.wait({transfer}, timeout=0.2)
                            await transfer
                            target = commit_download(partial, Path(output_dir), download.suggested_filename)
                        finally:
                            partial.unlink(missing_ok=True)
                        saved.append(target)
                        on_file(target)
                    else:
                        active = next((item for item in context.pages if not item.is_closed()), None)
                        if active is None:
                            break
                        await asyncio.sleep(0.2)
                notify(f"接收结束，已保存 {len(saved)} 个视频。")
                return saved
            finally:
                if browser.is_connected():
                    await browser.close()
    try:
        return asyncio.run(receive())
    except Exception as exc:
        # Closing a browser is the explicit finish action for this mode.
        if cancel.is_set() or "closed" in str(exc).lower() or "disconnected" in str(exc).lower():
            return saved
        raise BrowserScanError(f"接收网站下载失败：{exc}") from exc


def is_magnific(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host == "magnific.com" or host.endswith(".magnific.com")


def _video_key(url: str) -> str:
    parsed = urlsplit(url)
    # The same Magnific preview appears at several sizes and with short-lived
    # query tokens; use the video UUID rather than the token for deduplication.
    match = re.search(r"/videos/([a-f0-9-]{36})/", parsed.path, re.I)
    if match:
        return match.group(1)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _score(url: str) -> int:
    return (20 if "/large." in url else 0) + (3 if "magnific_watermarked" in url else 0)


def extract_candidates(snapshot: dict, page_url: str, limit: int = 30) -> list[VideoCandidate]:
    """Parse a DOM snapshot, keeping one real URL per preview without guessing URLs."""
    found: dict[str, VideoCandidate] = {}
    magnific = is_magnific(page_url)

    def add(raw: object, title: str = "", identity: str = "") -> None:
        if not isinstance(raw, str) or not raw.strip():
            return
        url = urljoin(page_url, raw.strip())
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not re.search(
            r"\.(?:mp4|webm|mov|m4v|m3u8|mpd)$", parsed.path, re.I
        ):
            return
        if magnific:
            # Include the site's public previews, excluding decorative videos
            # and never rewriting a preview URL into an original-file URL.
            if parsed.hostname != "videocdn.cdnpk.net" or "/previews/" not in parsed.path:
                return
        key = _video_key(url)
        previous = found.get(key)
        name = title or (previous.title if previous else f"video_{len(found) + 1:03d}")
        candidate = VideoCandidate(url, name, identity or key)
        if previous is None or _score(url) > _score(previous.url):
            found[key] = candidate

    def walk(value: object) -> None:
        if isinstance(value, dict):
            name = str(value.get("name") or value.get("title") or "")
            identity = str(value.get("id") or "")
            has_video = False
            for key in ("video", "videoFallback", "videoUrl", "contentUrl", "previewUrl"):
                if isinstance(value.get(key), str):
                    add(value[key], name, identity)
                    has_video = True
            # Magnific also exposes preview sizes in nested objects.
            for key, child in value.items():
                if key in {"bg_video", "backgrounds", "categoriesList", "relatedResources", "recommendations"}:
                    continue
                if isinstance(child, str):
                    if key in {"small", "large", "url", "src"} and (has_video or "/previews/" in child):
                        add(child, name, identity)
                else:
                    walk(child)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for script in snapshot.get("scripts", []):
        try:
            value = json.loads(script)
        except (TypeError, ValueError):
            continue
        if magnific and isinstance(value, dict) and "props" in value:
            props = value.get("props", {}).get("pageProps", {})
            # Spotlight resources are the actual cards on the provided subhome.
            # Avoid unrelated category backgrounds also included in Next data.
            if props.get("videoSpotlights"):
                walk(props["videoSpotlights"])
            else:
                walk(props)
        else:
            walk(value)
    for media in snapshot.get("media", []):
        title = str(media.get("title") or "")
        add(media.get("src"), title)
        for source in media.get("sources", []):
            add(source, title)
    for link in snapshot.get("links", []):
        add(link.get("url"), str(link.get("title") or ""))
    return list(found.values())[: max(1, limit)]


DOM_SNAPSHOT = r"""() => ({
    scripts: Array.from(document.querySelectorAll('script#__NEXT_DATA__,script[type="application/ld+json"]')).map(s => s.textContent),
    media: Array.from(document.querySelectorAll('video')).map(v => ({
        src: v.currentSrc || v.src || v.getAttribute('data-src') || '',
        title: v.getAttribute('aria-label') || v.title || '',
        sources: Array.from(v.querySelectorAll('source')).map(s => s.src || s.getAttribute('data-src') || '')
    })),
    links: Array.from(document.querySelectorAll('a[href]')).filter(a => /\.(mp4|webm|mov|m4v|m3u8|mpd)(\?|$)/i.test(a.href)).map(a => ({url:a.href,title:a.textContent.trim()}))
})"""


def scan_videos(
    url: str,
    limit: int,
    notify: Callable[[str], None],
    cancel: threading.Event,
) -> list[VideoCandidate]:
    """Open Edge/Chrome, wait for page media, and scan a bounded part of the page."""
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
    except ImportError as exc:
        raise BrowserScanError("缺少 Playwright，请安装 requirements.txt 后重试。") from exc

    def check_cancel() -> None:
        if cancel.is_set():
            raise ScanCancelled()

    try:
        with sync_playwright() as runtime:
            browser = _launch_browser(runtime, cancel)
            try:
                context = browser.new_context()
                page = context.new_page()
                notify("已打开独立浏览器窗口。如网站要求验证，请在该窗口手动完成（最多等待 2 分钟）。")
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                except PlaywrightTimeoutError:
                    notify("页面加载较慢，继续等待视频内容…")
                deadline = time.monotonic() + 120
                videos: list[VideoCandidate] = []
                while time.monotonic() < deadline:
                    check_cancel()
                    if page.is_closed():
                        raise BrowserScanError("浏览器窗口已关闭，未完成视频扫描。")
                    snapshot = page.evaluate(DOM_SNAPSHOT)
                    videos = extract_candidates(snapshot, page.url, limit)
                    if videos:
                        break
                    # Event.wait also lets cancellation interrupt this pause.
                    cancel.wait(0.5)
                if not videos:
                    raise BrowserScanError("没有找到可下载的视频地址。请确认页面已加载、已完成必要验证，或尝试单个视频页面。")

                stable = 0
                for _ in range(8):
                    check_cancel()
                    if len(videos) >= limit or stable >= 3:
                        break
                    # Bounded scrolling triggers lazy media; never follows links
                    # or paginates into other pages on the user's behalf.
                    page.mouse.wheel(0, 700)
                    cancel.wait(0.5)
                    check_cancel()
                    current = extract_candidates(page.evaluate(DOM_SNAPSHOT), page.url, limit)
                    merged = {item.identity: item for item in videos}
                    before = len(merged)
                    for item in current:
                        merged[item.identity] = item
                    videos = list(merged.values())[:limit]
                    stable = stable + 1 if len(merged) == before else 0
                notify(f"扫描完成，找到 {len(videos)} 个公开视频，开始下载。")
                return videos
            finally:
                browser.close()
    except (BrowserScanError, ScanCancelled):
        raise
    except Exception as exc:
        if cancel.is_set():
            raise ScanCancelled() from exc
        raise BrowserScanError(f"浏览器扫描失败：{exc}") from exc
