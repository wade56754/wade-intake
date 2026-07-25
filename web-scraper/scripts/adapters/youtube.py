"""
youtube.py — YouTube 视频适配器
主路径：yt-dlp（字幕 + 元数据）
降级：TikHub API → JinaFetcher
"""

import glob
import json
import os
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request

from .base import BaseFetcher, FetchResult
from utils import truncate_content


class YoutubeFetcher(BaseFetcher):
    """YouTube 视频适配器"""

    def can_handle(self, url: str) -> bool:
        url_lower = url.lower()
        return "youtube.com/watch" in url_lower or "youtu.be/" in url_lower

    def fetch(self, url: str, **kwargs) -> FetchResult:
        max_chars = kwargs.get("max_chars", 30000)

        # 1. yt-dlp
        try:
            if self._yt_dlp_available():
                return self._fetch_via_ytdlp(url, max_chars)
        except Exception:
            pass

        # 2. TikHub
        try:
            api_key = os.environ.get("TIKHUB_API_KEY")
            if api_key:
                return self._fetch_via_tikhub(url, api_key, max_chars)
        except Exception:
            pass

        # 3. Jina Reader
        try:
            from .jina import JinaFetcher
            result = JinaFetcher().fetch(url, **kwargs)
            result.platform = "youtube"
            return result
        except Exception as e:
            raise RuntimeError(f"YouTube 三级降级全部失败: {e}")

    def _yt_dlp_available(self) -> bool:
        try:
            result = subprocess.run(["which", "yt-dlp"], capture_output=True, timeout=5)
            return result.returncode == 0
        except Exception:
            return False

    def _fetch_via_ytdlp(self, url: str, max_chars: int) -> FetchResult:
        with tempfile.TemporaryDirectory(prefix="yt_scraper_") as tmpdir:
            out_template = os.path.join(tmpdir, "%(id)s")

            # 下载字幕和元数据
            cmd = [
                "yt-dlp",
                "--write-auto-sub",
                "--sub-lang", "zh-Hans,zh,en",
                "--skip-download",
                "--write-info-json",
                "--no-warnings",
                "-o", out_template,
                url,
            ]
            subprocess.run(cmd, capture_output=True, timeout=60)

            # 读取 info.json
            info_files = glob.glob(os.path.join(tmpdir, "*.info.json"))
            info = {}
            if info_files:
                with open(info_files[0], encoding="utf-8", errors="ignore") as f:
                    info = json.load(f)

            title = info.get("title", "")
            channel = info.get("uploader") or info.get("channel") or ""
            upload_date_raw = info.get("upload_date", "")
            upload_date = ""
            if upload_date_raw and len(upload_date_raw) == 8:
                upload_date = f"{upload_date_raw[:4]}-{upload_date_raw[4:6]}-{upload_date_raw[6:]}"
            duration_s = info.get("duration", 0)
            duration = f"{int(duration_s // 60)}:{int(duration_s % 60):02d}" if duration_s else ""
            description = info.get("description", "")

            # 读取字幕（.vtt 优先，.srt 次之）
            transcript = ""
            for ext in ["*.zh-Hans.vtt", "*.zh.vtt", "*.en.vtt", "*.vtt", "*.srt"]:
                sub_files = glob.glob(os.path.join(tmpdir, ext))
                if sub_files:
                    raw_sub = open(sub_files[0], encoding="utf-8", errors="ignore").read()
                    transcript = self._clean_subtitle(raw_sub)
                    break

            if not title and not description:
                raise RuntimeError("yt-dlp 未返回任何内容")

            # 组装 Markdown
            parts = [f"# {title}" if title else "# (无标题)"]
            meta_parts = []
            if channel:
                meta_parts.append(f"**频道：** {channel}")
            if upload_date:
                meta_parts.append(f"**发布时间：** {upload_date}")
            if duration:
                meta_parts.append(f"**时长：** {duration}")
            if meta_parts:
                parts.append("\n".join(meta_parts))
            if description:
                parts.append(f"## 简介\n{description[:2000]}")
            if transcript:
                parts.append(f"## 字幕\n{transcript}")

            content = "\n\n".join(parts)
            content = truncate_content(content, max_chars)

            return FetchResult(
                platform="youtube",
                title=title,
                author=channel,
                content=content,
                url=url,
                source="yt-dlp",
            )

    def _clean_subtitle(self, raw: str) -> str:
        """去掉 VTT/SRT 时间码，提取纯文本"""
        # 去掉 WEBVTT 头
        raw = re.sub(r'^WEBVTT.*?\n\n', '', raw, flags=re.DOTALL)
        # 去掉时间码行
        raw = re.sub(r'\d{2}:\d{2}:\d{2}[.,]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[.,]\d{3}[^\n]*\n', '', raw)
        # 去掉 SRT 序号
        raw = re.sub(r'^\d+\n', '', raw, flags=re.MULTILINE)
        # 去掉 HTML 标签
        raw = re.sub(r'<[^>]+>', '', raw)
        # 合并空行
        raw = re.sub(r'\n{3,}', '\n\n', raw)
        return raw.strip()

    def _fetch_via_tikhub(self, url: str, api_key: str, max_chars: int) -> FetchResult:
        api_url = f"https://api.tikhub.io/api/v1/youtube/web/fetch_video_info?url={urllib.parse.quote(url)}"
        req = urllib.request.Request(api_url)
        req.add_header("Authorization", f"Bearer {api_key}")
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())

        video = data.get("data", {})
        title = video.get("title", "")
        channel = video.get("author", "")
        description = video.get("description", "")
        duration = video.get("duration", "")

        parts = [f"# {title}" if title else "# (无标题)"]
        if channel:
            parts.append(f"**频道：** {channel}")
        if duration:
            parts.append(f"**时长：** {duration}")
        if description:
            parts.append(f"## 简介\n{description}")

        content = "\n\n".join(parts)
        content = truncate_content(content, max_chars)

        return FetchResult(
            platform="youtube",
            title=title,
            author=channel,
            content=content,
            url=url,
            source="tikhub",
        )
