"""
feishu.py — 飞书/Lark 文档适配器
降级链：飞书 Open API（tenant_access_token）→ Chrome CDP（登录态）→ Jina Reader → urllib 兜底
"""

import json
import re
import os
import subprocess
import time
import urllib.request
import urllib.parse
import urllib.error

from .base import BaseFetcher, FetchResult
from utils import truncate_content
from .cdp import _is_cdp_available, CDP_SCRIPT


def _quick_cdp_check() -> bool:
    """快速检测 CDP 是否可用，依次尝试 9222（标准）和 18800（备用/第二浏览器实例）"""
    import os
    timeout = float(os.environ.get("FEISHU_CDP_TIMEOUT", "2"))
    for port in [9222, 18800]:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout)
            return True
        except Exception:
            continue
    return False


def _get_cdp_port() -> int:
    """返回可用的 CDP 端口（9222 或 18800）"""
    import os
    timeout = float(os.environ.get("FEISHU_CDP_TIMEOUT", "2"))
    for port in [9222, 18800]:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout)
            return port
        except Exception:
            continue
    return 9222

# 飞书 CSS 选择器（按优先级）
FEISHU_SELECTORS = [
    ".wiki-content",
    ".doc-content",
    "[data-page-id]",
]


class FeishuFetcher(BaseFetcher):
    """飞书文档适配器"""

    def can_handle(self, url: str) -> bool:
        url_lower = url.lower()
        return "feishu.cn" in url_lower or "larksuite.com" in url_lower

    def fetch(self, url: str, **kwargs) -> FetchResult:
        max_chars = kwargs.get("max_chars", 30000)
        wait_ms = kwargs.get("wait_ms", 5000)

        # 1. 飞书 Open API（最稳，读取 .env 里的 App ID/Secret）
        app_id = os.environ.get("FEISHU_APP_ID", "")
        app_secret = os.environ.get("FEISHU_APP_SECRET", "")
        if app_id and app_secret:
            try:
                return self._fetch_via_open_api(url, max_chars, app_id, app_secret)
            except Exception:
                pass

        # 2. Chrome CDP（最佳，保留登录态）
        if _quick_cdp_check():
            try:
                return self._fetch_via_cdp(url, max_chars, wait_ms)
            except Exception:
                pass

        # 3. Jina Reader（公开文档可用）
        try:
            return self._fetch_via_jina(url, max_chars)
        except Exception:
            pass

        # 4. urllib 兜底
        try:
            return self._fetch_via_urllib(url, max_chars)
        except Exception as e:
            raise RuntimeError(f"飞书文档四级降级全部失败: {e}")

    def _fetch_via_open_api(self, url: str, max_chars: int, app_id: str, app_secret: str) -> FetchResult:
        """通过飞书 Open API 获取文档内容（tenant_access_token 方案）"""

        # 1. 获取 tenant_access_token
        token_url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        token_data = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
        req = urllib.request.Request(token_url, data=token_data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            token_resp = json.loads(resp.read())
        if token_resp.get("code") != 0:
            raise RuntimeError(f"飞书 token 获取失败: {token_resp.get('msg')}")
        token = token_resp["tenant_access_token"]

        # 2. 解析 URL，判断文档类型和 ID
        # 支持格式：
        #   wiki: /wiki/{space_node_token}
        #   docx: /docx/{doc_token}
        #   doc:  /doc/{doc_token}
        doc_token = None
        doc_type = None

        m_wiki = re.search(r"/wiki/([A-Za-z0-9_-]+)", url)
        m_docx = re.search(r"/docx/([A-Za-z0-9_-]+)", url)
        m_doc  = re.search(r"/doc/([A-Za-z0-9_-]+)", url)

        if m_wiki:
            doc_token = m_wiki.group(1)
            doc_type = "wiki"
        elif m_docx:
            doc_token = m_docx.group(1)
            doc_type = "docx"
        elif m_doc:
            doc_token = m_doc.group(1)
            doc_type = "doc"
        else:
            raise RuntimeError(f"无法解析飞书文档 token: {url}")

        auth_header = {"Authorization": f"Bearer {token}"}

        # 3. wiki 节点需要先获取实际 obj_token 和 obj_type
        title = ""
        if doc_type == "wiki":
            node_url = f"https://open.feishu.cn/open-apis/wiki/v2/spaces/get_node?token={doc_token}"
            req = urllib.request.Request(node_url, headers=auth_header)
            with urllib.request.urlopen(req, timeout=10) as resp:
                node_resp = json.loads(resp.read())
            if node_resp.get("code") != 0:
                raise RuntimeError(f"wiki 节点获取失败: {node_resp.get('msg')}")
            node = node_resp["data"]["node"]
            doc_token = node["obj_token"]
            doc_type = node["obj_type"]   # "docx" or "doc"
            title = node.get("title", "")

        # 4. 获取文档内容（纯文本）
        if doc_type == "docx":
            content_url = f"https://open.feishu.cn/open-apis/docx/v1/documents/{doc_token}/raw_content"
            req = urllib.request.Request(content_url, headers=auth_header)
            with urllib.request.urlopen(req, timeout=15) as resp:
                content_resp = json.loads(resp.read())
            if content_resp.get("code") != 0:
                raise RuntimeError(f"docx 内容获取失败: {content_resp.get('msg')}")
            content = content_resp["data"].get("content", "")
            if not title:
                # 尝试从文档元数据获取标题
                try:
                    meta_url = f"https://open.feishu.cn/open-apis/docx/v1/documents/{doc_token}"
                    req2 = urllib.request.Request(meta_url, headers=auth_header)
                    with urllib.request.urlopen(req2, timeout=10) as resp2:
                        meta = json.loads(resp2.read())
                    title = meta["data"]["document"].get("title", "")
                except Exception:
                    pass
        elif doc_type in ("doc", "sheet"):
            # 旧版 doc API
            content_url = f"https://open.feishu.cn/open-apis/doc/v2/{doc_token}/content"
            req = urllib.request.Request(content_url, headers=auth_header)
            with urllib.request.urlopen(req, timeout=15) as resp:
                content_resp = json.loads(resp.read())
            if content_resp.get("code") != 0:
                raise RuntimeError(f"doc 内容获取失败: {content_resp.get('msg')}")
            # 旧版 doc 返回富文本 JSON，提取纯文本
            body = content_resp.get("data", {}).get("content", "{}")
            try:
                body_obj = json.loads(body) if isinstance(body, str) else body
                texts = []
                self._extract_text_from_doc(body_obj, texts)
                content = "\n".join(texts)
            except Exception:
                content = str(body)
        else:
            raise RuntimeError(f"不支持的飞书文档类型: {doc_type}")

        if not content or len(content) < 50:
            raise RuntimeError(f"飞书 Open API 返回内容过短 ({len(content)} chars)")

        content = truncate_content(content, max_chars)
        return FetchResult(
            platform="feishu",
            title=title,
            author="",
            content=content,
            url=url,
            source="feishu-open-api",
        )

    def _extract_text_from_doc(self, obj, texts: list):
        """递归提取旧版飞书 doc 富文本中的纯文本"""
        if isinstance(obj, dict):
            if "text" in obj and isinstance(obj["text"], str):
                texts.append(obj["text"])
            for v in obj.values():
                self._extract_text_from_doc(v, texts)
        elif isinstance(obj, list):
            for item in obj:
                self._extract_text_from_doc(item, texts)

    def _fetch_via_cdp(self, url: str, max_chars: int, wait_ms: int) -> FetchResult:
        # 打开标签页
        open_result = subprocess.run(
            ["node", CDP_SCRIPT, "open", url],
            capture_output=True, text=True, timeout=15,
        )
        target_match = re.search(r"([A-F0-9a-f]{8,})", open_result.stdout)
        if not target_match:
            raise RuntimeError(f"CDP: 无法获取 targetId")
        target_id = target_match.group(1)

        time.sleep(wait_ms / 1000.0)

        # 按优先级尝试飞书选择器
        content = ""
        for selector in FEISHU_SELECTORS:
            eval_expr = f"document.querySelector('{selector}')?.innerText"
            result = subprocess.run(
                ["node", CDP_SCRIPT, "eval", target_id, eval_expr],
                capture_output=True, text=True, timeout=15,
            )
            content = result.stdout.strip()
            if content and len(content) > 200:
                break

        # 最终降级：全页文本
        if not content or len(content) < 100:
            eval_expr = "document.body.innerText"
            result = subprocess.run(
                ["node", CDP_SCRIPT, "eval", target_id, eval_expr],
                capture_output=True, text=True, timeout=15,
            )
            content = result.stdout.strip()

        # 提取标题
        title_result = subprocess.run(
            ["node", CDP_SCRIPT, "eval", target_id, "document.title"],
            capture_output=True, text=True, timeout=5,
        )
        title = title_result.stdout.strip() or ""

        if not content or len(content) < 100:
            raise RuntimeError(f"CDP 飞书内容过短 ({len(content)} chars)")

        content = truncate_content(content, max_chars)
        return FetchResult(
            platform="feishu",
            title=title,
            author="",
            content=content,
            url=url,
            source="chrome-cdp",
        )

    def _fetch_via_jina(self, url: str, max_chars: int) -> FetchResult:
        jina_url = f"https://r.jina.ai/{url}"
        req = urllib.request.Request(jina_url)
        req.add_header("Accept", "text/markdown")
        req.add_header("User-Agent", "Mozilla/5.0")
        resp = urllib.request.urlopen(req, timeout=20)
        raw = resp.read().decode("utf-8", errors="ignore").strip()

        if not raw or len(raw) < 100:
            raise RuntimeError("Jina Reader 返回内容过短")

        title = ""
        m = re.search(r"^Title:\s*(.+)$", raw, re.M)
        if m:
            title = m.group(1).strip()

        content = re.sub(
            r"^(Title:.*|URL Source:.*|Published Time:.*|Markdown Content:)\s*\n",
            "", raw, flags=re.M,
        ).strip()
        content = truncate_content(content, max_chars)

        return FetchResult(
            platform="feishu",
            title=title,
            author="",
            content=content,
            url=url,
            source="jina-reader",
        )

    def _fetch_via_urllib(self, url: str, max_chars: int) -> FetchResult:
        req = urllib.request.Request(url)
        req.add_header(
            "User-Agent",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        resp = urllib.request.urlopen(req, timeout=15)
        html = resp.read().decode("utf-8", errors="ignore")

        # 提取标题
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
        title = m.group(1).strip() if m else ""

        # 提取正文（飞书页面结构）
        content = ""
        for selector_pattern in [
            r'class="[^"]*wiki-content[^"]*"[^>]*>(.*?)</div>',
            r'class="[^"]*doc-content[^"]*"[^>]*>(.*?)</div>',
        ]:
            m = re.search(selector_pattern, html, re.S | re.I)
            if m:
                raw = m.group(1)
                content = re.sub(r"<[^>]+>", " ", raw)
                content = re.sub(r"\s+", " ", content).strip()
                if len(content) > 200:
                    break

        if not content:
            # 全页兜底
            body = re.search(r"<body[^>]*>(.*?)</body>", html, re.S | re.I)
            if body:
                content = re.sub(r"<[^>]+>", " ", body.group(1))
                content = re.sub(r"\s+", " ", content).strip()

        if not content or len(content) < 100:
            raise RuntimeError("urllib 飞书内容提取失败")

        content = truncate_content(content, max_chars)
        return FetchResult(
            platform="feishu",
            title=title,
            author="",
            content=content,
            url=url,
            source="urllib",
        )
