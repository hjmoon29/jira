#!/usr/bin/env python3
"""(IoT업무봇 사본 — 원본: 데일리 써머리 폴더의 claude_daily.py)
클로드 데일리 써머리: Claude Code 세션 기록에서 날짜별(KST) 토큰 사용량을 집계한다.

여러 PC의 기록은 화면(artifact)의 공유 DB 컬렉션 "usage"에 PC별·날짜별 문서로 모은다.

  python3 claude_daily.py export [--machine 이름]        # export/<이름>/ 에 날짜별 문서 JSON 생성 (DB 업로드용)
  python3 claude_daily.py show [YYYY-MM-DD]              # 이 PC의 하루 요약 출력 (기본: 오늘)
  python3 claude_daily.py send [YYYY-MM-DD] [--from 폴더] # 하루 요약을 Gmail로 발송 (수동, 기본: 어제)
                                                        #   YYYY-MM-DD..YYYY-MM-DD 로 주면 기간 요약(일별 표 + 일별 상세)
                                                        #   all 로 주면 데이터가 있는 모든 날짜의 요약
                                                        #   --from: DB에서 받은 문서 폴더를 주면 모든 PC 합산으로 발송

PC 이름 기본값: 환경변수 CLAUDE_DAILY_MACHINE, 없으면 mac / windows / linux.

메일 발송 설정은 ~/.config/claude-daily/gmail.json (권한 600):
  {"client_id": "...", "client_secret": "...", "refresh_token": "...", "to": "you@example.com"}
"""
import base64
import glob
import json
import os
import platform
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

KST = timezone(timedelta(hours=9))
PROJECTS = os.path.expanduser("~/.claude/projects")
HERE = os.path.dirname(os.path.abspath(__file__))
GMAIL_CONF = os.path.expanduser("~/.config/claude-daily/gmail.json")
# 상태 표시줄이 남기는 계정 사용률 기록 (모든 PC·웹 합산 한도의 %)
SNAPSHOTS = os.path.expanduser("~/.claude/usage-snapshots.jsonl")
# Windows 상태 표시줄은 최신 값 하나만 남긴다
RATE_CACHE = os.path.expanduser("~/.claude/rate-limits-cache.json")
WEEK_SEC = 7 * 86400
KEYS = ("input", "output", "cache_write", "cache_read")


def collect():
    # 스트리밍 중 같은 응답이 여러 줄로 기록되므로 메시지 id로 중복 제거
    msgs = {}
    for path in glob.glob(os.path.join(PROJECTS, "**", "*.jsonl"), recursive=True):
        try:
            fh = open(path, encoding="utf-8")
        except OSError:
            continue
        with fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                sid = d.get("sessionId")
                m = d.get("message") or {}
                u = m.get("usage")
                if d.get("type") != "assistant" or not u or not d.get("timestamp"):
                    continue
                if m.get("model") == "<synthetic>":
                    continue
                key = m.get("id") or d.get("requestId") or d.get("uuid")
                msgs[key] = {
                    "ts": d["timestamp"],
                    "sid": sid,
                    "sub": bool(d.get("isSidechain")),
                    "model": m.get("model") or "unknown",
                    "input": u.get("input_tokens") or 0,
                    "output": u.get("output_tokens") or 0,
                    "cache_write": u.get("cache_creation_input_tokens") or 0,
                    "cache_read": u.get("cache_read_input_tokens") or 0,
                }
    return msgs


def aggregate():
    msgs = collect()
    # 세션 제목은 프롬프트 원문이 아니라 날짜·세션별로 정리한 작업 요약 (summaries.json, 키 "YYYY-MM-DD|세션id 앞 8자")
    try:
        summaries = json.load(open(os.path.join(HERE, "summaries.json"), encoding="utf-8"))
    except (OSError, ValueError):
        summaries = {}
    days = defaultdict(lambda: {
        "tot": dict.fromkeys(KEYS, 0), "calls": 0, "sub_calls": 0,
        "models": defaultdict(lambda: dict.fromkeys(KEYS, 0)),
        "hours": [0] * 24, "sessions": defaultdict(lambda: {"tokens": 0, "calls": 0, "first": None, "last": None}),
    })
    for r in msgs.values():
        t = datetime.fromisoformat(r["ts"].replace("Z", "+00:00")).astimezone(KST)
        day = days[t.strftime("%Y-%m-%d")]
        total = sum(r[k] for k in KEYS)
        for k in KEYS:
            day["tot"][k] += r[k]
            day["models"][r["model"]][k] += r[k]
        day["calls"] += 1
        day["sub_calls"] += r["sub"]
        day["hours"][t.hour] += total
        s = day["sessions"][r["sid"]]
        s["tokens"] += total
        s["calls"] += 1
        hm = t.strftime("%H:%M")
        s["first"] = min(s["first"] or hm, hm)
        s["last"] = max(s["last"] or hm, hm)
    out = []
    for date in sorted(days):
        d = days[date]
        sessions = sorted(
            ({"id": sid[:8], "title": summaries.get(f"{date}|{sid[:8]}", "(요약 전)"), **v} for sid, v in d["sessions"].items()),
            key=lambda x: -x["tokens"],
        )
        out.append({
            "date": date,
            "total": sum(d["tot"].values()),
            **d["tot"],
            "calls": d["calls"],
            "sub_calls": d["sub_calls"],
            "models": {k: {**v, "total": sum(v.values())} for k, v in d["models"].items()},
            "hours": d["hours"],
            "sessions": sessions,
        })
    return out


