#!/usr/bin/env python3
# 상태 표시줄용: 이 PC의 5시간 창·주간 창·오늘 토큰 합계 (입력+출력+캐시 쓰기+캐시 읽기, 데일리 써머리와 같은 기준)
# 사용: statusline_tokens.py <5시간 리셋 epoch> <주간 리셋 epoch>  → "5시간|주간|오늘"
import glob, json, os, sys, time
from datetime import datetime

now = time.time()

def start(arg, span):
    # 리셋 시각에서 창 길이를 빼면 창 시작. 값이 없거나 이미 지난 리셋이면 지금 기준으로 굴림
    try:
        r = float(arg)
        if r > 1e11:
            r /= 1000
        if r > now:
            return r - span
    except (ValueError, IndexError):
        pass
    return now - span

h5s = start(sys.argv[1] if len(sys.argv) > 1 else "", 5 * 3600)
d7s = start(sys.argv[2] if len(sys.argv) > 2 else "", 7 * 86400)
today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
oldest = min(h5s, d7s, today)

msgs = {}
for path in glob.glob(os.path.expanduser("~/.claude/projects/**/*.jsonl"), recursive=True):
    try:
        if os.path.getmtime(path) < oldest:
            continue
        fh = open(path, encoding="utf-8")
    except OSError:
        continue
    with fh:
        for line in fh:
            if '"usage"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            m = d.get("message") or {}
            u = m.get("usage")
            if d.get("type") != "assistant" or not u or not d.get("timestamp") or m.get("model") == "<synthetic>":
                continue
            try:
                ts = datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            if ts < oldest:
                continue
            key = m.get("id") or d.get("requestId") or d.get("uuid")
            msgs[key] = (ts, sum(u.get(k) or 0 for k in (
                "input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")))

tot = lambda s: sum(n for ts, n in msgs.values() if ts >= s)
print(f"{tot(h5s):,}|{tot(d7s):,}|{tot(today):,}")
