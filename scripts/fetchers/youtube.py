#!/usr/bin/env python3
"""
YouTube 抓取器
- 主路径：baoyu-youtube-transcript（InnerTube API，无需 API Key/浏览器；可选外部依赖，本仓库不含实现）
- fallback 1：YouMind API
- fallback 2：TikHub API
"""

import json
import os
import re
import subprocess
import tempfile
import time
import urllib.request
import urllib.error


# baoyu-youtube-transcript 路径（可选外部依赖，未设置/未安装时自动跳过到 fallback）
BAOYU_SKILL_DIR = os.environ.get('BAOYU_YOUTUBE_TRANSCRIPT_DIR', '')
BAOYU_MAIN_TS = os.path.join(BAOYU_SKILL_DIR, 'scripts', 'main.ts') if BAOYU_SKILL_DIR else ''


# ─── 主路径：baoyu-youtube-transcript (InnerTube API) ───

def _baoyu_fetch_transcript(url):
    """
    用 baoyu-youtube-transcript 提取字幕（InnerTube API，无需任何 Key）
    返回 (title, author, description, transcript_text, chapters)
    """
    if not os.path.exists(BAOYU_MAIN_TS):
        raise RuntimeError(f'baoyu-youtube-transcript 未安装: {BAOYU_MAIN_TS}')

    # 优先中文字幕，其次英文
    # --no-timestamps 让输出更干净，方便提炼
    # --chapters 带章节分割
    cmd = [
        'bun', BAOYU_MAIN_TS,
        url,
        '--languages', 'zh-Hans,zh,en',
        '--chapters',
        '--no-timestamps',
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=60,
    )

    stdout = result.stdout.strip()
    stderr = result.stderr.strip()

    if result.returncode != 0:
        raise RuntimeError(f'baoyu script 失败 (code={result.returncode}): {stderr[:300]}')

    if not stdout:
        raise RuntimeError('baoyu script 无输出')

    # 解析输出的 markdown 文件路径（脚本会打印 "Saved to: xxx.md"）
    saved_path = None
    for line in (stderr + '\n' + stdout).splitlines():
        if 'Saved to:' in line or 'saved to:' in line or line.strip().endswith('.md'):
            parts = line.split(':', 1)
            if len(parts) == 2:
                candidate = parts[1].strip()
                if candidate.endswith('.md') and os.path.exists(candidate):
                    saved_path = candidate
                    break
        # 匹配形如 /path/to/file.md 的行
        stripped = line.strip()
        if stripped.startswith('/') and stripped.endswith('.md') and os.path.exists(stripped):
            saved_path = stripped
            break

    if saved_path and os.path.exists(saved_path):
        content = open(saved_path).read()
    else:
        # 脚本直接把内容输出到 stdout
        content = stdout

    # 从 YAML frontmatter 提取元数据
    title, author, description = '', '', ''
    chapters_text = ''

    fm_match = re.match(r'^---\n(.*?)\n---\n', content, re.DOTALL)
    if fm_match:
        fm = fm_match.group(1)
        for key, attr in [('title', 'title'), ('channel', 'author'), ('description', 'description')]:
            m = re.search(rf'^{key}:\s*["\']?(.*?)["\']?\s*$', fm, re.MULTILINE)
            if m:
                val = m.group(1).strip().strip('"\'')
                if attr == 'title':
                    title = val
                elif attr == 'author':
                    author = val
                elif attr == 'description':
                    description = val
        # 多行 description（YAML block scalar）
        if not description:
            m2 = re.search(r'^description:\s*\|\n((?:\s+.+\n?)+)', fm, re.MULTILINE)
            if m2:
                description = re.sub(r'^\s+', '', m2.group(1), flags=re.MULTILINE).strip()

    # 去掉 frontmatter，保留正文（即字幕内容）
    body = re.sub(r'^---\n.*?\n---\n', '', content, flags=re.DOTALL).strip()

    # 如果 title 还是空，从正文第一个 # 标题取
    if not title:
        m = re.search(r'^# (.+)', body, re.MULTILINE)
        if m:
            title = m.group(1).strip()

    # 正文就是字幕（带章节）
    transcript_text = body

    if not transcript_text:
        raise RuntimeError('baoyu 抓取到的字幕为空')

    return title, author, description, transcript_text


