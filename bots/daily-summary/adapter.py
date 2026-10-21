#!/usr/bin/env python3
# 업무 봇 ↔ 클로드 데일리 써머리 연결 (Mac·Windows 공용). 입력: argv[1]=기능, stdin=JSON / 출력: JSON
#   Mac     : ~/claude-daily-summary,           PC 이름 mac,     상태 표시줄 기록 있음
#   Windows : %USERPROFILE%\Downloads\claude-daily-summary, PC 이름 windows, 상태 표시줄 기록 ~/.claude/rate-limits-cache.json
import json, os, platform, re, shutil, subprocess, sys, time, urllib.request
from datetime import datetime

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
MACHINE = os.environ.get("CLAUDE_DAILY_MACHINE") or {"Darwin": "mac", "Windows": "windows"}.get(platform.system(), "linux")
DAILY = next((d for d in (os.environ.get("CLAUDE_DAILY_DIR"),
                          os.path.join(HOME, "claude-daily-summary"),
                          os.path.join(HOME, "Downloads", "claude-daily-summary")) if d and os.path.isfile(os.path.join(d, "claude_daily.py"))), None)
SCRIPT = DAILY and os.path.join(DAILY, "claude_daily.py")
RUNS = os.environ.get("BOT_HUB_RUNS") or os.path.join(HERE, "..", "..", "runs")
STATUS = os.path.join(HOME, ".cache", "claude-statusline", "last_input.json")
RATE_CACHE = os.path.join(HOME, ".claude", "rate-limits-cache.json")  # Windows 상태 표시줄(statusline.py)이 남기는 공식 한도 값
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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
    return datetime.fromtimestamp(e).strftime("%m-%d %H:%M")


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


def send(q):
    need_daily()
    s, e = q.get("start") or "", q.get("end") or ""
    if not (DATE.match(s) and DATE.match(e)) or s > e:
        out(ok=False, text="시작일·종료일을 확인하세요 (YYYY-MM-DD, 시작 ≤ 종료)")
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
    ok, t = py(SCRIPT, "send", f"{s}..{e}", "--from", stage)
    log.append("③ 메일 발송\n" + t)
    out(ok=ok, text="\n\n".join(log), summary=t.splitlines()[-1] if t else "")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    q = json.loads(sys.stdin.read() or "{}")
    {"now": now, "show": show, "send": send}.get(action, lambda q: out(ok=False, text="알 수 없는 기능"))(q)
