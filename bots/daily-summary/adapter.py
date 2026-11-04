#!/usr/bin/env python3
# IoT업무봇 ↔ 클로드 데일리 써머리 연결 (Mac·Windows 공용). 입력: argv[1]=기능, stdin=JSON / 출력: JSON
#   Mac     : ~/claude-daily-summary,           PC 이름 mac,     상태 표시줄 기록 있음
#   Windows : %USERPROFILE%\Downloads\claude-daily-summary, PC 이름 windows, 상태 표시줄 기록 ~/.claude/rate-limits-cache.json
import glob, json, os, platform, re, shutil, subprocess, sys, time, urllib.request
from datetime import datetime, timedelta

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
MACHINE = os.environ.get("CLAUDE_DAILY_MACHINE") or {"Darwin": "mac", "Windows": "windows"}.get(platform.system(), "linux")
DAILY = next((d for d in (os.environ.get("CLAUDE_DAILY_DIR"),
                          os.path.join(HOME, "claude-daily-summary"),
                          os.path.join(HOME, "Downloads", "claude-daily-summary"),
                          HERE)  # 데일리 써머리를 따로 설치하지 않은 PC는 봇에 들어 있는 사본을 쓴다
            if d and os.path.isfile(os.path.join(d, "claude_daily.py"))), None)
SCRIPT = DAILY and os.path.join(DAILY, "claude_daily.py")
RUNS = os.environ.get("BOT_HUB_RUNS") or os.path.join(HERE, "..", "..", "runs")
STATUS = os.path.join(HOME, ".cache", "claude-statusline", "last_input.json")
RATE_CACHE = os.path.join(HOME, ".claude", "rate-limits-cache.json")  # Windows 상태 표시줄(statusline.py)이 남기는 공식 한도 값
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LOCAL_DRIVE = [r"G:\내 드라이브\claude-daily", r"G:\My Drive\claude-daily"]  # Windows Google Drive 데스크톱 동기화 폴더


def out(**kw):
    print(json.dumps(kw, ensure_ascii=False))
    sys.exit(0)


def need_daily():
    if not SCRIPT:
        out(ok=False, text="데일리 써머리 폴더(claude_daily.py)를 찾지 못했습니다.\n"
                           "Mac: ~/claude-daily-summary, Windows: %USERPROFILE%\\Downloads\\claude-daily-summary 에 두거나\n"
                           "환경변수 CLAUDE_DAILY_DIR 로 위치를 지정하세요.")