def fmt(n):
    return f"{n:,}"


def default_machine():
    if os.environ.get("CLAUDE_DAILY_MACHINE"):
        return os.environ["CLAUDE_DAILY_MACHINE"]
    return {"Darwin": "mac", "Windows": "windows"}.get(platform.system(), "linux")


def empty_day(date):
    return {"date": date, "total": 0, **dict.fromkeys(KEYS, 0), "calls": 0, "sub_calls": 0,
            "models": {}, "hours": [0] * 24, "sessions": []}


def merge(docs, date):
    # 같은 날짜의 PC별 문서를 하나로 합친다 (세션에는 PC 이름을 붙인다)
    out = empty_day(date)
    machines = []
    for d in docs:
        if d.get("date") != date:
            continue
        machines.append(d.get("machine", "?"))
        for k in KEYS + ("total", "calls", "sub_calls"):
            out[k] += d.get(k, 0)
        out["hours"] = [a + b for a, b in zip(out["hours"], d.get("hours", [0] * 24))]
        for m, v in d.get("models", {}).items():
            cur = out["models"].setdefault(m, dict.fromkeys(KEYS + ("total",), 0))
            for k in cur:
                cur[k] += v.get(k, 0)
        out["sessions"] += [{**x, "machine": d.get("machine", "?")} for x in d.get("sessions", [])]
    out["sessions"].sort(key=lambda x: -x["tokens"])
    out["machines"] = sorted(machines)
    return out


def day_text(day):
    lines = [
        f"클로드 데일리 써머리 · {day['date']}" + (f" · {', '.join(day['machines'])}" if day.get("machines") else ""),
        "",
        f"총 토큰      {fmt(day['total'])}",
        f"  입력        {fmt(day['input'])}",
        f"  출력        {fmt(day['output'])}",
        f"  캐시 쓰기   {fmt(day['cache_write'])}",
        f"  캐시 읽기   {fmt(day['cache_read'])}",
        f"API 호출     {fmt(day['calls'])}회 (서브에이전트 {fmt(day['sub_calls'])}회)",
        f"세션         {len(day['sessions'])}개",
        "",
        "모델별",
    ]
    for name, v in sorted(day["models"].items(), key=lambda x: -x[1]["total"]):
        lines.append(f"  {name:<28} {fmt(v['total'])}")
    lines += ["", "세션별 작업 (토큰 많은 순)"]
    for s in day["sessions"]:
        tag = f"[{s['machine']}] " if s.get("machine") else ""
        lines.append(f"  {s['first']}–{s['last']}  {fmt(s['tokens']):>12}  {tag}{s['title']}")
    return "\n".join(lines)


def range_text(days):
    # 기간 요약: 일별 표, 합계, 사용한 날의 상세
    keys = ("total",) + KEYS
    tot = {k: sum(d[k] for d in days) for k in keys + ("calls",)}
    machines = sorted({m for d in days for m in d.get("machines", [])})
    row = "{:<12}{:>14}{:>10}{:>10}{:>13}{:>15}{:>7}{:>5}"
    lines = [
        f"클로드 데일리 써머리 · {days[0]['date']} ~ {days[-1]['date']}" + (f" · {', '.join(machines)}" if machines else ""),
        "",
        row.format("날짜", "총 토큰", "입력", "출력", "캐시 쓰기", "캐시 읽기", "호출", "세션"),
    ]
    for d in days:
        lines.append(row.format(d["date"], *(fmt(d[k]) for k in keys), fmt(d["calls"]), len(d["sessions"])))
    lines.append(row.format("합계", *(fmt(tot[k]) for k in keys), fmt(tot["calls"]),
                            sum(len(d["sessions"]) for d in days)))
    active = [d for d in days if d["total"]]
    if active:
        lines.append(row.format("일 평균*", *(fmt(round(tot[k] / len(active))) for k in keys),
                                fmt(round(tot["calls"] / len(active))), ""))
        lines.append(f"* 사용한 날 {len(active)}일 기준. 기간 전체 {len(days)}일 기준 일 평균 총 토큰 "
                     f"{fmt(round(tot['total'] / len(days)))}")
    for d in days:
        if d["total"]:
            lines += ["", "-" * 40, day_text(d)]
    return "\n".join(lines)


