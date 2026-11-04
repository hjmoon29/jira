#!/usr/bin/env python3
"""팀 휴가 캘린더: 네이버웍스 주소록(조직도)에서 우리 팀원 이름 뒤 상태('휴가', '휴가(오전)', '휴가(오후)', '경조휴가')를 읽어
Google 캘린더의 팀 캘린더(기본 'IoT기획팀')에 그날 종일 일정 '이름 상태' 로 등록한다.

- 브라우저: Playwright(Edge) 프로필 하나(~/.playwright-teamleave-profile)에 네이버웍스·구글 로그인을 함께 남긴다.
  처음 한 번 '로그인' 기능으로 창을 띄워 직접 로그인 (비밀번호는 저장·입력하지 않음).
- 중복 방지: runs/team-leave/state.json 에 (날짜, 이름, 상태) 기록. 같은 날 같은 사람·같은 상태는 한 번만 등록.
- 자동 실행: Windows 작업 스케줄러 'WorkBotTeamLeave' (설정의 시각들) → auto.py
"""
import datetime as dt
import json, os, platform, re, sys, time
from urllib.parse import urlencode

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DIR = os.path.join(ROOT, "runs", "team-leave")
STATE = os.path.join(DIR, "state.json")
LOG = os.path.join(DIR, "auto.log")
CONFIG = os.path.join(HERE, "config.json")
PROFILE = os.path.join(os.path.expanduser("~"), ".playwright-teamleave-profile")
CHANNEL = "msedge" if platform.system() == "Windows" else "chrome"
KST = dt.timezone(dt.timedelta(hours=9))
TASK_NAME = "WorkBotTeamLeave"
CAL = "https://calendar.google.com/calendar/"


class BotError(Exception):
    pass


def config():
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)


def save_config(c):
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=2)


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"done": [], "checks": []}


def save_state(s):
    os.makedirs(DIR, exist_ok=True)
    cut = (dt.datetime.now(KST) - dt.timedelta(days=120)).strftime("%Y-%m-%d")
    s["done"] = [d for d in s.get("done", []) if d["date"] >= cut]
    s["checks"] = s.get("checks", [])[-60:]
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)


# ---------- 상태 해석 ----------
def parse_status(display, statuses):
    """'최린아 님[IoT기획팀/팀원(경조휴가)]' → ('최린아', '경조휴가'). 대상 상태가 아니면 (이름, None)"""
    name = re.split(r"\s*님?\s*\[", display.strip(), 1)[0].strip()
    name = re.sub(r"\s*님$", "", name)
    m = re.search(r"\[(.*)\]", display)
    tail = m.group(1) if m else display
    flat = re.sub(r"\s+", "", tail)
    # 긴 이름부터 확인 ('경조휴가' 안의 '휴가', '휴가(오전)' 안의 '휴가' 를 먼저 잡지 않도록)
    for st in sorted(statuses, key=len, reverse=True):
        key = re.sub(r"\s+", "", st)
        if key in flat:
            if key == "휴가":  # 단순 '휴가' 는 오전/오후·경조가 아닐 때만
                if re.search(r"(경조|휴가\(오[전후]\))", flat):
                    continue
            return name, st
    return name, None


# ---------- 브라우저 ----------
def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise BotError("Playwright 가 설치돼 있지 않습니다. 명령 창에서: py -3 -m pip install playwright")
    return sync_playwright


def _open(p, headless=True):
    try:
        return p.chromium.launch_persistent_context(PROFILE, channel=CHANNEL, headless=headless, locale="ko-KR",
                                                    timezone_id="Asia/Seoul", viewport={"width": 1400, "height": 900})
    except Exception as e:
        if "lock" in str(e).lower() or "already" in str(e).lower():
            raise BotError("팀 휴가 봇 브라우저가 이미 열려 있습니다. 그 창을 닫고 다시 시도하세요.")
        raise BotError(f"브라우저를 열지 못했습니다: {e}")


def _works_ok(page):
    return page.url.startswith("https://contact.worksmobile.com/") and "login" not in page.url.lower()


def _google_ok(page):
    return page.url.startswith(CAL)


def login(log=print):
    """창 하나에 탭 두 개(네이버웍스 주소록·구글 캘린더)를 띄워 사용자가 직접 로그인. 둘 다 되면 닫는다."""
    c = config()
    with _playwright()() as p:
        ctx = _open(p, headless=False)
        w = ctx.pages[0] if ctx.pages else ctx.new_page()
        w.goto(c["orgunit_url"])
        g = ctx.new_page()
        g.goto(CAL + "r")
        log("열린 창의 두 탭에서 네이버웍스·구글에 각각 로그인해 주세요 (최대 10분)")
        end = time.time() + 600
        ok = False
        while time.time() < end:
            try:
                if _works_ok(w) and _google_ok(g) and w.locator("span.text strong").count():
                    ok = True
                    break
            except Exception:
                pass
            time.sleep(2)
        ctx.close()
    return ok