def py(*args, cwd=None):
    p = subprocess.run([sys.executable, "-I", "-X", "utf8", *args], capture_output=True, text=True, encoding="utf-8", cwd=cwd or DAILY or HOME,
                       env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"))
    return p.returncode == 0, (p.stdout + ("\n" + p.stderr if p.stderr.strip() else "")).strip()


def when(e):
    if not e:
        return "-"
    e = e / 1000 if e > 1e11 else e
    d = datetime.fromtimestamp(e)
    return f"{d:%m/%d}({'월화수목금토일'[d.weekday()]}) {d:%H:%M}"  # 예: 10/25(일) 16:00


def now(q):
    # 계정 한도 %: 상태 표시줄이 마지막으로 받은 값 (Mac: last_input.json, Windows: rate-limits-cache.json) / 토큰: 이 PC의 Claude Code 기록
    try:
        rl = json.load(open(STATUS, encoding="utf-8")).get("rate_limits") or {}
        age = int(time.time() - os.path.getmtime(STATUS))
    except (OSError, ValueError):
        try:
            rl = json.load(open(RATE_CACHE, encoding="utf-8"))
            age = int(time.time() - rl.get("ts", os.path.getmtime(RATE_CACHE)))
        except (OSError, ValueError):
            rl, age = {}, None
    h5, d7 = rl.get("five_hour") or {}, rl.get("seven_day") or {}
    ok, tk = py(os.path.join(HERE, "window_tokens.py"), str(h5.get("resets_at") or ""), str(d7.get("resets_at") or ""), cwd=HERE)
    t5, t7, td = (tk.splitlines()[-1].split("|") + ["-", "-", "-"])[:3] if ok and tk else ("-", "-", "-")
    cards = []
    if rl:
        cards += [
            {"label": "5시간 한도", "value": h5.get("used_percentage"), "unit": "%", "sub": f"리셋 {when(h5.get('resets_at'))}", "gauge": True},
            {"label": "주간 한도", "value": d7.get("used_percentage"), "unit": "%", "sub": f"리셋 {when(d7.get('resets_at'))}", "gauge": True},
        ]
    cards += [
        {"label": f"이 PC({MACHINE}) 5시간 창", "value": t5, "unit": "토큰"},
        {"label": f"이 PC({MACHINE}) 주간 창", "value": t7, "unit": "토큰"},
        {"label": f"이 PC({MACHINE}) 오늘", "value": td, "unit": "토큰"},
    ]
    if rl:
        note = f"한도 %는 계정 전체(모든 PC·웹 합산), 토큰은 이 PC의 Claude Code 기록입니다. 한도 값은 {age // 60}분 전에 받은 값입니다."
    else:
        note = "이 PC에는 상태 표시줄 기록이 없어 계정 한도 %는 볼 수 없습니다(Mac에서 확인). 토큰 창은 지금 기준 최근 5시간·7일입니다."
    out(ok=True, view="cards", data={"cards": cards, "note": note},
        summary=f"5시간 {h5.get('used_percentage', '-')}% · 주간 {d7.get('used_percentage', '-')}%" if rl else f"이 PC 오늘 {td} 토큰")


def show(q):
    need_daily()
    date = q.get("date") or datetime.now().strftime("%Y-%m-%d")
    if not DATE.match(date):
        out(ok=False, text="날짜 형식은 YYYY-MM-DD")
    ok, text = py(SCRIPT, "show", date)
    out(ok=ok, text=text)


def fetch_other_pcs(stage):
    # 다른 PC 기록은 Google Drive claude-daily/<PC이름>, <PC이름>-account 에서 받는다 (이 PC 폴더는 제외)
    sys.path.insert(0, DAILY)
    import drive_upload as du
    tok = du.access_token()
    root = du.find_root(tok)
    got = []
    for folder in du.search(tok, f"'{root}' in parents and trashed = false"):
        name = folder["name"]
        if "." in name or name.split("-")[0] == MACHINE:  # mac_setup.sh 같은 파일·이 PC 폴더 제외
            continue
        os.makedirs(os.path.join(stage, name), exist_ok=True)
        n = 0
        for f in du.search(tok, f"'{folder['id']}' in parents and trashed = false"):
            if not f["name"].endswith(".json"):
                continue
            req = urllib.request.Request(f"https://www.googleapis.com/drive/v3/files/{f['id']}?alt=media",
                                         headers={"Authorization": "Bearer " + tok})
            data = urllib.request.urlopen(req, timeout=30).read()
            json.loads(data)
            open(os.path.join(stage, name, os.path.basename(f["name"])), "wb").write(data)
            n += 1
        if n:
            got.append(f"{name} {n}개")
    return got


def period(q):
    need_daily()
    s, e = q.get("start") or "", q.get("end") or ""
    if not (DATE.match(s) and DATE.match(e)) or s > e:
        out(ok=False, text="시작일·종료일을 확인하세요 (YYYY-MM-DD, 시작 ≤ 종료)")
    return s, e


def gather():
    # 모든 PC 기록을 한 폴더(stage)에 모은다: 이 PC는 방금 내보낸 것, 다른 PC는 Drive와 공유 DB 사본(db_cache) 중 최신
    log = []
    ok, t = py(SCRIPT, "export", "--machine", MACHINE)
    log.append(f"① 이 PC({MACHINE}) 기록 내보내기\n" + t)
    stage = os.path.join(RUNS, f"daily-{time.strftime('%Y%m%d-%H%M%S')}")
    for sub in (MACHINE, MACHINE + "-account"):
        src = os.path.join(DAILY, "export", sub)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(stage, sub))
    try:
        got = fetch_other_pcs(stage)
        log.append("② 다른 PC 기록 받음 (Google Drive): " + (", ".join(got) or "없음"))
    except Exception as ex:  # Drive 설정이 없거나 실패하면 이 PC 기록만으로 보낸다
        log.append(f"② 다른 PC 기록을 받지 못했습니다 — 이 PC 기록만 포함 ({type(ex).__name__}: {ex})")
    # 대화 세션에서 받아 둔 공유 DB 사본(db_cache)과 PC에 동기화된 Drive 폴더가 더 최신이면 그것을 쓴다
    extra = [os.path.join(DAILY, "db_cache", sub) for sub in ("usage", "account")]
    extra += [os.path.join(d, x) for d in LOCAL_DRIVE if os.path.isdir(d) for x in os.listdir(d)]
    newer = 0
    for folder in extra:
        for path in glob.glob(os.path.join(folder, "*.json")):
            sub = "account" if os.path.basename(path).startswith("acct__") else "usage"
            name = os.path.basename(path)
            if name.startswith((f"{MACHINE}__", f"acct__{MACHINE}__")):
                continue
            pc = name.split("__")[1] if name.startswith("acct__") else name.split("__")[0]
            dst = os.path.join(stage, pc + ("-account" if sub == "account" else ""), name)
            if generated(path) > generated(dst):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy(path, dst)
                newer += 1
    if newer:
        log.append(f"③ 공유 DB 사본·Drive 폴더에서 더 최신 기록 {newer}개 사용")
    return stage, log


