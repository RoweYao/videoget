"""VideoGet 的中文桌面界面。运行 ``python main.py`` 启动。"""

from __future__ import annotations

import math
import queue
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from downloader import (
    DownloadError,
    DownloadEvent,
    DownloadRequest,
    DownloadResult,
    download_videos,
    validate_url,
)


def format_bytes(value: int | float | None) -> str:
    """把下载体积和速度转为便于阅读的单位。"""
    if value is None or not math.isfinite(value):
        return "--"
    size = max(0.0, float(value))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return "--"


def format_eta(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "--"
    seconds = max(0, int(value))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if hours:
        return f"{hours} 小时 {minutes} 分"
    if minutes:
        return f"{minutes} 分 {seconds} 秒"
    return f"{seconds} 秒"


class VideoGetApp:
    """Tk 仅在主线程使用；下载事件通过队列传回界面。"""

    POLL_INTERVAL_MS = 100
    MAX_LOG_LINES = 3000
    MODE_PREVIEW = "自动下载网页预览"
    MODE_ORIGINAL = "接收网站原片下载"
    MODE_DIRECT = "常规视频网址 / 直链"
    DOWNLOAD_MODES = (MODE_PREVIEW, MODE_ORIGINAL, MODE_DIRECT)
    MODE_HINTS = {
        MODE_PREVIEW: "预览模式：打开 Edge/Chrome 扫描当前页面公开的视频预览。",
        MODE_ORIGINAL: "原片模式：在浏览器中登录并点击网站下载按钮；关闭浏览器窗口即可结束接收。",
        MODE_DIRECT: "常规模式：使用 yt-dlp 处理当前网址或视频直链。",
    }

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self._events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._cancel_event: threading.Event | None = None
        self._worker: threading.Thread | None = None
        self._running = False
        self._close_requested = False
        self._indeterminate = False

        self.url_var = tk.StringVar(value="https://www.magnific.com/videos")
        self.output_var = tk.StringVar(value=str(Path.home() / "Downloads" / "VideoGet"))
        self.playlist_var = tk.BooleanVar(value=False)
        self.mode_var = tk.StringVar(value=self.MODE_PREVIEW)
        self.mode_hint_var = tk.StringVar(value=self.MODE_HINTS[self.MODE_PREVIEW])
        # Keep this a StringVar so malformed manual input can be reported with
        # a friendly message instead of letting Tk raise a TclError.
        self.max_videos_var = tk.StringVar(value="30")
        self.status_var = tk.StringVar(value="准备就绪")
        self.percent_var = tk.StringVar(value="--")
        self.file_var = tk.StringVar(value="尚未开始下载")
        self.details_var = tk.StringVar(value="已下载 --  ·  总大小 --  ·  速度 --  ·  剩余 --")

        self._build_window()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(self.POLL_INTERVAL_MS, self._poll_events)
        self.url_entry.focus_set()

    def _build_window(self) -> None:
        self.root.title("VideoGet 视频下载器")
        self.root.geometry("900x760")
        self.root.minsize(780, 680)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        style = ttk.Style(self.root)
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("Muted.TLabel", foreground="#5b6472")

        main = ttk.Frame(self.root, padding=22)
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(9, weight=1)

        ttk.Label(main, text="VideoGet 视频下载器", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(main, text="输入视频网址，选择保存文件夹，然后点击运行下载。", style="Muted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(5, 18)
        )

        form = ttk.Frame(main)
        form.grid(row=2, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="视频网址").grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.url_entry = ttk.Entry(form, textvariable=self.url_var)
        self.url_entry.grid(row=0, column=1, columnspan=2, sticky="ew", ipady=4)
        self.url_entry.bind("<Return>", self._start_download)

        ttk.Label(form, text="保存文件夹").grid(row=1, column=0, sticky="w", padx=(0, 12), pady=(12, 0))
        self.output_entry = ttk.Entry(form, textvariable=self.output_var)
        self.output_entry.grid(row=1, column=1, sticky="ew", pady=(12, 0), ipady=4)
        self.output_entry.bind("<Return>", self._start_download)
        self.browse_button = ttk.Button(form, text="选择文件夹…", command=self._choose_folder)
        self.browse_button.grid(row=1, column=2, sticky="ew", padx=(10, 0), pady=(12, 0))

        ttk.Label(form, text="下载模式").grid(row=2, column=0, sticky="w", padx=(0, 12), pady=(12, 0))
        self.mode_combo = ttk.Combobox(
            form,
            textvariable=self.mode_var,
            values=self.DOWNLOAD_MODES,
            state="readonly",
            width=30,
        )
        self.mode_combo.grid(row=2, column=1, columnspan=2, sticky="w", pady=(12, 0))
        self.mode_combo.bind("<<ComboboxSelected>>", self._mode_changed)

        ttk.Label(
            form,
            textvariable=self.mode_hint_var,
            style="Muted.TLabel",
            wraplength=760,
        ).grid(row=3, column=1, columnspan=2, sticky="w", pady=(5, 0))

        options = ttk.Frame(form)
        options.grid(row=4, column=1, columnspan=2, sticky="w", pady=(10, 0))
        self.playlist_check = ttk.Checkbutton(
            options, text="下载整份播放列表（网址包含播放列表时）", variable=self.playlist_var
        )
        self.playlist_check.grid(row=0, column=0, sticky="w")
        ttk.Label(options, text="网页最多视频数").grid(row=0, column=1, sticky="w", padx=(24, 8))
        self.max_videos_spin = ttk.Spinbox(
            options,
            from_=1,
            to=200,
            increment=1,
            width=7,
            textvariable=self.max_videos_var,
            justify="right",
        )
        self.max_videos_spin.grid(row=0, column=2, sticky="w")

        ttk.Label(
            main,
            text=(
                "支持 yt-dlp 可解析站点及视频直链；部分需登录或加密的视频无法下载。"
                "\nMagnific 公开预览需要已安装 Edge 或 Chrome，扫描当前页；原片下载仅通过网站官方按钮并按网站权限执行。"
            ),
            style="Muted.TLabel",
            wraplength=820,
        ).grid(row=3, column=0, sticky="w", pady=(13, 16))

        actions = ttk.Frame(main)
        actions.grid(row=4, column=0, sticky="ew")
        actions.columnconfigure(2, weight=1)
        self.start_button = ttk.Button(actions, text="运行下载", command=self._start_download, width=15)
        self.start_button.grid(row=0, column=0, sticky="w")
        self.cancel_button = ttk.Button(
            actions, text="取消下载", command=self._cancel_download, state="disabled", width=13
        )
        self.cancel_button.grid(row=0, column=1, padx=(10, 0), sticky="w")
        ttk.Label(actions, textvariable=self.status_var, wraplength=430).grid(
            row=0, column=2, sticky="e", padx=(15, 0)
        )

        progress_frame = ttk.Frame(main)
        progress_frame.grid(row=5, column=0, sticky="ew", pady=(18, 0))
        progress_frame.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(progress_frame, maximum=100, mode="determinate")
        self.progress.grid(row=0, column=0, sticky="ew")
        ttk.Label(progress_frame, textvariable=self.percent_var, width=8, anchor="e").grid(
            row=0, column=1, padx=(10, 0)
        )
        ttk.Label(main, textvariable=self.details_var, style="Muted.TLabel").grid(
            row=6, column=0, sticky="w", pady=(8, 0)
        )
        ttk.Label(main, textvariable=self.file_var, style="Muted.TLabel", wraplength=760).grid(
            row=7, column=0, sticky="w", pady=(6, 12)
        )

        log_heading = ttk.Frame(main)
        log_heading.grid(row=8, column=0, sticky="ew", pady=(0, 5))
        log_heading.columnconfigure(0, weight=1)
        ttk.Label(log_heading, text="运行日志").grid(row=0, column=0, sticky="w")
        ttk.Button(log_heading, text="复制日志", command=self._copy_log).grid(row=0, column=1, sticky="e")

        log_frame = ttk.Frame(main)
        log_frame.grid(row=9, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(
            log_frame,
            wrap="word",
            height=12,
            state="disabled",
            font="TkFixedFont",
            background="#ffffff",
            foreground="#26313f",
            relief="solid",
            borderwidth=1,
            padx=10,
            pady=8,
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.bind("<Control-a>", self._select_all_log)
        self.log_text.bind("<Command-a>", self._select_all_log)

    def _choose_folder(self) -> None:
        try:
            candidate = Path(self.output_var.get()).expanduser()
            initial_dir = candidate if candidate.is_dir() else Path.home()
        except (OSError, ValueError):
            initial_dir = Path.home()
        selected = filedialog.askdirectory(
            parent=self.root,
            title="选择视频保存文件夹",
            initialdir=str(initial_dir),
            mustexist=False,
        )
        if selected:
            self.output_var.set(selected)

    def _mode_changed(self, _event: tk.Event | None = None) -> None:
        mode = self.mode_var.get()
        self.mode_hint_var.set(self.MODE_HINTS.get(mode, self.MODE_HINTS[self.MODE_DIRECT]))

    def _start_download(self, _event: tk.Event | None = None) -> None:
        if self._running or self._close_requested:
            return
        try:
            url = validate_url(self.url_var.get())
        except DownloadError as exc:
            messagebox.showerror("网址有误", str(exc), parent=self.root)
            self.url_entry.focus_set()
            return
        output_dir = self.output_var.get().strip()
        if not output_dir:
            messagebox.showerror("请选择保存位置", "请先选择或输入视频保存文件夹。", parent=self.root)
            self.output_entry.focus_set()
            return
        raw_max_videos = self.max_videos_var.get().strip()
        try:
            max_videos = int(raw_max_videos)
        except (TypeError, ValueError):
            messagebox.showerror("数量有误", "网页最多视频数必须是 1 到 200 之间的整数。", parent=self.root)
            self.max_videos_spin.focus_set()
            return
        if not 1 <= max_videos <= 200:
            messagebox.showerror("数量有误", "网页最多视频数必须是 1 到 200 之间的整数。", parent=self.root)
            self.max_videos_spin.focus_set()
            return

        self.url_var.set(url)
        self.max_videos_var.set(str(max_videos))
        mode = self.mode_var.get()
        if mode not in self.DOWNLOAD_MODES:
            mode = self.MODE_PREVIEW
            self.mode_var.set(mode)
            self._mode_changed()
        browser_scan = mode == self.MODE_PREVIEW
        original_download = mode == self.MODE_ORIGINAL
        request = DownloadRequest(
            url=url,
            output_dir=output_dir,
            download_playlist=self.playlist_var.get(),
            browser_scan=browser_scan,
            max_videos=max_videos,
            original_download=original_download,
        )
        self._cancel_event = threading.Event()
        self._set_running(True)
        if original_download:
            self.status_var.set("等待网站下载…")
            self.file_var.set("请在新打开的浏览器中登录并点击网站下载按钮…")
            self.details_var.set("原片接收模式：等待网站下载；已保存 --")
            self._set_progress_idle()
        else:
            self.status_var.set("正在解析网址…")
            self.file_var.set("正在获取视频信息…")
            self.details_var.set("已下载 --  ·  总大小 --  ·  速度 --  ·  剩余 --")
            self._set_progress(None)
        self._append_log(f"开始任务：{url}")
        self._append_log(f"保存文件夹：{output_dir}")
        if request.browser_scan:
            self._append_log(f"已启用浏览器扫描，最多处理 {request.max_videos} 个公开预览。")
        elif request.original_download:
            self._append_log(
                "原片接收模式：请在浏览器中登录并点击网站官方已有下载按钮；关闭浏览器窗口即可结束接收。"
            )
        else:
            self._append_log("常规视频网址 / 直链模式：由 yt-dlp 处理当前网址。")
        self._worker = threading.Thread(
            target=self._download_worker,
            args=(request, self._cancel_event),
            name="VideoGetDownload",
            daemon=True,
        )
        self._worker.start()

    def _download_worker(self, request: DownloadRequest, cancel_event: threading.Event) -> None:
        """后台线程只做下载和发消息，不访问任何 Tk 对象。"""
        try:
            result = download_videos(request, lambda event: self._events.put(("event", event)), cancel_event)
        except DownloadError as exc:
            self._events.put(("error", str(exc)))
        except Exception as exc:
            self._events.put(("error", f"程序遇到异常：{type(exc).__name__}: {exc}"))
        else:
            self._events.put(("finished", result))

    def _poll_events(self) -> None:
        # 每轮限制消息数，播放列表大量输出时也给窗口处理输入的机会。
        for _ in range(150):
            try:
                kind, payload = self._events.get_nowait()
            except queue.Empty:
                break
            if kind == "event":
                self._handle_download_event(payload)
            elif kind == "finished":
                self._finish_download(payload)
            elif kind == "error":
                self._finish_error(str(payload))
            if self._close_requested and not self._running:
                self.root.destroy()
                return
        self.root.after(self.POLL_INTERVAL_MS, self._poll_events)

    def _handle_download_event(self, event: DownloadEvent) -> None:
        if event.kind == "log":
            self._append_log(event.message)
        elif event.kind == "status":
            if not (self._cancel_event and self._cancel_event.is_set()):
                self.status_var.set(event.message)
        elif event.kind == "progress":
            # The website-download mode reports status and file events only;
            # keep its progress bar idle while the user operates the browser.
            if self.mode_var.get() == self.MODE_ORIGINAL:
                return
            self._set_progress(event.percent)
            total = format_bytes(event.total_bytes)
            speed = f"{format_bytes(event.speed)}/s" if event.speed is not None else "--"
            self.details_var.set(
                f"已下载 {format_bytes(event.downloaded_bytes)}  ·  总大小 {total}"
                f"  ·  速度 {speed}  ·  剩余 {format_eta(event.eta)}"
            )
            if event.filename:
                self.file_var.set(f"当前文件：{Path(event.filename).name}")
        elif event.kind == "file":
            if event.filename:
                self.file_var.set(f"已处理：{Path(event.filename).name}")
                if self.mode_var.get() == self.MODE_ORIGINAL:
                    self.status_var.set(f"已保存：{Path(event.filename).name}")
                    self.details_var.set("原片接收模式：已保存网站下载文件")
            self._append_log(event.message or f"已处理文件：{event.filename}")

    def _set_running(self, running: bool) -> None:
        self._running = running
        state = "disabled" if running else "normal"
        for widget in (
            self.url_entry,
            self.output_entry,
            self.browse_button,
            self.playlist_check,
            self.max_videos_spin,
            self.start_button,
        ):
            widget.configure(state=state)
        # Keep the mode selector constrained to the three supported values
        # when a task is idle; ``normal`` would make a ttk.Combobox editable.
        self.mode_combo.configure(state="disabled" if running else "readonly")
        self.cancel_button.configure(state="normal" if running else "disabled")

    def _set_progress(self, percent: float | None) -> None:
        if percent is None or not math.isfinite(percent):
            if not self._indeterminate:
                self.progress.configure(mode="indeterminate")
                self.progress.start(14)
                self._indeterminate = True
            self.percent_var.set("--")
        else:
            if self._indeterminate:
                self.progress.stop()
                self._indeterminate = False
            value = min(100.0, max(0.0, percent))
            self.progress.configure(mode="determinate", value=value)
            self.percent_var.set(f"{value:.1f}%")

    def _stop_progress(self) -> None:
        if self._indeterminate:
            self.progress.stop()
            self._indeterminate = False
            self.progress.configure(mode="determinate", value=0)
            self.percent_var.set("--")

    def _set_progress_idle(self) -> None:
        """Leave the progress display idle for browser-managed downloads."""
        self._stop_progress()
        self.progress.configure(mode="determinate", value=0)
        self.percent_var.set("--")

    def _finish_download(self, result: DownloadResult) -> None:
        self._stop_progress()
        self._set_running(False)
        count = len(set(result.files))
        failed = tuple(getattr(result, "failed", ()))
        if result.cancelled:
            self.status_var.set("已取消")
            detail = "已完成的文件已保留；未完成的原片需要重新下载。" if self.mode_var.get() == self.MODE_ORIGINAL else "未完成的数据已保留，可再次运行以续传。"
            self._append_log(f"任务已取消。本次已处理 {count} 个视频；{detail}")
        else:
            if self.mode_var.get() == self.MODE_ORIGINAL:
                self._set_progress_idle()
            else:
                self._set_progress(100)
            if failed:
                self.status_var.set(f"部分失败：已处理 {count} 个，失败 {len(failed)} 个")
                self._append_log(
                    f"任务部分完成：已处理 {count} 个视频，失败 {len(failed)} 个。"
                )
                failed_preview = "\n".join(f"· {item}" for item in failed[:8])
                if len(failed) > 8:
                    failed_preview += f"\n· 其余 {len(failed) - 8} 个失败项目请查看日志。"
                self._append_log("失败项目：\n" + failed_preview)
            else:
                self.status_var.set(f"完成，已处理 {count} 个视频")
                self._append_log(f"任务完成，本次已处理 {count} 个视频（可能包括已存在的文件）。")
            if not self._close_requested:
                if failed:
                    messagebox.showwarning(
                        "部分下载失败",
                        f"本次已处理 {count} 个视频，失败 {len(failed)} 个。\n"
                        "已完成的文件会保留；可以修正后重新运行。\n\n"
                        f"保存位置：{self.output_var.get()}",
                        parent=self.root,
                    )
                else:
                    messagebox.showinfo(
                        "处理完成",
                        f"本次已处理 {count} 个视频。\n已存在的文件可能被跳过。\n\n保存位置：{self.output_var.get()}",
                        parent=self.root,
                    )

    def _finish_error(self, message: str) -> None:
        self._stop_progress()
        self._set_running(False)
        self.status_var.set("下载失败，请查看日志")
        self._append_log(f"下载失败：{message}")
        if not self._close_requested:
            messagebox.showerror("下载失败", message, parent=self.root)

    def _cancel_download(self) -> None:
        if not self._running or self._cancel_event is None or self._cancel_event.is_set():
            return
        self._cancel_event.set()
        self.cancel_button.configure(state="disabled")
        self.status_var.set("正在取消，请等待当前网络请求结束…")
        self._append_log(
            "正在取消；已完成的文件会保留，未完成的原片需要重新下载。"
            if self.mode_var.get() == self.MODE_ORIGINAL
            else "正在取消；已下载的数据会保留，便于下次续传。"
        )

    def _on_close(self) -> None:
        if self._close_requested:
            return
        if not self._running:
            self.root.destroy()
            return
        confirmed = messagebox.askyesno(
            "取消下载并退出",
            "视频仍在下载。是否取消下载并退出？\n\n已下载的数据会保留。退出可能需要等待当前网络请求结束。",
            parent=self.root,
        )
        if confirmed:
            self._close_requested = True
            self._cancel_download()
            self.status_var.set("正在取消并关闭，请等待当前网络请求结束…")

    def _append_log(self, message: str) -> None:
        if not message:
            return
        follow_tail = self.log_text.yview()[1] >= 0.98
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{datetime.now():%H:%M:%S}] {message.rstrip()}\n")
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > self.MAX_LOG_LINES:
            self.log_text.delete("1.0", f"{line_count - self.MAX_LOG_LINES + 1}.0")
        self.log_text.configure(state="disabled")
        if follow_tail:
            self.log_text.see("end")

    def _copy_log(self) -> None:
        content = self.log_text.get("1.0", "end-1c")
        if content:
            self.root.clipboard_clear()
            self.root.clipboard_append(content)

    def _select_all_log(self, _event: tk.Event) -> str:
        self.log_text.tag_add("sel", "1.0", "end-1c")
        return "break"


def main() -> None:
    root = tk.Tk()
    VideoGetApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
