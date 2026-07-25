"""
github.py — GitHub 仓库适配器
主路径：GitHub REST API（无需 Token）
降级：JinaFetcher → UrllibFetcher
"""

import base64
import json
import os
import urllib.request
from urllib.parse import urlparse

from .base import BaseFetcher, FetchResult
from utils import truncate_content


class GithubFetcher(BaseFetcher):
    """GitHub 仓库主页适配器"""

    API_BASE = "https://api.github.com"

    def can_handle(self, url: str) -> bool:
        return "github.com" in url.lower()

    def fetch(self, url: str, **kwargs) -> FetchResult:
        max_chars = kwargs.get("max_chars", 30000)
        parsed = urlparse(url)
        path_parts = [p for p in parsed.path.strip("/").split("/") if p]
        if len(path_parts) < 2:
            raise RuntimeError(f"GitHub URL 路径不足两段: {url}")
        owner, repo = path_parts[0], path_parts[1]

        # 1. GitHub REST API
        try:
            return self._fetch_via_api(url, owner, repo, max_chars)
        except Exception as e:
            pass

        # 2. Jina Reader
        try:
            from .jina import JinaFetcher
            result = JinaFetcher().fetch(url, **kwargs)
            result.platform = "github"
            return result
        except Exception:
            pass

        # 3. urllib 兜底
        try:
            from .urllib_adapter import UrllibFetcher
            result = UrllibFetcher().fetch(url, **kwargs)
            result.platform = "github"
            return result
        except Exception as e:
            raise RuntimeError(f"GitHub 三级降级全部失败: {e}")

    def _fetch_via_api(self, url: str, owner: str, repo: str, max_chars: int) -> FetchResult:
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "web-scraper/1.0",
        }
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"token {token}"

        # 获取仓库元数据
        req = urllib.request.Request(
            f"{self.API_BASE}/repos/{owner}/{repo}",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            repo_data = json.loads(resp.read().decode())

        description = repo_data.get("description") or ""
        stars = repo_data.get("stargazers_count", 0)
        language = repo_data.get("language") or "Unknown"
        homepage = repo_data.get("homepage") or ""

        # 获取 README
        readme_content = ""
        try:
            req_readme = urllib.request.Request(
                f"{self.API_BASE}/repos/{owner}/{repo}/readme",
                headers=headers,
            )
            with urllib.request.urlopen(req_readme, timeout=15) as resp:
                readme_data = json.loads(resp.read().decode())
            encoded = readme_data.get("content", "")
            readme_content = base64.b64decode(encoded).decode("utf-8", errors="ignore")
        except Exception:
            pass

        # 组装 Markdown 内容
        meta_line = f"> {description}" if description else ""
        if stars:
            meta_line += f" | ⭐ {stars}"
        if language != "Unknown":
            meta_line += f" | 语言: {language}"
        if homepage:
            meta_line += f" | 🔗 {homepage}"

        content_parts = [f"# {owner}/{repo}"]
        if meta_line:
            content_parts.append(meta_line)
        if readme_content:
            content_parts.append("\n## README\n")
            content_parts.append(readme_content)

        content = "\n\n".join(content_parts)
        content = truncate_content(content, max_chars)

        return FetchResult(
            platform="github",
            title=f"{owner}/{repo}",
            author=owner,
            content=content,
            url=url,
            source="github-api",
        )