def read_team(page, c, log=print):
    """주소록 팀 화면에서 [(이름, 표시 문자열)]"""
    page.goto(c["orgunit_url"], wait_until="domcontentloaded")
    try:
        page.wait_for_selector("span.text strong", timeout=30000)
    except Exception:
        if not _works_ok(page):
            raise BotError("네이버웍스 로그인이 필요합니다. '로그인' 탭에서 한 번 로그인해 주세요.")
        raise BotError("주소록 화면에서 팀원 목록을 찾지 못했습니다(화면 구조가 바뀌었을 수 있음).")
    time.sleep(1.5)
    names = page.eval_on_selector_all("span.text strong", "els => els.map(e => e.textContent.trim())")
    names = [n for n in names if "[" in n]
    if not names:
        raise BotError("팀원 목록이 비어 있습니다.")
    return names


def _calendar_pick(page, cal_name):
    """일정 편집 화면에서 캘린더 선택 드롭다운을 cal_name 으로 바꾼다."""
    # 캘린더 선택 칸 = 현재 캘린더 이름(기본은 내 이름/메일)이 적힌 listbox
    cands = page.locator("[role='listbox'][aria-label*='캘린더'], [role='combobox'][aria-label*='캘린더'], [aria-label*='Calendar'][role='listbox']")
    target = cands.first if cands.count() else None
    if target is None:
        raise BotError("일정 화면에서 캘린더 선택 칸을 찾지 못했습니다.")
    if cal_name in (target.inner_text() or ""):
        return
    target.click()
    time.sleep(0.8)
    opt = page.get_by_role("option", name=re.compile(re.escape(cal_name)))
    if not opt.count():
        page.keyboard.press("Escape")
        raise BotError(f"캘린더 목록에 '{cal_name}' 이(가) 없습니다. 구글 캘린더에서 그 캘린더에 일정 추가 권한이 있는지 확인하세요.")
    opt.first.click()
    time.sleep(0.8)
    if cal_name not in (target.inner_text() or ""):
        raise BotError(f"캘린더를 '{cal_name}' 로 바꾸지 못했습니다.")


def _norm(s):
    return re.sub(r"\s+", "", s or "")


def day_events(page, day):
    """구글 캘린더 일 보기에서 그날 일정 글자 목록 (보이는 캘린더 전부 — 팀 캘린더가 켜져 있어야 함)"""
    page.goto(CAL + f"r/day/{day:%Y/%m/%d}", wait_until="networkidle")
    if not _google_ok(page):
        raise BotError("Google 캘린더 로그인이 필요합니다. '로그인' 탭에서 다시 로그인해 주세요.")
    time.sleep(1.5)
    texts = page.eval_on_selector_all("[data-eventid], [data-eventchip]", "els => els.map(e => e.innerText || e.textContent || '')")
    if not texts:  # 화면 구조가 바뀌었을 때 대비: 본문 전체 글자
        texts = [page.locator("[role='main']").first.inner_text() if page.locator("[role='main']").count() else ""]
    return texts


def already_there(texts, name, status):
    """같은 날 일정 중 '이름'과 '상태'가 함께 들어 있는 것이 있으면 True (직접 넣은 일정 포함)"""
    n, s = _norm(name), _norm(status)
    return any(n in _norm(t) and s in _norm(t) for t in texts)


def add_event(page, cal_name, day, title):
    d0 = day.strftime("%Y%m%d")
    d1 = (day + dt.timedelta(days=1)).strftime("%Y%m%d")
    page.goto(CAL + "r/eventedit?" + urlencode({"text": title, "dates": f"{d0}/{d1}"}), wait_until="networkidle")
    if not _google_ok(page):
        raise BotError("Google 캘린더 로그인이 필요합니다. '로그인' 탭에서 다시 로그인해 주세요.")
    _calendar_pick(page, cal_name)
    save = page.locator("button[jsname='x8hlje']")
    if not save.count():
        save = page.get_by_role("button", name=re.compile(r"^(저장|Save)$"))
    save.first.click()
    end = time.time() + 20
    while time.time() < end:
        if "eventedit" not in page.url:
            return
        for name in ("보내기", "Send", "저장", "Save", "확인", "OK"):
            b = page.locator("div[role='dialog'], div[role='alertdialog']").get_by_role("button", name=name)
            if b.count() and b.first.is_visible():
                b.first.click()
                break
        time.sleep(1)
    raise BotError("Google 캘린더에서 저장이 끝나지 않았습니다. 캘린더에서 일정이 만들어졌는지 확인해 주세요.")