# ---- HTML 메일 (메일 앱 호환을 위해 표 + 인라인 스타일만 사용) ----
C = {"ink": "#1f2328", "sub": "#656d76", "line": "#e6e8eb", "bg": "#f5f6f8", "card": "#ffffff",
     "accent": "#4f46e5", "zero": "#c4c9cf"}
PARTS = (("cache_read", "캐시 읽기", "#a5b4fc"), ("cache_write", "캐시 쓰기", "#6366f1"),
         ("output", "출력", "#f59e0b"), ("input", "입력", "#10b981"))
WEEK = "월화수목금토일"


def esc(t):
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def wd(date):
    return WEEK[datetime.strptime(date, "%Y-%m-%d").weekday()]


def h_tile(label, value, note=""):
    return (f'<td style="padding:6px;width:25%;vertical-align:top">'
            f'<div style="background:{C["card"]};border:1px solid {C["line"]};border-radius:10px;padding:14px 14px 12px">'
            f'<div style="font-size:12px;color:{C["sub"]}">{label}</div>'
            f'<div style="font-size:20px;font-weight:700;color:{C["ink"]};margin-top:4px;white-space:nowrap">{value}</div>'
            f'<div style="font-size:11px;color:{C["sub"]};margin-top:2px">{note}&nbsp;</div></div></td>')


def h_stack(d, width_pct):
    # 그날 토큰 구성(캐시 읽기/쓰기/출력/입력)을 한 줄 막대로, 길이는 기간 최대값 대비
    if not d["total"]:
        return ""
    cells = "".join(
        f'<td style="background:{col};height:10px;width:{max(d[k] / d["total"] * 100, 0.6 if d[k] else 0):.2f}%"></td>'
        for k, _, col in PARTS if d[k])
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" style="width:{max(width_pct, 2):.1f}%;'
            f'border-collapse:collapse;border-radius:5px;overflow:hidden"><tr>{cells}</tr></table>')


def h_legend():
    return "".join(f'<span style="display:inline-block;margin-right:12px;font-size:11px;color:{C["sub"]}">'
                   f'<span style="display:inline-block;width:9px;height:9px;border-radius:2px;background:{col};'
                   f'margin-right:4px;vertical-align:-1px"></span>{name}</span>' for _, name, col in PARTS)


def h_hours(hours):
    top = max(hours) or 1
    cells = ""
    for h, v in enumerate(hours):
        a = 0 if not v else 0.2 + 0.8 * v / top
        bg = f"rgba(79,70,229,{a:.2f})" if v else C["bg"]
        cells += f'<td title="{h}시 {fmt(v)}" style="height:14px;background:{bg};border:1px solid #fff"></td>'
    labels = "".join(f'<td style="font-size:9px;color:{C["sub"]};text-align:left">{h if h % 6 == 0 else ""}</td>'
                     for h in range(24))
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;'
            f'table-layout:fixed"><tr>{cells}</tr><tr>{labels}</tr></table>')


def h_day_card(d):
    rows = "".join(
        f'<tr><td style="padding:3px 0;font-size:13px;color:{C["sub"]}">'
        f'<span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:{col};margin-right:6px"></span>{name}</td>'
        f'<td style="padding:3px 0;font-size:13px;color:{C["ink"]};text-align:right">{fmt(d[k])}</td>'
        f'<td style="padding:3px 0 3px 10px;font-size:12px;color:{C["sub"]};text-align:right;width:52px">'
        f'{d[k] / d["total"] * 100:.1f}%</td></tr>' for k, name, col in PARTS)
    models = "".join(
        f'<tr><td style="padding:3px 0;font-size:13px;color:{C["ink"]}">{esc(m)}</td>'
        f'<td style="padding:3px 0;font-size:13px;color:{C["ink"]};text-align:right">{fmt(v["total"])}</td></tr>'
        for m, v in sorted(d["models"].items(), key=lambda x: -x[1]["total"]))
    sessions = "".join(
        f'<tr><td style="padding:8px 0;border-top:1px solid {C["line"]};vertical-align:top;width:92px;'
        f'font-size:12px;color:{C["sub"]};white-space:nowrap">{s["first"]}–{s["last"]}</td>'
        f'<td style="padding:8px 8px;border-top:1px solid {C["line"]};font-size:13px;color:{C["ink"]}">'
        + (f'<span style="font-size:11px;color:{C["accent"]};background:#eef2ff;border-radius:4px;padding:1px 5px;'
           f'margin-right:6px">{esc(s["machine"])}</span>' if s.get("machine") else "")
        + f'{esc(s["title"])}</td>'
        f'<td style="padding:8px 0;border-top:1px solid {C["line"]};font-size:13px;color:{C["ink"]};'
        f'text-align:right;white-space:nowrap;vertical-align:top">{fmt(s["tokens"])}</td></tr>'
        for s in d["sessions"])
    machines = f' · {esc(", ".join(d["machines"]))}' if d.get("machines") else ""
    sec = f'font-size:12px;font-weight:700;color:{C["sub"]};margin:18px 0 6px;letter-spacing:.3px'
    return f"""
<div style="background:{C["card"]};border:1px solid {C["line"]};border-radius:12px;padding:20px;margin-top:14px">
  <table role="presentation" cellpadding="0" cellspacing="0" style="width:100%"><tr>
    <td style="font-size:16px;font-weight:700;color:{C["ink"]}">{d["date"]} ({wd(d["date"])})<span style="font-weight:400;color:{C["sub"]};font-size:13px">{machines}</span></td>
    <td style="text-align:right;font-size:18px;font-weight:700;color:{C["accent"]};white-space:nowrap">{fmt(d["total"])}</td>
  </tr><tr><td colspan="2" style="text-align:right;font-size:12px;color:{C["sub"]}">
    API 호출 {fmt(d["calls"])}회 (서브에이전트 {fmt(d["sub_calls"])}회) · 세션 {len(d["sessions"])}개</td></tr></table>
  <div style="margin-top:12px">{h_stack(d, 100)}</div>
  <div style="{sec}">토큰 구성</div>
  <table role="presentation" cellpadding="0" cellspacing="0" style="width:100%">{rows}</table>
  <div style="{sec}">시간대별 사용 (0–23시)</div>{h_hours(d.get("hours", [0] * 24))}
  <div style="{sec}">모델별</div>
  <table role="presentation" cellpadding="0" cellspacing="0" style="width:100%">{models}</table>
  <div style="{sec}">세션별 작업 (토큰 많은 순)</div>
  <table role="presentation" cellpadding="0" cellspacing="0" style="width:100%">{sessions}</table>
</div>"""


