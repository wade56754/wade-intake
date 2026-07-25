#!/usr/bin/env python3
"""
GitHub repo fetcher for intake.

Uses the GitHub CLI as the local first-party fetch path. The CLI handles auth
and public-repo access; this module never reads or prints tokens.
"""

import base64
import json
import shutil
import subprocess
import urllib.parse


def _run_gh(args, timeout=30):
    if not shutil.which('gh'):
        raise RuntimeError('gh CLI 未安装，无法抓取 GitHub 仓库')

    proc = subprocess.run(
        ['gh', *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or '').strip()
        raise RuntimeError(f'gh CLI 失败: {err[:500]}')
    return proc.stdout


def _repo_metadata(owner, repo):
    raw = _run_gh([
        'repo',
        'view',
        f'{owner}/{repo}',
        '--json',
        ','.join([
            'nameWithOwner',
            'description',
            'stargazerCount',
            'forkCount',
            'createdAt',
            'updatedAt',
            'pushedAt',
            'defaultBranchRef',
            'url',
            'licenseInfo',
            'repositoryTopics',
            'primaryLanguage',
        ]),
    ])
    return json.loads(raw)


def _root_files(owner, repo, ref):
    ref_q = urllib.parse.quote(ref or '', safe='')
    endpoint = f'repos/{owner}/{repo}/contents'
    if ref_q:
        endpoint += f'?ref={ref_q}'
    raw = _run_gh(['api', endpoint], timeout=30)
    try:
        files = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return files if isinstance(files, list) else []


def _read_file(owner, repo, path, ref):
    path_q = urllib.parse.quote(path, safe='/')
    ref_q = urllib.parse.quote(ref or '', safe='')
    endpoint = f'repos/{owner}/{repo}/contents/{path_q}'
    if ref_q:
        endpoint += f'?ref={ref_q}'
    raw = _run_gh(['api', endpoint], timeout=30)
    data = json.loads(raw)
    encoded = data.get('content', '')
    if not encoded:
        return ''
    return base64.b64decode(encoded).decode('utf-8', errors='replace')


def _select_docs(files, max_files=6):
    markdown = [
        f for f in files
        if f.get('type') == 'file'
        and f.get('name', '').lower().endswith(('.md', '.mdx'))
    ]

    def priority(item):
        name = item.get('name', '').lower()
        if name == 'readme.md':
            return (0, name)
        if name in ('codex.md', 'tutorial.md', 'guide.md'):
            return (1, name)
        return (2, name)

    markdown.sort(key=priority)
    return markdown[:max_files]


def fetch_repo(owner, repo, source_url='', max_files=6, max_chars_per_file=120000):
    """Fetch a GitHub repo summary plus root markdown docs via gh CLI."""
    if not owner or not repo:
        raise RuntimeError('GitHub URL 缺少 owner/repo')

    meta = _repo_metadata(owner, repo)
    name_with_owner = meta.get('nameWithOwner') or f'{owner}/{repo}'
    branch_obj = meta.get('defaultBranchRef') or {}
    default_branch = branch_obj.get('name') or 'main'
    files = _root_files(owner, repo, default_branch)
    docs = []

    for item in _select_docs(files, max_files=max_files):
        path = item.get('path') or item.get('name')
        if not path:
            continue
        try:
            body = _read_file(owner, repo, path, default_branch)
        except Exception as e:
            docs.append({
                'path': path,
                'content': '',
                'error': str(e),
            })
            continue
        if len(body) > max_chars_per_file:
            body = body[:max_chars_per_file] + '\n\n[truncated]\n'
        docs.append({
            'path': path,
            'content': body,
            'error': '',
        })

    topics = [
        topic.get('name', '')
        for topic in (meta.get('repositoryTopics') or [])
        if topic.get('name')
    ]
    primary_language = (meta.get('primaryLanguage') or {}).get('name', '')
    license_name = (meta.get('licenseInfo') or {}).get('name', '')

    header = [
        f'# {name_with_owner}',
        '',
        meta.get('description') or '',
        '',
        f'- URL: {meta.get("url") or source_url}',
        f'- Stars: {meta.get("stargazerCount", 0)}',
        f'- Forks: {meta.get("forkCount", 0)}',
        f'- Default branch: {default_branch}',
        f'- Language: {primary_language or "unknown"}',
        f'- License: {license_name or "unknown"}',
        f'- Topics: {", ".join(topics) if topics else "none"}',
        f'- Updated: {meta.get("updatedAt", "")}',
        f'- Pushed: {meta.get("pushedAt", "")}',
    ]

    content_parts = ['\n'.join(header).strip()]
    for doc in docs:
        if doc.get('error'):
            content_parts.append(f'## {doc["path"]}\n\n[fetch error] {doc["error"]}')
        else:
            content_parts.append(f'## {doc["path"]}\n\n{doc["content"]}'.strip())

    content = '\n\n'.join(content_parts).strip()
    return {
        'platform': 'github',
        'mode': 'repo',
        'owner': owner,
        'repo': repo,
        'author': owner,
        'title': name_with_owner,
        'description': meta.get('description') or '',
        'content': content,
        'full_text': content,
        'url': meta.get('url') or source_url or f'https://github.com/{owner}/{repo}',
        'stars': meta.get('stargazerCount', 0),
        'forks': meta.get('forkCount', 0),
        'default_branch': default_branch,
        'primary_language': primary_language,
        'topics': topics,
        'docs': docs,
        '_source': 'gh',
    }
