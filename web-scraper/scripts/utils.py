"""
utils.py — 公共工具函数
HTML 清洗、标题提取、内容截断等
"""

import re
import html
from typing import Optional


def fix_lazy_images(html_raw: str) -> str:
    """将 data-src 懒加载图片提升为 src（微信/飞书等平台通用）"""
    return re.sub(
        r'<img([^>]*?)\sdata-src="([^"]+)"([^>]*?)>',
        lambda m: f'<img{m.group(1)} src="{m.group(2)}"{m.group(3)}>',
        html_raw
    )


def clean_markdown(md: str, max_chars: int = 30000) -> str:
    """清理 Markdown 文本：去除多余空行、截断"""
    md = re.sub(r'\n{3,}', '\n\n', md)
    md = md.strip()
    return md[:max_chars]


def extract_title_from_html(html_raw: str) -> str:
    """从 HTML 中提取 title 标签内容"""
    m = re.search(r'<title[^>]*>([^<]+)</title>', html_raw, re.IGNORECASE)
    if m:
        return html.unescape(m.group(1).strip())
    # og:title fallback
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html_raw, re.IGNORECASE)
    if m:
        return html.unescape(m.group(1).strip())
    return ""


def extract_author_from_html(html_raw: str) -> str:
    """从 HTML 中提取作者信息（og:author / meta author）"""
    patterns = [
        r'<meta[^>]+property=["\']og:article:author["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']author["\'][^>]+content=["\']([^"\']+)["\']',
    ]
    for pat in patterns:
        m = re.search(pat, html_raw, re.IGNORECASE)
        if m:
            return html.unescape(m.group(1).strip())
    return ""


def detect_platform(url: str) -> str:
    """根据 URL 判断平台类型"""
    url_lower = url.lower()
    if "mp.weixin.qq.com" in url_lower:
        return "wechat_mp"
    if "feishu.cn" in url_lower or "larksuite.com" in url_lower:
        return "feishu"
    if "github.com" in url_lower or "raw.githubusercontent.com" in url_lower:
        return "github"
    if "twitter.com" in url_lower or "x.com" in url_lower:
        return "twitter"
    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "youtube"
    if "dedao.cn" in url_lower:
        return "dedao"
    return "web"


def truncate_content(content: str, max_chars: int) -> str:
    """安全截断内容（避免截断 Unicode 字符）"""
    if len(content) <= max_chars:
        return content
    return content[:max_chars] + "\n\n...[内容已截断]"


def is_github_repo_root(url: str) -> bool:
    """判断 URL 是否为 GitHub 仓库主页（精确两段路径：user/repo）"""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if "github.com" not in parsed.netloc.lower():
        return False
    path_parts = [p for p in parsed.path.strip("/").split("/") if p]
    return len(path_parts) == 2
