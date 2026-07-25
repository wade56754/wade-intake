"""
wechat.py — 微信公众号适配器
三级降级：curl直接抓取 → Scrapling渲染 → urllib兜底
"""

import re
import subprocess
from datetime import datetime

from .base import BaseFetcher, FetchResult
from utils import fix_lazy_images, truncate_content


class WechatFetcher(BaseFetcher):
    """微信公众号文章适配器"""

    def can_handle(self, url: str) -> bool:
        return "mp.weixin.qq.com" in url

    def fetch(self, url: str, **kwargs) -> FetchResult:
        max_chars = kwargs.get("max_chars", 30000)

        # 1. curl 直接抓取（最快，成功率高）
        try:
            return self._fetch_via_curl(url, max_chars)
        except Exception as e:
            pass

        # 2. Scrapling 渲染（动态内容更稳定）
        try:
            return self._fetch_via_scrapling(url, max_chars)
        except Exception as e:
            pass

        # 3. urllib 兜底
        try:
            return self._fetch_via_urllib(url, max_chars)
        except Exception as e:
            raise RuntimeError(f"微信公众号三级降级全部失败: {e}")

    def _fetch_via_curl(self, url: str, max_chars: int) -> FetchResult:
        result = subprocess.run(
            [
                "curl", "-sL",
                "-H", "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                url,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"curl 失败: {result.stderr[:200]}")

        html = result.stdout
        if len(html) < 1000:
            raise RuntimeError("curl 返回内容过短，可能被反爬拦截")

        return self._parse_html(html, url, max_chars, source="wechat-curl")

    def _fetch_via_scrapling(self, url: str, max_chars: int) -> FetchResult:
        try:
            import html2text
            from scrapling.fetchers import Fetcher
        except ImportError as e:
            raise RuntimeError(f"Scrapling 未安装: {e}")

        page = Fetcher(auto_match=False).get(
            url,
            headers={"Referer": "https://www.google.com/search?q=site"},
        )
        html = page.html_content
        return self._parse_html(html, url, max_chars, source="wechat-scrapling")

    def _fetch_via_urllib(self, url: str, max_chars: int) -> FetchResult:
        import urllib.request
        req = urllib.request.Request(url)
        req.add_header(
            "User-Agent",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        resp = urllib.request.urlopen(req, timeout=15)
        html = resp.read().decode("utf-8", errors="ignore")
        return self._parse_html(html, url, max_chars, source="wechat-urllib")

    def _parse_html(self, html: str, url: str, max_chars: int, source: str) -> FetchResult:
        """从微信公众号 HTML 中提取结构化内容"""
        meta = _extract_wechat_meta(html)
        content = _extract_wechat_content(html)

        if not content or len(content) < 100:
            raise RuntimeError(f"微信公众号内容提取失败 (source={source})")

        content = truncate_content(content, max_chars)

        return FetchResult(
            platform="wechat_mp",
            title=meta.get("title", ""),
            author=meta.get("author", ""),
            content=content,
            url=url,
            source=source,
        )


def _extract_wechat_meta(html: str) -> dict:
    """从微信公众号 HTML 提取元数据"""
    meta = {}

    m = re.search(r'var msg_title\s*=\s*["\']([^"\']*)["\']', html)
    if m:
        meta["title"] = m.group(1).strip()
    else:
        m = re.search(r'<meta\s+property="og:title"\s+content="([^"]*)"', html)
        if m:
            meta["title"] = m.group(1).strip()

    m = re.search(r'var nickname\s*=\s*["\']([^"\']*)["\']', html)
    if m:
        meta["author"] = m.group(1).strip()
    else:
        m = re.search(r'<meta\s+property="og:article:author"\s+content="([^"]*)"', html)
        if m:
            meta["author"] = m.group(1).strip()

    m = re.search(r'var ct\s*=\s*["\'](\d+)["\']', html)
    if m:
        ts = int(m.group(1))
        meta["publish_time"] = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

    return meta


class _WechatParser:
    """简单状态机提取微信公众号正文"""

    def __init__(self):
        self.in_content = False
        self.depth = 0
        self.texts = []

    def feed(self, html_str: str):
        import html.parser as _html_parser

        class _Parser(_html_parser.HTMLParser):
            def __init__(inner):
                super().__init__()
                inner.in_content = False
                inner.depth = 0
                inner.skip = False
                inner.texts = []

            def handle_starttag(inner, tag, attrs):
                attrs_dict = dict(attrs)
                cls = attrs_dict.get("class", "")
                id_ = attrs_dict.get("id", "")
                if id_ == "js_content" or "rich_media_content" in cls:
                    inner.in_content = True
                    inner.depth = 0
                if inner.in_content:
                    inner.depth += 1
                if tag in ("script", "style", "noscript"):
                    inner.skip = True
                if tag in ("p", "br", "div", "h1", "h2", "h3", "h4", "h5", "section", "li") and inner.in_content:
                    inner.texts.append("\n")
                if tag == "li" and inner.in_content:
                    inner.texts.append("• ")
                if tag in ("strong", "b") and inner.in_content:
                    inner.texts.append("**")

            def handle_endtag(inner, tag):
                if tag in ("script", "style", "noscript"):
                    inner.skip = False
                if inner.in_content:
                    inner.depth -= 1
                    if inner.depth <= 0:
                        inner.in_content = False
                if tag in ("strong", "b") and inner.in_content:
                    inner.texts.append("**")

            def handle_data(inner, data):
                if inner.in_content and not inner.skip:
                    text = data.strip()
                    if text:
                        inner.texts.append(text)

        p = _Parser()
        p.feed(html_str)
        self.texts = p.texts


def _extract_wechat_content(html: str) -> str:
    """从微信公众号 HTML 提取正文"""
    parser = _WechatParser()
    parser.feed(html)
    content = "\n".join(parser.texts)
    content = re.sub(r"\n{3,}", "\n\n", content)
    return content.strip()
