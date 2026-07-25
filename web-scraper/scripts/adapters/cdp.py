"""
cdp.py — Chrome CDP 适配器
复用用户已登录的 Chrome，无需 cookie 导出
支持通用网页 + 飞书专用选择器
"""

import os
import re
import subprocess
import time
import urllib.request

from .base import BaseFetcher, FetchResult
from utils import detect_platform, truncate_content

# cdp.mjs 就在本仓库同级 scripts/ 目录下
CDP_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cdp.mjs")
CDP_PORT = "9222"


def _is_cdp_available() -> bool:
    """检查 Chrome CDP 端口是否可用"""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{CDP_PORT}/json/version")
        urllib.request.urlopen(req, timeout=3)
        return True
    except Exception:
        return False


class CdpFetcher(BaseFetcher):
    """Chrome CDP 适配器 — 用于需要登录态或 JS 渲染的页面"""

    # 飞书专用 CSS 选择器（按优先级）
    FEISHU_SELECTORS = [
        ".wiki-content",
        ".doc-content",
        "[data-page-id]",
    ]

    # 通用选择器
    GENERAL_SELECTOR = "article, main, .content, [role=main]"

    def fetch(self, url: str, **kwargs) -> FetchResult:
        max_chars = kwargs.get("max_chars", 30000)
        wait_ms = kwargs.get("wait_ms", 5000)  # 等待页面加载的时间（ms）
        platform = detect_platform(url)

        if not _is_cdp_available():
            raise RuntimeError(
                "Chrome CDP 端口 9222 未开启。"
                "请先用 --remote-debugging-port=9222 启动 Chrome"
                "（独立 profile 示例：--user-data-dir=~/.chrome-cdp）"
            )

        # 1. 打开新标签页导航
        open_result = subprocess.run(
            ["node", CDP_SCRIPT, "open", url],
            capture_output=True, text=True, timeout=15,
        )
        target_match = re.search(r"([A-F0-9a-f]{8,})", open_result.stdout)
        if not target_match:
            raise RuntimeError(f"CDP: 无法获取 targetId. stdout={open_result.stdout[:200]}")
        target_id = target_match.group(1)

        # 2. 等待页面加载
        time.sleep(wait_ms / 1000.0)

        # 3. 根据平台选择合适的选择器
        if platform == "feishu":
            content, title = self._extract_feishu(target_id)
        else:
            content, title = self._extract_general(target_id)

        if not content or len(content) < 100:
            raise RuntimeError(f"CDP 提取内容过短 ({len(content)} chars)")

        content = truncate_content(content, max_chars)

        return FetchResult(
            platform=platform,
            title=title,
            author="",
            content=content,
            url=url,
            source="chrome-cdp",
        )

    def _extract_general(self, target_id: str):
        """通用内容提取"""
        # 提取正文
        eval_expr = (
            f"document.querySelector('{self.GENERAL_SELECTOR}')?.innerText "
            "|| document.body.innerText"
        )
        content_result = subprocess.run(
            ["node", CDP_SCRIPT, "eval", target_id, eval_expr],
            capture_output=True, text=True, timeout=15,
        )
        content = content_result.stdout.strip()

        # 提取标题
        title_result = subprocess.run(
            ["node", CDP_SCRIPT, "eval", target_id, "document.title"],
            capture_output=True, text=True, timeout=5,
        )
        title = title_result.stdout.strip() or ""

        return content, title

    def _extract_feishu(self, target_id: str):
        """飞书专用内容提取，按选择器优先级尝试"""
        for selector in self.FEISHU_SELECTORS:
            eval_expr = f"document.querySelector('{selector}')?.innerText"
            result = subprocess.run(
                ["node", CDP_SCRIPT, "eval", target_id, eval_expr],
                capture_output=True, text=True, timeout=15,
            )
            content = result.stdout.strip()
            if content and len(content) > 200:
                # 提取标题
                title_result = subprocess.run(
                    ["node", CDP_SCRIPT, "eval", target_id, "document.title"],
                    capture_output=True, text=True, timeout=5,
                )
                title = title_result.stdout.strip() or ""
                return content, title

        # 最终降级
        return self._extract_general(target_id)