# ─── Fallback 1：YouMind API ───

YOUMIND_BASE = 'https://youmind.com'


def _youmind_api(name, params=None):
    api_key = os.environ.get('YOUMIND_API_KEY', '')
    if not api_key:
        raise RuntimeError('YOUMIND_API_KEY 未设置')

    url = f'{YOUMIND_BASE}/openapi/v1/{name}'
    data = json.dumps(params or {}).encode('utf-8')

    req = urllib.request.Request(
        url,
        data=data,
        headers={
            'x-api-key': api_key,
            'Content-Type': 'application/json',
            'x-use-camel-case': 'true',
            'User-Agent': 'Mozilla/5.0',
        },
        method='POST',
    )

    proxies = {}
    for key in ('HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy'):
        val = os.environ.get(key)
        if val:
            proxies['https'] = val
            proxies['http'] = val
            break

    if proxies:
        proxy_handler = urllib.request.ProxyHandler(proxies)
        opener = urllib.request.build_opener(proxy_handler)
    else:
        opener = urllib.request.build_opener()

    with opener.open(req, timeout=20) as resp:
        raw = resp.read()

    text = raw.decode('utf-8', errors='replace')
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    return json.loads(text)


def _youmind_fetch_transcript(url):
    board = _youmind_api('getDefaultBoard')
    board_id = board['id']

    material = _youmind_api('createMaterialByUrl', {
        'url': url,
        'boardId': board_id,
    })
    material_id = material['id']

    deadline = time.time() + 60
    result = None
    while time.time() < deadline:
        time.sleep(3)
        resp = _youmind_api('getMaterial', {
            'id': material_id,
            'includeBlocks': True,
        })
        if resp.get('type') == 'video':
            result = resp
            break

    if not result:
        raise RuntimeError('YouMind 轮询超时（60s）')

    title = result.get('title', '')
    author = (result.get('urlMeta', {}) or {}).get('channelName', '')
    if not author:
        author = ((result.get('webpage', {}) or {}).get('site', {}) or {}).get('name', '')

    transcript_obj = result.get('transcript')
    if not transcript_obj:
        raise RuntimeError('该视频无字幕')

    contents = transcript_obj.get('contents', [])
    if not contents or contents[0].get('status') != 'completed':
        raise RuntimeError('字幕状态未完成')

    c0 = contents[0]
    plain = c0.get('plain', '')
    if plain and len(plain) > 10:
        transcript_text = plain
    else:
        raw = c0.get('raw', '')
        if raw:
            try:
                raw_data = json.loads(raw) if isinstance(raw, str) else raw
                lines = []
                for item in raw_data:
                    for seg in item.get('content', []):
                        if isinstance(seg, str) and seg.strip():
                            lines.append(seg.strip())
                transcript_text = '\n'.join(lines)
            except Exception:
                transcript_text = str(raw)
        else:
            transcript_text = ''

    if not transcript_text:
        raise RuntimeError('字幕内容为空')

    return title, author, '', transcript_text


# ─── Fallback 2：TikHub API ───

