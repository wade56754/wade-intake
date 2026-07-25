#!/usr/bin/env python3
"""
小红书抓取器
- 短链先解析重定向拿真实 URL 再提取 note_id
- 主接口：TikHub web/get_note_info_v7（最稳定）
- 备用接口：TikHub app/get_note_info
- 提取：标题、正文、点赞数、收藏数、评论数、作者
"""

import json
import re
import time
import urllib.request

from fetchers.base import fetch as tikhub_fetch


def _resolve_short_link(url):
    """解析小红书短链，拿到最终 URL"""
    if 'xhslink.com' not in url:
        return url
    try:
        req = urllib.request.Request(url, method='HEAD')
        req.add_header('User-Agent', 'Mozilla/5.0')
        resp = urllib.request.urlopen(req, timeout=10)
        return resp.url
    except Exception:
        try:
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'Mozilla/5.0')
            resp = urllib.request.urlopen(req, timeout=10)
            return resp.url
        except Exception:
            return url


def _extract_note_id(url):
    """从小红书 URL 提取 note_id"""
    # /explore/{note_id} 或 /discovery/item/{note_id}
    m = re.search(r'/(?:explore|discovery/item)/([a-f0-9]{24})', url)
    if m:
        return m.group(1)
    # 通用 24位十六进制
    m = re.search(r'/([a-f0-9]{24})', url)
    if m:
        return m.group(1)
    return None


def _fetch_via_web_v7(note_id, retries=2):
    """主接口：web/get_note_info_v7（带重试，API 偶尔 400）"""
    for attempt in range(retries + 1):
        try:
            data = tikhub_fetch(
                '/api/v1/xiaohongshu/web/get_note_info_v7',
                params={'note_id': note_id},
                max_retries=0,  # 我们自己控制重试
            )
            # 结构: data -> data (list) -> [0] -> note_list -> [0]
            inner = data.get('data', [])
            if isinstance(inner, list) and inner:
                note_list = inner[0].get('note_list', [])
                if note_list:
                    return note_list[0]
            # data 为空列表 = 笔记不存在，不重试
            if isinstance(inner, list) and not inner:
                return None
        except Exception:
            pass
        if attempt < retries:
            time.sleep(2)
    return None


def _fetch_via_app_v1(note_id, retries=1):
    """备用接口：app/get_note_info"""
    for attempt in range(retries + 1):
        try:
            data = tikhub_fetch(
                '/api/v1/xiaohongshu/app/get_note_info',
                params={'note_id': note_id},
                max_retries=0,
            )
            # 结构: data -> data (dict) -> data (list)
            inner = data.get('data', {})
            if isinstance(inner, dict):
                nested = inner.get('data', [])
                if isinstance(nested, list) and nested:
                    return nested[0]
                if inner.get('title') or inner.get('desc'):
                    return inner
            return None
        except Exception:
            pass
        if attempt < retries:
            time.sleep(2)
    return None


def _parse_note_data(note):
    """解析笔记数据，返回标准化结构"""
    if not note:
        return None

    # 标题
    title = note.get('title') or note.get('display_title') or ''

    # 正文/描述
    desc = note.get('desc') or note.get('description') or ''

    # 互动数据 — web_v7 直接在 note 顶层
    likes = note.get('liked_count', 0)
    collects = note.get('collected_count', 0)
    comments = note.get('comments_count', 0)
    shares = note.get('shared_count', 0)
    views = note.get('view_count', 0)

    # 也检查 interact_info（兼容 app 接口）
    interact_info = note.get('interact_info', {})
    if interact_info:
        likes = likes or interact_info.get('liked_count', 0)
        collects = collects or interact_info.get('collected_count', 0)
        comments = comments or interact_info.get('comment_count', 0)

    # 作者
    user_info = note.get('user', {})
    author = user_info.get('nickname') or user_info.get('nick_name') or ''

    # 标签/话题
    tags = []
    tag_list = note.get('topics') or note.get('tag_list') or []
    for t in tag_list:
        if isinstance(t, dict):
            name = t.get('name', '')
            if name:
                tags.append(name)
        elif isinstance(t, str):
            tags.append(t)

    # 笔记类型
    note_type = note.get('type', 'normal')

    # IP 属地
    ip_location = note.get('ip_location', '')

    # 发布时间
    create_time = note.get('time') or note.get('create_time') or ''

    return {
        'title': title,
        'author': author,
        'content': desc,
        'desc': desc,
        'note_type': note_type,
        'likes': _to_int(likes),
        'collects': _to_int(collects),
        'comments': _to_int(comments),
        'shares': _to_int(shares),
        'views': _to_int(views),
        'tags': tags,
        'ip_location': ip_location,
        'publish_time': str(create_time),
        'platform': 'xiaohongshu',
        'source': 'tikhub',
    }


def _to_int(val):
    """安全转 int"""
    try:
        if isinstance(val, str):
            # 处理 "1.2万" 之类
            val = val.replace('万', '0000').replace('w', '0000')
            val = re.sub(r'[^\d]', '', val)
        return int(val) if val else 0
    except (ValueError, TypeError):
        return 0


def fetch_note(url, note_id=None):
    """
    抓取小红书笔记
    返回标准化结构
    """
    # 解析短链
    url = _resolve_short_link(url)

    # 提取 note_id
    if not note_id:
        note_id = _extract_note_id(url)

    if not note_id:
        return {'error': f'无法从 URL 提取 note_id: {url}', 'platform': 'xiaohongshu'}

    # 主接口：web/get_note_info_v7
    note = _fetch_via_web_v7(note_id)

    # 备用接口：app/get_note_info
    if not note:
        note = _fetch_via_app_v1(note_id)

    if not note:
        return {'error': f'无法获取笔记数据: {note_id}', 'platform': 'xiaohongshu'}

    result = _parse_note_data(note)
    if not result:
        return {'error': f'解析笔记数据失败: {note_id}', 'platform': 'xiaohongshu'}

    result['note_id'] = note_id
    result['url'] = url
    return result


if __name__ == '__main__':
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else 'https://www.xiaohongshu.com/explore/test'
    result = fetch_note(url)
    print(json.dumps(result, ensure_ascii=False, indent=2)[:2000])
