"""Offline regression tests: a local server exercises real yt-dlp downloads."""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

import imageio_ffmpeg

from browser_scan import available_filename, commit_download, extract_candidates, VideoCandidate
from downloader import DownloadError, DownloadRequest, download_videos, validate_url


class PageParsingTests(unittest.TestCase):
    def test_magnific_cards_deduplicate_sizes_and_ignore_backgrounds(self):
        base = "https://videocdn.cdnpk.net/videos/abf65b25-fbf7-5c36-b495-372f1c6a55a6/horizontal/previews/"
        snapshot = {"scripts": [json.dumps({"props": {"pageProps": {
            "videoSpotlights": [{"id": 6347658, "name": "Candles", "video": base + "magnific_watermarked/large.mp4"}],
            "categoriesList": [{"bg_video": base.replace("abf65b25", "bbf65b25") + "watermarked/large.mp4"}],
        }}})], "media": [{"src": base + "magnific_watermarked/small.mp4"}]}
        items = extract_candidates(snapshot, "https://www.magnific.com/videos")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Candles")
        self.assertIn("large.mp4", items[0].url)

    def test_relative_video_urls_and_count_limit(self):
        snapshot = {"media": [{"src": "/one.mp4"}, {"src": "/two.webm"}, {"src": "blob:invalid"}]}
        items = extract_candidates(snapshot, "https://example.com/watch", 1)
        self.assertEqual([item.url for item in items], ["https://example.com/one.mp4"])

    def test_invalid_and_valid_urls(self):
        for url in ("", "file:///tmp/test.mp4", "https://example.com:bad/video", "http://", "https://a.com/a b"):
            with self.subTest(url=url), self.assertRaises(DownloadError):
                validate_url(url)
        self.assertEqual(validate_url("  https://www.magnific.com/videos  "), "https://www.magnific.com/videos")

    def test_original_filename_stays_in_folder_and_avoids_collision(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            unsafe = available_filename(directory, "../CON.mp4")
            self.assertEqual(unsafe.parent, directory)
            self.assertNotIn("/", unsafe.name)
            first = available_filename(directory, "原片.mp4")
            first.write_bytes(b"keep")
            second = available_filename(directory, "原片.mp4")
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_bytes(), b"keep")
            self.assertEqual(available_filename(directory, "CON.mp4").name, "_CON.mp4")
            long_path = available_filename(directory, "a" * 250 + ".mp4")
            self.assertEqual(long_path.suffix, ".mp4")
            partial = directory / "tmp.part"
            partial.write_bytes(b"new")
            published = commit_download(partial, directory, "原片.mp4")
            self.assertNotEqual(published, first)
            self.assertEqual(first.read_bytes(), b"keep")
            self.assertEqual(published.read_bytes(), b"new")


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


class DownloadIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="videoget-tests-")
        cls.web = Path(cls.temp.name) / "web"
        cls.web.mkdir()
        cls.sample = cls.web / "sample.mp4"
        subprocess.run([
            imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=10", "-t", "1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(cls.sample),
        ], check=True, timeout=30, capture_output=True)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(cls.web)))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.temp.cleanup()

    def setUp(self):
        self.output = tempfile.TemporaryDirectory(prefix="videoget-output-")
        self.folder = str(Path(self.output.name) / "中文 视频")

    def tearDown(self):
        self.output.cleanup()

    def test_direct_download_and_rerun_preserve_real_video(self):
        request = DownloadRequest(self.base + "/sample.mp4", self.folder)
        events = []
        result = download_videos(request, events.append, threading.Event())
        self.assertEqual(len(result.files), 1)
        output = Path(result.files[0])
        self.assertEqual(output.read_bytes(), self.sample.read_bytes())
        self.assertTrue(any(event.kind == "progress" for event in events))
        repeat = download_videos(request, events.append, threading.Event())
        self.assertEqual(repeat.files, result.files)
        self.assertEqual(len(list(Path(self.folder).glob("*.mp4"))), 1)

    def test_batch_uses_distinct_ids_and_reports_partial_failure(self):
        targets = [
            VideoCandidate(self.base + "/sample.mp4", "相同标题", "one"),
            VideoCandidate(self.base + "/missing.mp4", "missing", "missing"),
            VideoCandidate(self.base + "/sample.mp4", "相同标题", "two"),
        ]
        with patch("browser_scan.scan_videos", return_value=targets):
            result = download_videos(DownloadRequest(self.base, self.folder, browser_scan=True), lambda _: None, threading.Event())
        self.assertEqual(len(result.files), 2)
        self.assertEqual(len(set(result.files)), 2)
        self.assertEqual(result.failed, ("missing",))
        self.assertTrue(all(Path(path).read_bytes() == self.sample.read_bytes() for path in result.files))

    def test_cancellation_does_not_report_unfinished_file(self):
        cancel = threading.Event()
        def emit(event):
            if event.kind == "progress":
                cancel.set()
        result = download_videos(DownloadRequest(self.base + "/sample.mp4", self.folder), emit, cancel)
        self.assertTrue(result.cancelled)
        self.assertEqual(result.files, ())


if __name__ == "__main__":
    unittest.main()
