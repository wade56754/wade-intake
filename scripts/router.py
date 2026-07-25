#!/usr/bin/env python3
"""
平台路由器 — 输入 URL，返回 {platform, tool, mode, ...params}
支持：微信公众号、小红书、抖音、TikTok、X/Twitter、YouTube、知乎、飞书、通用网页
"""

import re
import urllib.request
import urllib.error


def _resolve_redirect(url, timeout=5):
    """解析短链重定向，返回最终 URL"""
    try:
        req = urllib.request.Request(url, method='HEAD')
        req.add_header('User-Agent', 'Mozilla/5.0')
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.url
    except Exception:
        # HEAD 不行就 GET
        try:
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'Mozilla/5.0')
            resp = urllib.request.urlopen(req, timeout=timeout)
            return resp.url
        except Exception:
            return url


def _extract_xhs_note_id(url):
    """从小红书 URL 提取 note_id"""
    # 格式: /explore/{note_id} 或 /discovery/item/{note_id}
    m = re.search(r'/(?:explore|discovery/item)/([a-f0-9]{24})', url)
    if m:
        return m.group(1)
    # 格式: note_id 在路径最后
    m = re.search(r'/([a-f0-9]{24})', url)
    if m:
        return m.group(1)
    return None


def _extract_youtube_video_id(url):
    """从 YouTube URL 提取 video_id"""
    # 标准格式: youtube.com/watch?v=xxx
    m = re.search(r'[?&]v=([a-zA-Z0-9_-]{11})', url)
    if m:
        return m.group(1)
    # 短链: youtu.be/xxx
    m = re.search(r'youtu\.be/([a-zA-Z0-9_-]{11})', url)
    if m:
        return m.group(1)
    # embed 格式
    m = re.search(r'youtube\.com/embed/([a-zA-Z0-9_-]{11})', url)
    if m:
        return m.group(1)
    return None


def _extract_twitter_info(url):
    """
    从 X/Twitter URL 提取用户和帖子ID
    返回 (username, tweet_id) — tweet_id 可能为 None（主页模式）
    """
    # 单帖: x.com/{user}/status/{id} 或 twitter.com/{user}/status/{id}
    m = re.search(r'(?:x\.com|twitter\.com)/([^/]+)/status/(\d+)', url)
    if m:
        return m.group(1), m.group(2)
    # 主页: x.com/{user}
    m = re.search(r'(?:x\.com|twitter\.com)/([^/?#]+)', url)
    if m:
        username = m.group(1)
        # 排除非用户页面的路径
        if username.lower() not in ('home', 'explore', 'search', 'notifications',
                                     'messages', 'settings', 'i', 'compose'):
            return username, None
    return None, None


def _extract_douyin_video_id(url):
    """从抖音 URL 提取 video_id"""
    # v.douyin.com/xxx 短链需要先重定向
    m = re.search(r'douyin\.com/video/(\d+)', url)
    if m:
        return m.group(1)
    m = re.search(r'douyin\.com/note/(\d+)', url)
    if m:
        return m.group(1)
    return None


def _extract_tiktok_video_id(url):
    """从 TikTok URL 提取 video_id"""
    m = re.search(r'tiktok\.com/@[^/]+/video/(\d+)', url)
    if m:
        return m.group(1)
    # vm.tiktok.com 短链需先解析
    m = re.search(r'/video/(\d+)', url)
    if m:
        return m.group(1)
    return None


def _extract_zhihu_info(url):
    """从知乎 URL 提取 question_id / answer_id / article_id。"""
    m = re.search(r'zhihu\.com/question/(\d+)/answer/(\d+)', url)
    if m:
        return {
            'mode': 'answer',
            'question_id': m.group(1),
            'answer_id': m.group(2),
        }

    m = re.search(r'zhihu\.com/question/(\d+)', url)
    if m:
        return {
            'mode': 'question',
            'question_id': m.group(1),
            'answer_id': None,
        }

    m = re.search(r'zhuanlan\.zhihu\.com/p/(\d+)', url)
    if m:
        return {
            'mode': 'article',
            'article_id': m.group(1),
        }

    return None