def h_gauge(pct, color, height=8):
    pct = max(0, min(pct or 0, 100))
    fill = (f'<td style="background:{color};height:{height}px;width:{pct}%"></td>' if pct else "")
    rest = (f'<td style="background:{C["line"]};height:{height}px"></td>' if pct < 100 else "")
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;'
            f'border-radius:{height // 2}px;overflow:hidden"><tr>{fill}{rest}</tr></table>')


def level(p):
    return "#dc2626" if (p or 0) >= 80 else "#f59e0b" if (p or 0) >= 50 else C["accent"]


def h_account(snaps, dates):
    rows = account_days(snaps, dates)
    if not rows:
        return ""
    last = snaps[-1]

    def now_box(label, pct, reset):
        return (f'<td style="padding:6px;width:50%;vertical-align:top"><div style="background:{C["bg"]};border-radius:10px;padding:12px 14px">'
                f'<table role="presentation" cellpadding="0" cellspacing="0" style="width:100%"><tr>'
                f'<td style="font-size:12px;color:{C["sub"]}">{label}</td>'
                f'<td style="font-size:20px;font-weight:700;color:{level(pct)};text-align:right">{pct}%</td></tr></table>'
                f'<div style="margin:6px 0">{h_gauge(pct, level(pct))}</div>'
                f'<div style="font-size:11px;color:{C["sub"]}">초기화 {reset}</div></div></td>')

    head = ("<tr>" + now_box("5시간 한도", last.get("h5"), kst(last["h5r"]) if last.get("h5r") else "-")
            + now_box("주간 한도", last.get("d7"), kst(last["d7r"]) if last.get("d7r") else "-") + "</tr>")
    th = f'padding:6px 0;font-size:11px;color:{C["sub"]};font-weight:400;text-align:left'
    body = ""
    for a in rows:
        td = f'padding:10px 0;border-top:1px solid {C["line"]};font-size:13px;vertical-align:middle'
        body += (f'<tr><td style="{td};white-space:nowrap">{a["date"][5:]} ({wd(a["date"])})'
                 f'<div style="font-size:11px;color:{C["sub"]}">{a["first"]}–{a["last"]}</div></td>'
                 f'<td style="{td};padding-left:10px;width:30%">{h_gauge(a["h5_max"], level(a["h5_max"]), 6)}'
                 f'<div style="font-size:12px;margin-top:3px;font-weight:700;color:{level(a["h5_max"])}">{a["h5_max"]}%</div></td>'
                 f'<td style="{td};padding-left:14px;width:36%">{h_gauge(a["d7_end"], level(a["d7_end"]), 6)}'
                 f'<div style="font-size:12px;margin-top:3px"><span style="color:{C["sub"]}">{a["d7_start"]}% → </span>'
                 f'<b style="color:{level(a["d7_end"])}">{a["d7_end"]}%</b></div></td>'
                 f'<td style="{td};padding-left:10px;text-align:right;font-size:11px;color:{C["sub"]};white-space:nowrap">'
                 f'{kst(a["d7r"]) if a.get("d7r") else "-"}</td></tr>')
    return f"""
<div style="background:{C["card"]};border:1px solid {C["line"]};border-radius:12px;padding:20px;margin-top:14px">
  <div style="font-size:15px;font-weight:700">계정 전체 사용률</div>
  <div style="font-size:12px;color:{C["sub"]};margin-top:3px">모든 기기·웹 사용을 합친 구독 한도 대비 % · {kst(last["t"])} 기준</div>
  <table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;margin:10px -6px 4px;border-collapse:separate">{head}</table>
  <table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;margin-top:8px">
    <tr><th style="{th}">날짜 · 기록 시간</th><th style="{th};padding-left:10px">5시간 한도 최고</th>
    <th style="{th};padding-left:14px">주간 한도 (그날 시작 → 끝)</th><th style="{th};text-align:right">주간 초기화</th></tr>{body}
  </table>
  <div style="font-size:11px;color:{C["sub"]};margin-top:10px;line-height:1.6">계정 토큰 수는 제공되지 않아 %로만 표시합니다. 상태 표시줄이 있는 PC에서 Claude Code를 쓰는 동안 기록된 날만 나옵니다.</div>
</div>"""


