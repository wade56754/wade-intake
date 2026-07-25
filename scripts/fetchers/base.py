#!/usr/bin/env python3
"""
TikHub API 基础封装
- 自动读取 TIKHUB_API_KEY（环境变量 → 本工具 .env/credentials）
- fetch(path, params) → JSON
- 失败自动重试一次
"""

import http.client
import json
import os
import urllib.parse
import ssl
from pathlib import Path

# TikHub API 配置
TIKHUB_HOST = 'api.tikhub.io'
SCRIPT_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SCRIPT_DIR.parent


def _parse_env_file(path):
    result = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def _load_api_key():
    """读取 TIKHUB_API_KEY；不打印、不返回任何凭证日志。"""
    key = os.environ.get('TIKHUB_API_KEY')
    if key:
        return key.strip()

    # 优先读取当前工具目录的 .env 与 credentials。
    for env_file in (PROJECT_ROOT / '.env', SCRIPT_DIR / '.env'):
        value = _parse_env_file(env_file).get('TIKHUB_API_KEY')
        if value:
            return value.strip()

    credentials_dir = PROJECT_ROOT / 'credentials'
    key_file = credentials_dir / 'tikhub.key'
    if key_file.exists():
        content = key_file.read_text(encoding='utf-8').strip()
        if content:
            return content.splitlines()[0].strip()

    raise RuntimeError(
        '未找到 TIKHUB_API_KEY，请配置环境变量，或在当前工具 .env / credentials/tikhub.key 中配置'
    )


# 模块级缓存
_API_KEY = None


def _get_api_key():
    """获取 API Key（带缓存）"""
    global _API_KEY
    if _API_KEY is None:
        _API_KEY = _load_api_key()
    return _API_KEY


def fetch(path, params=None, method='GET', body=None, max_retries=1):
    """
    调用 TikHub API
    
    参数:
        path: API 路径，如 '/api/v1/wechat_mp/web/...'
        params: GET 查询参数字典
        method: HTTP 方法
        body: POST 请求体（dict，会被 JSON 序列化）
        max_retries: 最大重试次数（默认1次，即总共尝试2次）
    
    返回:
        解析后的 JSON 字典
    """
    api_key = _get_api_key()
    
    # 构建完整路径
    if params:
        query = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        full_path = f'{path}?{query}'
    else:
        full_path = path
    
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Accept': 'application/json',
        'User-Agent': 'WadeLearningAssistant/1.0',
    }
    
    if body:
        headers['Content-Type'] = 'application/json'
    
    last_error = None
    
    for attempt in range(max_retries + 1):
        conn = None
        try:
            # 创建 HTTPS 连接
            context = ssl.create_default_context()
            conn = http.client.HTTPSConnection(TIKHUB_HOST, timeout=30, context=context)
            
            request_body = json.dumps(body).encode('utf-8') if body else None
            conn.request(method, full_path, body=request_body, headers=headers)
            
            resp = conn.getresponse()
            data = resp.read().decode('utf-8')
            
            if resp.status == 200:
                return json.loads(data)
            
            # 非200状态码，记录错误
            last_error = f'HTTP {resp.status}: {data[:500]}'
            
            # 4xx 客户端错误不重试（除了 429）
            if 400 <= resp.status < 500 and resp.status != 429:
                break
            
        except Exception as e:
            last_error = str(e)
        finally:
            if conn:
                conn.close()
    
    # 所有重试都失败
    raise RuntimeError(f'TikHub API 调用失败 [{path}]: {last_error}')


if __name__ == '__main__':
    # 验证 API Key 加载
    key = _get_api_key()
    print(f'API Key 加载成功: {key[:10]}...')