def route(url):
    """
    路由入口 — 根据 URL 判断平台、工具、模式和参数
    返回 dict: {platform, tool, mode, url, ...params}
    """
    url = url.strip()

    # === 微信公众号 ===
    if 'mp.weixin.qq.com' in url:
        return {
            'platform': 'wechat_mp',
            'tool': 'tikhub',
            'mode': 'article',
            'url': url,
        }

    # === 小红书 ===
    if 'xiaohongshu.com' in url or 'xhslink.com' in url:
        # 短链先解析
        if 'xhslink.com' in url:
            url = _resolve_redirect(url)
        note_id = _extract_xhs_note_id(url)
        return {
            'platform': 'xiaohongshu',
            'tool': 'tikhub',
            'mode': 'note',
            'url': url,
            'note_id': note_id,
        }

    # === 抖音 ===
    if 'douyin.com' in url:
        # 短链重定向
        if 'v.douyin.com' in url:
            url = _resolve_redirect(url)
        video_id = _extract_douyin_video_id(url)
        return {
            'platform': 'douyin',
            'tool': 'tikhub',
            'mode': 'video',
            'url': url,
            'video_id': video_id,
        }

    # === TikTok ===
    if 'tiktok.com' in url:
        # 短链重定向
        if 'vm.tiktok.com' in url:
            url = _resolve_redirect(url)
        video_id = _extract_tiktok_video_id(url)
        return {
            'platform': 'tiktok',
            'tool': 'tikhub',
            'mode': 'video',
            'url': url,
            'video_id': video_id,
        }

    # === YouTube ===
    if 'youtube.com' in url or 'youtu.be' in url:
        video_id = _extract_youtube_video_id(url)
        return {
            'platform': 'youtube',
            'tool': 'tikhub',
            'mode': 'video',
            'url': url,
            'video_id': video_id,
        }

    # === X/Twitter ===
    if 'x.com' in url or 'twitter.com' in url:
        username, tweet_id = _extract_twitter_info(url)
        if tweet_id:
            return {
                'platform': 'twitter',
                'tool': 'tikhub',
                'mode': 'tweet',
                'url': url,
                'username': username,
                'tweet_id': tweet_id,
            }
        elif username:
            return {
                'platform': 'twitter',
                'tool': 'tikhub',
                'mode': 'profile',
                'url': url,
                'username': username,
            }

    # === 知乎 ===
    if 'zhihu.com' in url:
        zhihu_info = _extract_zhihu_info(url)
        if zhihu_info:
            return {
                'platform': 'zhihu',
                'tool': 'tikhub',
                'url': url,
                **zhihu_info,
            }

    # === 飞书 ===
    if 'feishu.cn' in url or 'larksuite.com' in url or 'lark.com' in url:
        # 提取 document_id
        doc_id = None
        m = re.search(r'/(?:docx|wiki|sheets|bitable|base)/([a-zA-Z0-9]+)', url)
        if m:
            doc_id = m.group(1)
        return {
            'platform': 'feishu',
            'tool': 'feishu_mcp',
            'mode': 'document',
            'url': url,
            'doc_id': doc_id,
        }

    # === GitHub ===
    if 'github.com' in url:
        # 提取 owner/repo
        m = re.search(r'github\.com/([^/]+)/([^/?#]+)', url)
        owner = m.group(1) if m else None
        repo = m.group(2) if m else None
        return {
            'platform': 'github',
            'tool': 'gh',
            'mode': 'repo',
            'url': url,
            'owner': owner,
            'repo': repo,
        }

    # === FlowUs ===
    if 'flowus.cn' in url:
        return {
            'platform': 'flowus',
            'tool': 'scrapling',
            'mode': 'page',
            'url': url,
        }

    # === 通用网页（兜底） ===
    return {
        'platform': 'web',
        'tool': 'web',
        'mode': 'page',
        'url': url,
    }


if __name__ == '__main__':
    import json
    import sys
    test_url = sys.argv[1] if len(sys.argv) > 1 else 'https://mp.weixin.qq.com/s/test'
    result = route(test_url)
    print(json.dumps(result, ensure_ascii=False, indent=2))