# ---------- 실행 ----------
def check(log=print, register=True):
    """지금 팀원 상태를 읽고, 대상 상태인 사람을 오늘 날짜로 팀 캘린더에 등록. 결과 dict"""
    c = config()
    st = load_state()
    today = dt.datetime.now(KST).date()
    done_keys = {(d["date"], d["name"], d["status"]) for d in st.get("done", [])}
    people, added, errors = [], [], []
    with _playwright()() as p:
        ctx = _open(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            for disp in read_team(page, c, log):
                name, status = parse_status(disp, c["statuses"])
                people.append({"name": name, "display": disp, "status": status})
            targets = [x for x in people if x["status"] and (not c.get("members") or x["name"] in c["members"])]
            log(f"팀원 {len(people)}명 중 대상 상태 {len(targets)}명" + (": " + ", ".join(f"{x['name']}({x['status']})" for x in targets) if targets else ""))
            todo = [x for x in targets if (today.isoformat(), x["name"], x["status"]) not in done_keys]
            for x in targets:
                if x not in todo:
                    x["result"] = "이미 등록"
            # 캘린더에 이미 있는 일정(다른 사람이 직접 넣은 것 포함)은 건너뜀
            existing = day_events(page, today) if todo else []
            for x in todo:
                key = (today.isoformat(), x["name"], x["status"])
                title = c.get("title_format", "{name} {status}").format(name=x["name"], status=x["status"])
                if already_there(existing, x["name"], x["status"]):
                    x["result"] = "캘린더에 이미 있음"
                    st.setdefault("done", []).append({"date": key[0], "name": key[1], "status": key[2], "title": title + " (기존 일정)",
                                                      "at": dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M")})
                    done_keys.add(key)
                    log(f"건너뜀(이미 있음): {title}")
                    continue
                if not register:
                    x["result"] = "등록 예정(미리보기)"
                    continue
                try:
                    add_event(page, c["calendar"], today, title)
                    st.setdefault("done", []).append({"date": key[0], "name": key[1], "status": key[2], "title": title,
                                                      "at": dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M")})
                    done_keys.add(key)
                    added.append(title)
                    x["result"] = "등록함"
                    log(f"등록: {today:%m/%d} {title} → {c['calendar']}")
                except BotError as e:
                    x["result"] = f"실패: {e}"
                    errors.append(f"{title}: {e}")
                    log(f"실패: {title} — {e}")
        finally:
            ctx.close()
    if register:
        st.setdefault("checks", []).append({"at": dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "targets": len([x for x in people if x["status"]]),
                                            "added": len(added), "errors": len(errors)})
        save_state(st)
    return {"people": people, "added": added, "errors": errors, "date": today.isoformat()}


def board(result=None):
    st = load_state()
    tables = []
    if result:
        rows = [[x["name"], x["status"] or "-", x.get("result", ""), x["display"]] for x in result["people"]]
        tables.append({"title": f"지금 팀원 상태 ({result['date']})", "columns": ["이름", "휴가 상태", "캘린더", "주소록 표시"],
                       "rows": rows, "total": len(rows), "shown": len(rows)})
    done = sorted(st.get("done", []), key=lambda d: (d["date"], d.get("at", "")), reverse=True)[:60]
    tables.append({"title": "캘린더에 등록한 휴가", "columns": ["날짜", "일정 제목", "등록 시각"],
                   "rows": [[d["date"], d["title"], d.get("at", "")] for d in done], "total": len(done), "shown": len(done)})
    chk = list(reversed(st.get("checks", [])))[:15]
    tables.append({"title": "최근 확인 기록", "columns": ["확인 시각", "휴가 상태 인원", "새로 등록", "실패"],
                   "rows": [[x["at"], x["targets"], x["added"], x["errors"]] for x in chk], "total": len(chk), "shown": len(chk)})
    return {"tables": tables}


# ---------- 자동 실행 (Windows 작업 스케줄러) ----------
def schedule_status():
    import subprocess
    if sys.platform != "win32":
        return {"on": False}
    p = subprocess.run(["schtasks", "/query", "/tn", TASK_NAME, "/fo", "list"], capture_output=True, text=True, encoding="cp949", errors="replace")
    if p.returncode:
        return {"on": False}
    nxt = re.search(r"(다음 실행 시간|Next Run Time):\s*(.+)", p.stdout)
    return {"on": True, "next": nxt.group(2).strip() if nxt else ""}


def set_schedule(on, times):
    import subprocess
    if sys.platform != "win32":
        raise BotError("자동 실행 등록은 Windows 에서만 지원합니다")
    times = [t.strip() for t in re.split(r"[,\s]+", times or "") if t.strip()]
    bad = [t for t in times if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t)]
    if bad or (on and not times):
        raise BotError("시각은 HH:MM 을 쉼표로 (예: 08:40, 10:00, 13:30)")
    subprocess.run(["schtasks", "/delete", "/tn", TASK_NAME, "/f"], capture_output=True)
    if not on:
        return "자동 확인을 껐습니다"
    script = os.path.join(HERE, "auto.py")
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pyw if os.path.isfile(pyw) else sys.executable
    trig = ",".join(f"(New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At '{t}')" for t in times)
    ps = (f"$a = New-ScheduledTaskAction -Execute '{exe}' -Argument '-X utf8 \"{script}\"' -WorkingDirectory '{HERE}';"
          f"$t = @({trig});"
          "$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 20);"
          f"Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $a -Trigger $t -Settings $s -Force | Out-Null")
    p = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, encoding="cp949", errors="replace")
    if p.returncode:
        raise BotError("작업 스케줄러 등록 실패: " + (p.stderr or p.stdout)[-400:])
    return f"평일 {', '.join(times)} 자동 확인 (작업 스케줄러 '{TASK_NAME}', PC가 꺼져 있었으면 켠 뒤 바로 실행)"
