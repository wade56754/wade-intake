#!/usr/bin/env python3
"""
本地失败日志模块。

把抓取/分析链路里的失败事件追加写入 logs/failures.jsonl，方便排障。
接口：log(...) / read_recent(...).
"""

import json
import os
import traceback
from datetime import datetime, timedelta, timezone


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
LOGS_DIR = os.path.join(SKILL_DIR, 'logs')
FAILURES_FILE = os.path.join(LOGS_DIR, 'failures.jsonl')
TZ_CST = timezone(timedelta(hours=8))


def log(url='', platform='', step='', error='', exc=None, input_params=None):
    os.makedirs(LOGS_DIR, exist_ok=True)
    record = {
        'ts': datetime.now(TZ_CST).isoformat(),
        'url': url or '',
        'platform': platform or 'unknown',
        'step': step or '',
        'error': str(error or ''),
        'traceback': traceback.format_exc() if exc else '',
        'input_params': input_params or {},
    }
    with open(FAILURES_FILE, 'a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')
    print('[failure_logger] 已记录失败: step=%s platform=%s error=%s' % (
        record['step'],
        record['platform'],
        record['error'][:80],
    ))


def read_recent(n=30):
    if not os.path.exists(FAILURES_FILE):
        return []
    records = []
    with open(FAILURES_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records[-n:]