def h_page(title, subtitle, inner):
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:{C["bg"]}">
<div style="max-width:680px;margin:0 auto;padding:24px 14px;font-family:-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Malgun Gothic','Segoe UI',sans-serif;color:{C["ink"]}">
  <div style="font-size:12px;font-weight:700;color:{C["accent"]};letter-spacing:.5px">CLAUDE DAILY SUMMARY</div>
  <div style="font-size:22px;font-weight:700;margin-top:4px">{title}</div>
  <div style="font-size:13px;color:{C["sub"]};margin-top:4px">{subtitle}</div>
  {inner}
  <div style="font-size:11px;color:{C["sub"]};margin-top:20px;line-height:1.6">
    토큰은 입력·출력·캐시 쓰기·캐시 읽기의 합계입니다. 시간은 한국 시간(KST) 기준입니다.
  </div>
</div></body></html>"""


def range_html(days, span=None, snaps=(), acct_dates=None):
    tot = {k: sum(d[k] for d in days) for k in ("total", "calls") + KEYS}
    active = [d for d in days if d["total"]]
    sessions = sum(len(d["sessions"]) for d in days)
    machines = sorted({m for d in days for m in d.get("machines", [])})
    top = max((d["total"] for d in days), default=0) or 1
    tiles = "".join([
        h_tile("총 토큰", fmt(tot["total"]), f'캐시 읽기 {tot["cache_read"] / (tot["total"] or 1) * 100:.0f}%'),
        h_tile("일 평균", fmt(round(tot["total"] / (len(active) or 1))),
               f'사용일 기준 · 전체 {span or len(days)}일 평균 {fmt(round(tot["total"] / (span or len(days) or 1)))}'),
        h_tile("API 호출", f'{fmt(tot["calls"])}회', f'세션 {sessions}개'),
        h_tile("사용한 날", f"{len(active)}일", f"기간 {span or len(days)}일 중"),
    ])
    rows = ""
    for d in days:
        zero = not d["total"]
        color = C["zero"] if zero else C["ink"]
        rows += (f'<tr><td style="padding:9px 0;border-top:1px solid {C["line"]};font-size:13px;color:{color};'
                 f'white-space:nowrap;width:96px">{d["date"][5:]} ({wd(d["date"])})</td>'
                 f'<td style="padding:9px 10px;border-top:1px solid {C["line"]}">{h_stack(d, d["total"] / top * 100)}</td>'
                 f'<td style="padding:9px 0;border-top:1px solid {C["line"]};font-size:13px;font-weight:{400 if zero else 700};'
                 f'color:{color};text-align:right;white-space:nowrap;width:110px">{"—" if zero else fmt(d["total"])}</td>'
                 f'<td style="padding:9px 0 9px 10px;border-top:1px solid {C["line"]};font-size:12px;color:{C["sub"] if not zero else C["zero"]};'
                 f'text-align:right;white-space:nowrap;width:70px">{"" if zero else str(d["calls"]) + "회"}</td></tr>')
    rows += (f'<tr><td style="padding:10px 0;border-top:2px solid {C["ink"]};font-size:13px;font-weight:700">합계</td>'
             f'<td style="border-top:2px solid {C["ink"]}"></td>'
             f'<td style="padding:10px 0;border-top:2px solid {C["ink"]};font-size:14px;font-weight:700;text-align:right;'
             f'color:{C["accent"]};white-space:nowrap">{fmt(tot["total"])}</td>'
             f'<td style="padding:10px 0 10px 10px;border-top:2px solid {C["ink"]};font-size:12px;text-align:right;'
             f'color:{C["sub"]};white-space:nowrap">{fmt(tot["calls"])}회</td></tr>')
    inner = f"""
<table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;margin:16px -6px 0;border-collapse:separate"><tr>{tiles}</tr></table>
<div style="background:{C["card"]};border:1px solid {C["line"]};border-radius:12px;padding:20px;margin-top:8px">
  <div style="font-size:15px;font-weight:700">날짜별 사용량</div>
  <div style="margin:6px 0 10px">{h_legend()}</div>
  <table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse">{rows}</table>
