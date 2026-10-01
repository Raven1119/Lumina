"""Read-only, content-free B2 trial counts from Nervous's local SQLite database."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path
import sqlite3


def summarize(path: Path, since: date, show_errors: bool = False) -> str:
    uri=path.resolve().as_uri()+'?mode=ro'
    with sqlite3.connect(uri,uri=True) as conn:
        conn.execute('PRAGMA query_only=ON')
        rows=conn.execute('SELECT body,digest FROM usage_records WHERE at>=? ORDER BY at',
                          (since.isoformat(),)).fetchall()
    totals=Counter();error_texts=Counter()
    for raw,digest in rows:
        if hashlib.sha256(raw.encode()).hexdigest()!=digest:
            raise ValueError('usage_integrity_failure')
        item=json.loads(raw);kind=item.get('type','unknown')
        totals['记录·'+kind]+=1
        for field in ('steps','parse_failures','retries','protocol_residue','speech_count',
                      'duplicate_speech','no_speech','ignored_last_tool_calls',
                      'model_failures','decision_calls','guard_triggers','auto_replies'):
            totals[field]+=int(item.get(field,0))
        if kind=='helper':
            totals['帮手结果·'+item.get('result','未说明')]+=1
        for name,count in item.get('tools',{}).items():totals['工具·'+name]+=int(count)
        for name,count in item.get('tool_errors',{}).items():totals['工具错误·'+name]+=int(count)
        for role,count in item.get('calls',{}).items():totals['模型调用·'+role]+=int(count)
        for role,usage in item.get('tokens',{}).items():
            totals['输入 token·'+role]+=int(usage.get('input',0))
            totals['输出 token·'+role]+=int(usage.get('output',0))
        if show_errors:
            error_texts.update(item.get('tool_error_texts',[]))
    lines=['| 指标 | 数量 |','| --- | ---: |']
    lines.extend(f'| {name} | {value} |' for name,value in sorted(totals.items()))
    if show_errors:
        lines.extend(['','| 工具错误文字 | 次数 |','| --- | ---: |'])
        lines.extend(f'| {text.replace("|","\\|").replace(chr(10)," ")} | {count} |'
                     for text,count in sorted(error_texts.items()))
    return '\n'.join(lines)


def main():
    parser=argparse.ArgumentParser(description='只读汇总 Lumina 试用计数')
    parser.add_argument('--since',required=True,type=date.fromisoformat,
                        help='YYYY-MM-DD，包含这一天')
    parser.add_argument('--db',type=Path,default=Path('data/nervous/lumina.sqlite'))
    parser.add_argument('--errors',action='store_true',help='列出工具错误文字')
    args=parser.parse_args()
    print(summarize(args.db,args.since,args.errors))


if __name__=='__main__':main()