def _tikhub_fetch_transcript(url):
    from fetchers.base import fetch as tikhub_fetch

    video_data = {}
    try:
        resp = tikhub_fetch('/api/v1/youtube/web/get_video_info', params={'url': url})
        video_data = resp.get('data', resp)
    except Exception as e:
        video_data = {'error': str(e)}

    transcript = ''
    try:
        resp = tikhub_fetch('/api/v1/youtube/web/get_video_transcript', params={'url': url})
        td = resp.get('data', resp)

        if isinstance(td, list):
            transcript = '\n'.join(item.get('text', '') for item in td if isinstance(item, dict))
        elif isinstance(td, dict):
            for lang in ['zh-Hans', 'zh', 'zh-CN', 'zh-TW', 'en', 'en-US']:
                if lang in td:
                    sub = td[lang]
                    if isinstance(sub, list):
                        transcript = '\n'.join(item.get('text', '') for item in sub if isinstance(item, dict))
                    elif isinstance(sub, str):
                        transcript = sub
                    break
            if not transcript:
                for lang, sub in td.items():
                    if lang in ('error', 'status', 'message'):
                        continue
                    if isinstance(sub, list):
                        transcript = '\n'.join(item.get('text', '') for item in sub if isinstance(item, dict))
                    elif isinstance(sub, str):
                        transcript = sub
                    if transcript:
                        break
            if not transcript:
                transcript = td.get('transcript', '') or td.get('text', '')
        elif isinstance(td, str):
            transcript = td
    except Exception:
        transcript = ''

    title = video_data.get('title', '') or video_data.get('name', '')
    author = (video_data.get('author', '') or
              (video_data.get('channel', {}).get('name', '') if isinstance(video_data.get('channel'), dict)
               else video_data.get('channel', '')) or '')
    description = video_data.get('description', '') or video_data.get('shortDescription', '')
    views = int(video_data.get('viewCount', 0) or video_data.get('views', 0) or 0)

    return title, author, description, transcript, views


# ─── 主入口 ───

def fetch_video(url, video_id=None):
    """
    抓取 YouTube 视频信息 + 字幕
    优先级：baoyu (InnerTube) → YouMind → TikHub
    """
    # 1. baoyu-youtube-transcript（主路径，InnerTube API，无需 Key）
    try:
        print('[youtube] 使用 baoyu-youtube-transcript (InnerTube API)...')
        title, author, description, transcript = _baoyu_fetch_transcript(url)
        print(f'[youtube] ✅ baoyu 成功，字幕 {len(transcript)} 字')
        return {
            'platform': 'youtube',
            'video_id': video_id or '',
            'title': title,
            'author': author,
            'description': description,
            'transcript': transcript,
            'duration': '',
            'views': 0,
            'url': url,
            'source': 'baoyu-innertube',
        }
    except Exception as e:
        print(f'[youtube] baoyu 失败: {e}，尝试 YouMind...')

    # 2. YouMind API（fallback 1）
    if os.environ.get('YOUMIND_API_KEY'):
        try:
            print('[youtube] 使用 YouMind API...')
            title, author, description, transcript = _youmind_fetch_transcript(url)
            print(f'[youtube] ✅ YouMind 成功，字幕 {len(transcript)} 字')
            return {
                'platform': 'youtube',
                'video_id': video_id or '',
                'title': title,
                'author': author,
                'description': description,
                'transcript': transcript,
                'duration': '',
                'views': 0,
                'url': url,
                'source': 'youmind',
            }
        except Exception as e:
            print(f'[youtube] YouMind 失败: {e}，尝试 TikHub...')

    # 3. TikHub（fallback 2）
    try:
        print('[youtube] 使用 TikHub API...')
        title, author, description, transcript, views = _tikhub_fetch_transcript(url)
        return {
            'platform': 'youtube',
            'video_id': video_id or '',
            'title': title,
            'author': author,
            'description': description,
            'transcript': transcript,
            'duration': '',
            'views': views,
            'url': url,
            'source': 'tikhub',
        }
    except Exception as e:
        return {
            'platform': 'youtube',
            'video_id': video_id or '',
            'title': '',
            'author': '',
            'description': '',
            'transcript': '',
            'url': url,
            'error': f'所有路径均失败（baoyu/YouMind/TikHub）: {e}',
        }


if __name__ == '__main__':
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'
    result = fetch_video(url)
    print(json.dumps(result, ensure_ascii=False, indent=2)[:3000])
