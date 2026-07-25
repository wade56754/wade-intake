#!/usr/bin/env python3
"""
抖音/TikTok 抓取器
- 视频标题、描述、字幕
- 支持抖音 aweme_id 和 TikTok video_id
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from fetchers.base import fetch as tikhub_fetch


def _safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _parse_json(text):
    text = (text or '').strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for idx, ch in enumerate(text):
            if ch not in '[{':
                continue
            try:
                obj, _ = decoder.raw_decode(text[idx:])
                return obj
            except json.JSONDecodeError:
                continue
    return None


def _run_mcporter(args, timeout=75):
    if not shutil.which('mcporter'):
        return None, 'mcporter 未安装'
    try:
        proc = subprocess.run(
            ['mcporter', *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as e:
        return None, str(e)
    if proc.returncode != 0:
        return None, (proc.stderr or proc.stdout or f'退出码 {proc.returncode}')[:300]
    data = _parse_json(proc.stdout)
    if data is None:
        return None, (proc.stderr or proc.stdout or 'MCP 返回非 JSON')[:300]
    return data, ''


def _douyin_mcp_available():
    data, error = _run_mcporter(['list', 'douyin', '--json'], timeout=15)
    if error:
        return False, error
    text = json.dumps(data, ensure_ascii=False).lower()
    if 'unknown mcp server' in text:
        return False, 'Unknown MCP server douyin'
    return 'douyin' in text, ''


def _unwrap_aweme(payload):
    data = payload
    if isinstance(data, dict) and 'data' in data:
        data = data.get('data')
    if isinstance(data, dict) and 'result' in data and isinstance(data.get('result'), dict):
        data = data.get('result')
    if isinstance(data, dict) and 'aweme_detail' in data:
        return data.get('aweme_detail') or {}
    if isinstance(data, dict) and 'aweme_info' in data:
        return data.get('aweme_info') or {}
    return data if isinstance(data, dict) else {}


def _extract_video_id(data, fallback=''):
    value = (
        data.get('aweme_id')
        or data.get('video_id')
        or data.get('id')
        or fallback
        or ''
    )
    return str(value).strip()


def _extract_subtitle(data):
    video_info = data.get('video', {})
    if isinstance(video_info, dict):
        subtitle_infos = video_info.get('subtitleInfos', []) or video_info.get('subtitle_infos', [])
        if isinstance(subtitle_infos, list):
            for item in subtitle_infos:
                if isinstance(item, dict):
                    text = item.get('text') or item.get('content') or ''
                    if text:
                        return str(text).strip()
    return str(data.get('caption') or data.get('subtitle') or '').strip()


def _extract_chapter_text(data):
    parts = []
    chapter_abstract = str(data.get('chapter_abstract') or '').strip()
    if chapter_abstract:
        parts.append(chapter_abstract)
    for chapter in data.get('chapter_list') or []:
        if not isinstance(chapter, dict):
            continue
        desc = str(chapter.get('desc') or '').strip()
        detail = str(chapter.get('detail') or '').strip()
        if desc and detail:
            parts.append(f'{desc}: {detail}')
        elif detail:
            parts.append(detail)
        elif desc:
            parts.append(desc)
    return '\n'.join(dict.fromkeys(parts))


def _extract_mcp_text(payload):
    if not isinstance(payload, dict):
        return ''
    data = payload.get('data') if isinstance(payload.get('data'), dict) else payload
    for key in ('text', 'transcript', 'subtitle', 'content', 'full_text'):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''


def _normalize_video(payload, url, video_id='', platform='douyin', fetch_source='tikhub', extra_text='', fallback_reason=''):
    data = _unwrap_aweme(payload)
    if not data:
        return {
            'platform': platform,
            'video_id': video_id,
            'error': '未找到视频详情',
            'url': url,
            'fetch_source': fetch_source,
        }

    title = data.get('desc') or data.get('title') or data.get('item_title') or ''
    author = ''
    author_info = data.get('author', {})
    if isinstance(author_info, dict):
        author = author_info.get('nickname', '') or author_info.get('unique_id', '')
    elif isinstance(author_info, str):
        author = author_info

    subtitle = _extract_subtitle(data)
    chapter_text = _extract_chapter_text(data)
    transcript = extra_text or ''
    content_parts = [transcript, subtitle, chapter_text, title]
    content = '\n'.join(dict.fromkeys([p for p in content_parts if p]))

    stats = data.get('statistics', {}) or data.get('stats', {})
    video_info = data.get('video', {}) if isinstance(data.get('video', {}), dict) else {}
    duration = video_info.get('duration', '') or data.get('duration', '')

    result = {
        'platform': platform,
        'video_id': _extract_video_id(data, video_id),
        'title': title,
        'author': author,
        'description': title,
        'subtitle': subtitle,
        'transcript': transcript,
        'chapter_text': chapter_text,
        'content': content,
        'text': content,
        'duration': str(duration),
        'likes': _safe_int(stats.get('digg_count', 0) or stats.get('likes', 0)),
        'comments': _safe_int(stats.get('comment_count', 0) or stats.get('comments', 0)),
        'shares': _safe_int(stats.get('share_count', 0) or stats.get('shares', 0)),
        'views': _safe_int(stats.get('play_count', 0) or stats.get('views', 0)),
        'url': url,
        'fetch_source': fetch_source,
    }
    if fallback_reason:
        result['fallback_reason'] = fallback_reason
    return result


def _fetch_douyin_via_mcp(url, video_id=''):
    ok, error = _douyin_mcp_available()
    if not ok:
        return None, error or 'douyin MCP 不可用'

    detail, error = _run_mcporter(
        [
            'call',
            'douyin.parse_douyin_video_info',
            f'share_link={url}',
            '--timeout',
            '60000',
            '--output',
            'json',
        ],
        timeout=75,
    )
    if error:
        return None, error

    text_payload, text_error = _run_mcporter(
        [
            'call',
            'douyin.extract_douyin_text',
            f'share_link={url}',
            '--timeout',
            '120000',
            '--output',
            'json',
        ],
        timeout=135,
    )
    transcript = _extract_mcp_text(text_payload) if text_payload else ''
    result = _normalize_video(
        detail,
        url,
        video_id=video_id,
        platform='douyin',
        fetch_source='douyin-mcp',
        extra_text=transcript,
    )
    if text_error and not transcript:
        result['transcript_error'] = text_error[:300]
    return result, ''


def _fetch_via_tikhub(url, video_id, platform):
    if platform == 'douyin':
        cli = _find_douyin_cli()
        if cli:
            proc = subprocess.run(
                [sys.executable, str(cli), 'video', str(video_id), '--timeout', '120'],
                capture_output=True,
                text=True,
                timeout=150,
            )
            if proc.returncode != 0:
                raise RuntimeError((proc.stderr or proc.stdout or f'退出码 {proc.returncode}')[:300])
            data = _parse_json(proc.stdout)
            if data is None:
                raise RuntimeError((proc.stderr or proc.stdout or '抖音 CLI 返回非 JSON')[:300])
            return _normalize_video(data, url, video_id=video_id, platform=platform, fetch_source='tikhub-douyin-cli')

    if platform == 'tiktok':
        api_path = '/api/v1/tiktok/app_v3/get_video_info'
        params = {'aweme_id': video_id}
    else:
        api_path = '/api/v1/douyin/app_v3/get_video_info'
        params = {'aweme_id': video_id}

    resp = tikhub_fetch(api_path, params=params)
    return _normalize_video(resp, url, video_id=video_id, platform=platform, fetch_source='tikhub')


def _find_douyin_cli():
    """可选外部依赖：一个独立的抖音 CLI 抓取器。未设置 DOUYIN_CLI_PATH 时跳过，直接走 TikHub API。"""
    cli_path = os.environ.get('DOUYIN_CLI_PATH', '')
    if not cli_path:
        return None
    candidate = Path(cli_path)
    return candidate if candidate.exists() else None


def fetch_video(url, video_id=None, platform='douyin'):
    """
    抓取抖音/TikTok 视频信息
    返回标准化结构
    """
    if platform == 'douyin':
        mcp_result, mcp_error = _fetch_douyin_via_mcp(url, video_id or '')
        if mcp_result and not mcp_result.get('error'):
            return mcp_result
    else:
        mcp_error = ''

    if not video_id:
        return {
            'platform': platform,
            'error': f'无法提取 video_id: {url}',
            'url': url,
            'fetch_source': 'none',
            'mcp_error': mcp_error,
        }

    try:
        result = _fetch_via_tikhub(url, video_id, platform)
        if platform == 'douyin' and mcp_error:
            result['fallback_reason'] = f'douyin-mcp unavailable: {mcp_error}'
        return result
    except Exception as e:
        return {
            'platform': platform,
            'video_id': video_id,
            'error': f'API 调用失败: {e}',
            'url': url,
            'fetch_source': 'tikhub',
            'mcp_error': mcp_error,
        }


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print('用法: python3 douyin.py <video_id> [douyin|tiktok]')
        sys.exit(1)
    vid = sys.argv[1]
    plat = sys.argv[2] if len(sys.argv) > 2 else 'douyin'
    result = fetch_video(f'https://www.douyin.com/video/{vid}', vid, plat)
    print(json.dumps(result, ensure_ascii=False, indent=2)[:3000])