</div>
{h_account(list(snaps), acct_dates or [d["date"] for d in days])}
{"".join(h_day_card(d) for d in active)}"""
    sub = f"{days[0]['date']} ({wd(days[0]['date'])}) ~ {days[-1]['date']} ({wd(days[-1]['date'])})"
    if machines:
        sub += " · " + ", ".join(machines)
    return h_page("기간 사용량 요약", sub, inner)


def day_html(d, snaps=()):
    inner = (f'<table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;margin:16px -6px 0;'
             f'border-collapse:separate"><tr>'
             + h_tile("총 토큰", fmt(d["total"])) + h_tile("API 호출", f'{fmt(d["calls"])}회')
             + h_tile("세션", f'{len(d["sessions"])}개') + h_tile("출력 토큰", fmt(d["output"]))
             + "</tr></table>"
             + (h_day_card(d) if d["total"] else
                f'<div style="margin-top:14px;font-size:14px;color:{C["sub"]}">이 날은 사용 기록이 없습니다.</div>')
             + h_account(list(snaps), [d["date"]]))
    return h_page("일일 사용량", f'{d["date"]} ({wd(d["date"])})', inner)


def find_day(data, date):
    for d in data:
        if d["date"] == date:
            return d
    return empty_day(date)


def export(machine):
    # DB 문서 id는 "<PC>__<날짜>", 본문은 그날 이 PC의 집계
    out_dir = os.path.join(HERE, "export", machine)
    os.makedirs(out_dir, exist_ok=True)
    generated = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    data = aggregate()
    for d in data:
        with open(os.path.join(out_dir, f"{machine}__{d['date']}.json"), "w", encoding="utf-8") as f:
            json.dump({"machine": machine, "generated": generated, **d}, f, ensure_ascii=False)
    export_account(machine, generated)
    missing = sorted({f"{d['date']}|{s['id']}" for d in data for s in d["sessions"] if s["title"] == "(요약 전)"})
    print(f"{machine}: {len(data)}일치 → {out_dir}")
    if missing:
        print("요약이 없는 세션 (summaries.json에 추가 필요):")
        print("\n".join("  " + m for m in missing))


def export_account(machine, generated):
    # DB 컬렉션 "account", 문서 id "acct__<PC>__<날짜>": 그날 이 PC에서 관찰한 계정 사용률 기록
    rows = []
    if os.path.exists(SNAPSHOTS):
        for line in open(SNAPSHOTS, encoding="utf-8"):
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    cached = cache_snapshot()
    if cached:
        rows.append(cached)
    if not rows:
        return
    by_day = defaultdict(list)
    for r in rows:
        day = datetime.fromtimestamp(r["t"], KST).strftime("%Y-%m-%d")
        by_day[day].append(r)
    out_dir = os.path.join(HERE, "export", machine + "-account")
    os.makedirs(out_dir, exist_ok=True)
    for day, snaps in by_day.items():
        # 캐시는 최신 값 하나뿐이라 앞서 저장한 같은 날 기록에 누적한다
        path = os.path.join(out_dir, f"acct__{machine}__{day}.json")
        try:
            old = json.load(open(path, encoding="utf-8")).get("snapshots", [])
        except (OSError, ValueError):
            old = []
        snaps = sorted({r["t"]: r for r in old + snaps}.values(), key=lambda r: r["t"])
        with open(os.path.join(out_dir, f"acct__{machine}__{day}.json"), "w", encoding="utf-8") as f:
            json.dump({"machine": machine, "date": day, "generated": generated, "snapshots": snaps}, f)
    print(f"계정 사용률: {len(by_day)}일치 → {out_dir}")


def cache_snapshot():
    try:
        c = json.load(open(RATE_CACHE, encoding="utf-8"))
        h5, d7 = c.get("five_hour") or {}, c.get("seven_day") or {}
        return {"t": int(c["ts"]), "h5": h5.get("used_percentage"), "h5r": h5.get("resets_at"),
                "d7": d7.get("used_percentage"), "d7r": d7.get("resets_at")}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def normalize(d):
    # 한때 쓰던 "캐시 읽기 제외" 기준(basis: no_cache_read) 문서를 캐시 읽기 포함 합계로 되돌린다.
    # 시간대·세션별 값은 구성 정보가 없어 그날 비율로 환산한다
    if d.get("basis") != "no_cache_read":
        return d
    old = d.get("total", 0)
    new = sum(d.get(k, 0) for k in KEYS)
    ratio = new / old if old else 0
    d["total"] = new
    d["hours"] = [round(h * ratio) for h in d.get("hours", [0] * 24)]
    d["sessions"] = [{**x, "tokens": round(x.get("tokens", 0) * ratio)} for x in d.get("sessions", [])]
    d["models"] = {m: {**v, "total": sum(v.get(k, 0) for k in KEYS)} for m, v in d.get("models", {}).items()}
    del d["basis"]
    return d


def load_docs(folder):
    docs = []
    for path in glob.glob(os.path.join(folder, "**", "*.json"), recursive=True):
        d = json.load(open(path, encoding="utf-8"))
        d = d.get("data", d) if "date" not in d else d
        if "date" in d and "snapshots" not in d:
            docs.append(normalize(d))
    return docs


def load_snapshots(folder=None):
    # 계정 사용률 기록: DB 폴더(account 문서, 모든 PC)가 있으면 그것, 없으면 이 PC의 기록 파일
    snaps = {}
    if folder:
        for path in glob.glob(os.path.join(folder, "**", "*.json"), recursive=True):
            d = json.load(open(path, encoding="utf-8"))
            d = d.get("data", d) if "snapshots" not in d else d
            for r in d.get("snapshots", []):
                snaps[r["t"]] = r
    elif os.path.exists(SNAPSHOTS):
        for line in open(SNAPSHOTS, encoding="utf-8"):
            try:
                r = json.loads(line)
                snaps[r["t"]] = r
            except (ValueError, KeyError):
                pass
    rows = [snaps[t] for t in sorted(snaps)]
    # 앞뒤 값과 동떨어진 순간 값(상태 표시줄 일시 오류)은 뺀다
    clean = []
    for i, r in enumerate(rows):
        a, b = rows[i - 1] if i else None, rows[i + 1] if i + 1 < len(rows) else None
        if a and b and r.get("h5") is not None and a.get("h5") is not None and b.get("h5") is not None \
                and abs(a["h5"] - b["h5"]) <= 10 and abs(r["h5"] - a["h5"]) > 30:
            continue
        clean.append(r)
    return clean


def kst(t, f="%m-%d %H:%M"):
    return datetime.fromtimestamp(t, KST).strftime(f)


def account_days(snaps, dates):
    # 날짜별: 첫·마지막 기록 시각, 5시간 한도 최고치, 주간 한도 시작→끝, 주간 초기화 시각
    out = []
    for date in dates:
        rs = [r for r in snaps if kst(r["t"], "%Y-%m-%d") == date]
        if not rs:
            continue
        h5 = [r["h5"] for r in rs if r.get("h5") is not None]
        d7 = [r["d7"] for r in rs if r.get("d7") is not None]
        out.append({"date": date, "first": kst(rs[0]["t"], "%H:%M"), "last": kst(rs[-1]["t"], "%H:%M"),
                    "h5_max": max(h5) if h5 else None, "d7_start": d7[0] if d7 else None,
                    "d7_end": d7[-1] if d7 else None, "d7r": rs[-1].get("d7r"), "n": len(rs)})
    return out


def account_text(snaps, dates):
    rows = account_days(snaps, dates)
    if not rows:
        return ""
    last = snaps[-1]
    lines = ["", "계정 전체 사용률 (모든 기기·웹 합산 한도 대비 %)",
             f"  현재 ({kst(last['t'])} 기준): 5시간 {last.get('h5')}% · 주간 {last.get('d7')}%"
             f" (주간 초기화 {kst(last['d7r']) if last.get('d7r') else '-'})",
             f"  {'날짜':<12}{'기록 시간':<14}{'5시간 최고':>10}{'주간':>14}"]
    for a in rows:
        lines.append(f"  {a['date']:<12}{a['first'] + '–' + a['last']:<14}{str(a['h5_max']) + '%':>10}"
                     f"{str(a['d7_start']) + '% → ' + str(a['d7_end']) + '%':>14}")
    return "\n".join(lines)


def week_window(snaps, now):
    # 주간 한도 구간: 가장 최근 기록의 주간 초기화 시각에서 7일 전 ~ now (기록이 없으면 최근 7일)
    reset = next((r["d7r"] for r in reversed(snaps) if r.get("d7r")), None)
    if not reset:
        return now - WEEK_SEC, now
    while reset <= now:
        reset += WEEK_SEC
    while reset - WEEK_SEC > now:
        reset -= WEEK_SEC
    return reset - WEEK_SEC, reset


def trim_day(d, from_hour):
    # 구간 첫날은 초기화 시각 이후 시간대만 남긴다 (토큰 구성·세션은 그날 비율로 환산)
    hours = [0] * from_hour + d["hours"][from_hour:]
    kept = sum(hours)
    ratio = kept / d["total"] if d["total"] else 0
    out = {**d, "hours": hours, "total": kept, "calls": round(d["calls"] * ratio),
           "sub_calls": round(d["sub_calls"] * ratio)}
    for k in KEYS:
        out[k] = round(d[k] * ratio)
    out["models"] = {m: {k: round(v.get(k, 0) * ratio) for k in v} for m, v in d["models"].items()}
    out["sessions"] = [s for s in d["sessions"] if (s.get("last") or "") >= f"{from_hour:02d}:00"]
    return out


def week_html(days, snaps, start, reset):
    tot = sum(d["total"] for d in days)
    last = snaps[-1] if snaps else {}
    pct = last.get("d7")
    tiles = (h_tile("주간 총 토큰", fmt(tot), "모든 PC 합산")
             + h_tile("주간 한도", f"{pct}%" if pct is not None else "-", "계정 전체 (모든 기기·웹)")
             + h_tile("주간 초기화", kst(reset), wd(kst(reset, "%Y-%m-%d")) + "요일")
             + h_tile("사용한 날", f'{len([d for d in days if d["total"]])}일', f"구간 {len(days)}일 중"))
    page = range_html(days, len(days), snaps, [d["date"] for d in days])
    # range_html의 제목·요약 타일을 주간 기준으로 바꾼다
    page = page.replace(">기간 사용량 요약<", ">주간 사용량<", 1)
    head = (f'<table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;margin:16px -6px 0;'
            f'border-collapse:separate"><tr>{tiles}</tr></table>'
            f'<div style="font-size:12px;color:{C["sub"]};margin-top:6px">주간 구간 {kst(start)} ~ {kst(reset)} (KST)</div>')
    i = page.index('<table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;margin:16px -6px 0')
    j = page.index("</table>", i) + len("</table>")
    return page[:i] + head + page[j:]


def send(date, folder=None):
    if not os.path.exists(GMAIL_CONF):
        sys.exit(f"메일 설정 파일이 없습니다: {GMAIL_CONF}")
    conf = json.load(open(GMAIL_CONF))
    if ".." in date or date == "all":
        docs = load_docs(folder) if folder else aggregate()
        if date == "all":
            # 데이터가 있는 날짜만 (사용 없는 날은 표에서 뺀다)
            dates = sorted({d["date"] for d in docs if d.get("total")})
            start, end = (datetime.strptime(x, "%Y-%m-%d").date() for x in (dates[0], dates[-1]))
        else:
            start, end = (datetime.strptime(x, "%Y-%m-%d").date() for x in date.split(".."))
            dates = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
        days = [merge(docs, x) if folder else find_day(docs, x) for x in dates]
        day = {"total": sum(d["total"] for d in days)}
        snaps = load_snapshots(folder)
        span = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
        text = range_text(days) + account_text(snaps, span)
        html = range_html(days, len(span), snaps, span)
        date = f"{dates[0]} ~ {dates[-1]}"
    else:
        # 기본 발송: 그날이 속한 주간 한도 구간(계정 전체·모든 PC 합산) 사용량
        docs = load_docs(folder) if folder else aggregate()
        snaps = load_snapshots(folder)
        end_of_day = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=KST) + timedelta(days=1)
        now = min(datetime.now(KST), end_of_day).timestamp()
        start, reset = week_window(snaps, now)
        first = datetime.fromtimestamp(start, KST)
        last = datetime.fromtimestamp(now, KST).date()
        dates = [(first.date() + timedelta(days=i)).isoformat() for i in range((last - first.date()).days + 1)]
        days = [merge(docs, x) if folder else find_day(docs, x) for x in dates]
        if days and first.hour:
            days[0] = trim_day(days[0], first.hour)
        day = {"total": sum(d["total"] for d in days)}
        pct = snaps[-1].get("d7") if snaps else None
        text = (f"주간 사용량 (계정 전체·모든 PC 합산) · {kst(start)} ~ {kst(reset)}\n"
                f"주간 총 토큰 {fmt(day['total'])} · 주간 한도 {pct if pct is not None else '-'}%\n\n"
                + range_text(days) + account_text(snaps, dates))
        html = week_html(days, snaps, start, reset)
        date = f"주간 {kst(start, '%m/%d')}~{kst(reset, '%m/%d')}" + (f" · 한도 {pct}%" if pct is not None else "")
    body = urllib.parse.urlencode({
        "client_id": conf["client_id"], "client_secret": conf["client_secret"],
        "refresh_token": conf["refresh_token"], "grant_type": "refresh_token",
    }).encode()
    with urllib.request.urlopen("https://oauth2.googleapis.com/token", body, timeout=30) as r:
        token = json.loads(r.read())["access_token"]
    msg = MIMEMultipart("alternative")
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    msg["To"] = conf["to"]
    msg["Subject"] = f"[클로드 데일리 써머리] {date} · 총 {fmt(day['total'])} 토큰"
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    req = urllib.request.Request(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        json.dumps({"raw": raw}).encode(),
        {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        print(f"발송 완료 ({date}) → {conf['to']} · id {json.loads(r.read())['id']}")


def main():
    args = sys.argv[1:]
    opts = {}
    for flag in ("--machine", "--from"):
        if flag in args:
            i = args.index(flag)
            opts[flag] = args[i + 1]
            del args[i:i + 2]
    cmd = args[0] if args else "export"
    arg = args[1] if len(args) > 1 else None
    today = datetime.now(KST).date()
    if cmd == "export":
        export(opts.get("--machine") or default_machine())
    elif cmd == "show":
        print(day_text(find_day(aggregate(), arg or today.isoformat())))
    elif cmd == "send":
        send(arg or (today - timedelta(days=1)).isoformat(), opts.get("--from"))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