def generated(path):
    try:
        d = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(d.get("generated") or (d.get("data") or {}).get("generated") or "")


def report(q):
    # 기간 리포트 화면: 날짜별 계정 전체(모든 PC 합산) 토큰을 PC별로 나눠 차트로 보여 준다
    s, e = period(q)
    stage, log = gather()
    sys.path.insert(0, DAILY)
    import claude_daily as cd
    by = {}
    for d in cd.load_docs(stage):
        if s <= d["date"] <= e:
            by.setdefault(d["date"], {})[d.get("machine") or "?"] = d.get("total", 0)
    pcs = sorted({m for v in by.values() for m in v}, key=lambda m: (m != "mac", m != "windows", m))
    days, cur, end = [], datetime.strptime(s, "%Y-%m-%d"), datetime.strptime(e, "%Y-%m-%d")
    while cur <= end:
        k = cur.strftime("%Y-%m-%d")
        v = by.get(k, {})
        days.append({"date": k, "by": {m: v.get(m, 0) for m in pcs}, "total": sum(v.values())})
        cur += timedelta(days=1)
    used = [x for x in days if x["total"]]
    total = sum(x["total"] for x in days)
    peak = max(days, key=lambda x: x["total"]) if used else None
    out(ok=True, view="usage_chart", text="\n".join(log),
        data={"start": s, "end": e, "pcs": pcs, "days": days, "total": total, "active": len(used),
              "avg": total // len(used) if used else 0, "peak": peak and {"date": peak["date"], "total": peak["total"]},
              "pc_total": {m: sum(x["by"][m] for x in days) for m in pcs}},
        summary=f"{s}~{e} 계정 전체 {total:,} 토큰 · 사용일 {len(used)}일")


def send(q):
    s, e = period(q)
    stage, log = gather()
    ok, t = py(SCRIPT, "send", f"{s}..{e}", "--from", stage)
    log.append("④ 메일 발송\n" + t)
    out(ok=ok, text="\n\n".join(log), summary=t.splitlines()[-1] if t else "")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    q = json.loads(sys.stdin.read() or "{}")
    {"now": now, "show": show, "report": report, "send": send}.get(action, lambda q: out(ok=False, text="알 수 없는 기능"))(q)
