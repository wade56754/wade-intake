#!/usr/bin/env python3
"""
Feishu document fetcher for Wade learning assistant.

Uses lark-cli as the first-party fetch path (replaces the retired OC
web-scraper feishu adapter, 2026-07-24):

- wiki 链接先经 `lark-cli wiki spaces get_node` 解析出 obj_token / obj_type / title。
  必须 bot 身份（--as bot）；user 身份会报 need_user_authorization。
- 正文经 `lark-cli docs +fetch --doc {obj_token} --as bot` 拿 markdown。

lark-cli 自行管理凭证；本模块不读取、不打印任何 token。
"""

import json
import re
import shutil
import subprocess


def _run_lark(args, timeout=60):
    if not shutil.which('lark-cli'):
        raise RuntimeError('lark-cli 未安装，无法抓取飞书文档')

    proc = subprocess.run(
        ['lark-cli', *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or '').strip()
        raise RuntimeError(f'lark-cli 失败: {err[:500]}')
    return proc.stdout


def _parse_url(url):
    """从飞书 URL 提取 (doc_kind, token)。识别不了返回 (None, None)。"""
    m = re.search(r'/(wiki|docx|docs|sheets|base|bitable)/([A-Za-z0-9]+)', url)
    if not m:
        return None, None
    return m.group(1), m.group(2)


def _resolve_wiki_node(node_token):
    """wiki node_token → (obj_token, obj_type, title)。"""
    raw = _run_lark([
        'wiki', 'spaces', 'get_node',
        '--params', json.dumps({'token': node_token}),
        '--as', 'bot',
    ])
    body = json.loads(raw)
    if body.get('code') != 0:
        raise RuntimeError(
            f'wiki get_node 失败: code={body.get("code")} msg={str(body.get("msg", ""))[:200]}'
        )
    node = (body.get('data') or {}).get('node') or {}
    obj_token = node.get('obj_token')
    if not obj_token:
        raise RuntimeError('wiki get_node 响应缺少 obj_token')
    return obj_token, node.get('obj_type', ''), node.get('title', '')


def _fetch_doc_markdown(obj_token):
    raw = _run_lark(['docs', '+fetch', '--doc', obj_token, '--as', 'bot'])
    body = json.loads(raw)
    if not body.get('ok'):
        raise RuntimeError(f'docs +fetch 失败: {json.dumps(body, ensure_ascii=False)[:300]}')
    markdown = (body.get('data') or {}).get('markdown', '')
    if not markdown.strip():
        raise RuntimeError('docs +fetch 返回空 markdown')
    return markdown


def _title_from_markdown(markdown):
    for line in markdown.splitlines():
        line = line.strip().lstrip('#').strip()
        if line:
            return line[:50]
    return ''


def fetch_document(url, doc_id=None):
    """抓取飞书文档，返回学习助手标准 dict。

    wiki 链接两步走（get_node → docs +fetch），docx 直链一步走。
    sheets / bitable 等非文档对象明确报错，不静默返回空内容。
    """
    doc_kind, token = _parse_url(url)
    token = doc_id or token
    if not token:
        raise RuntimeError(f'无法从飞书 URL 提取 token: {url}')

    title = ''
    if doc_kind == 'wiki':
        obj_token, obj_type, title = _resolve_wiki_node(token)
    else:
        obj_token, obj_type = token, (doc_kind or 'docx')

    if obj_type not in ('docx', 'doc', 'docs'):
        raise RuntimeError(f'暂不支持的飞书对象类型: {obj_type}（当前仅支持文档正文抓取）')

    markdown = _fetch_doc_markdown(obj_token)

    return {
        'platform': 'feishu',
        'mode': 'document',
        'url': url,
        'title': title or _title_from_markdown(markdown) or '飞书文档',
        'author': '',
        'content': markdown,
        'full_text': markdown,
        'doc_token': obj_token,
        'node_token': token if doc_kind == 'wiki' else '',
        'obj_type': obj_type,
        '_source': 'lark-cli',
    }
