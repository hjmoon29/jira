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
# 로그인 안 된 채 캘린더 주소로 가면 구글이 소개 페이지(workspace.google.com)로 보내므로 로그인 화면으로 바로 연다
GOOGLE_LOGIN = "https://accounts.google.com/ServiceLogin?service=cl&continue=https%3A%2F%2Fcalendar.google.com%2Fcalendar%2Fr"


class BotError(Exception):
    pass


SITE_NAMES = {"works": "네이버웍스", "google": "구글 캘린더"}


class LoginNeeded(BotError):
    """로그인이 안 돼 있어 진행할 수 없음. sites = ['works', 'google'] 중 필요한 것"""
    def __init__(self, sites):
        self.sites = list(sites)
        super().__init__(" · ".join(SITE_NAMES[x] for x in self.sites) + " 로그인이 필요합니다.")


# ---------- 실행 중지 ----------
def _procs():
    """[(pid, ppid, 명령줄)] — 이 PC에서 실행 중인 모든 프로세스"""
    import subprocess
    if sys.platform == "win32":
        ps = ("Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CommandLine | "
              "ConvertTo-Json -Compress")
        p = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        try:
            rows = json.loads(p.stdout or "[]")
        except ValueError:
            raise BotError("실행 중인 프로그램 목록을 읽지 못했습니다: " + (p.stderr or p.stdout)[-300:])
        rows = rows if isinstance(rows, list) else [rows]
        return [(r["ProcessId"], r["ParentProcessId"], r.get("CommandLine") or "") for r in rows]
    p = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True)
    out = []
    for line in p.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 2:
            out.append((int(parts[0]), int(parts[1]), parts[2] if len(parts) > 2 else ""))
    return out


def stop(log=print):
    """이 봇이 하고 있는 일을 모두 멈춘다: 화면에서 누른 기능(지금 확인·미리보기·로그인), 평일 자동 확인(auto.py),
    그리고 그 기능들이 띄운 브라우저(전용 프로필 창). 반환: 멈춘 항목 설명 목록"""
    import signal, subprocess
    me = os.getpid()
    norm = lambda s: s.replace("\\", "/").lower()
    here, prof = norm(HERE), norm(PROFILE)
    procs = _procs()
    parent = {pid: ppid for pid, ppid, _ in procs}
    mine = set()  # 나 자신과 내 부모(허브 서버 등)는 건드리지 않는다
    x = me
    while x and x not in mine:
        mine.add(x)
        x = parent.get(x)
    bots, browsers = [], []
    for pid, _, cmd in procs:
        c = norm(cmd)
        if pid in mine or not c:
            continue
        if "python" in c and here in c and ("adapter.py" in c or "auto.py" in c):
            what = "자동 확인" if "auto.py" in c else {"run": "지금 확인", "preview": "미리보기", "login": "로그인"}.get(c.split()[-1].strip('"'), "실행 중 기능")
            bots.append((pid, what))
        elif prof in c:
            browsers.append(pid)
    done = []
    if sys.platform == "win32":
        subprocess.run(["schtasks", "/end", "/tn", TASK_NAME], capture_output=True)  # 작업 스케줄러에서 도는 중이면 그것도 끝냄
        for pid, what in bots:  # /T: 그 기능이 띄운 브라우저·드라이버까지 함께
            # 하위 브라우저가 먼저 닫히면 taskkill 이 0이 아닌 값을 돌려주므로, 결과는 아래에서 실제로 사라졌는지로 판단
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
        time.sleep(1)
        now = _procs()
        left = {p for p, _, _ in now}
        for pid, what in bots:
            done.append(f"{what} 중지 (PID {pid})" if pid not in left else f"{what} 중지 실패 (PID {pid}) — 작업 관리자에서 종료해 주세요")
        closed = 0
        for _ in range(3):  # 중지 직전에 새로 뜬 창이 있을 수 있어 목록을 다시 읽어 전용 프로필 브라우저를 모두 닫는다
            br = {pid: ppid for pid, ppid, cmd in now if pid not in mine and prof in norm(cmd)}
            roots = [p for p, pp in br.items() if pp not in br]
            if not roots:
                break
            for pid in roots:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
            closed += len(roots)
            time.sleep(1)
            now = _procs()
        if closed:
            done.append(f"남은 브라우저 창 닫음 ({closed}개)")
    else:
        for pid, what in bots:
            subprocess.run(["pkill", "-TERM", "-P", str(pid)], capture_output=True)
            try:
                os.kill(pid, signal.SIGTERM)
                done.append(f"{what} 중지 (PID {pid})")
            except OSError:
                pass
        for pid in browsers:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        if browsers:
            done.append(f"남은 브라우저 창 닫음 ({len(browsers)}개)")
    if bots:
        os.makedirs(DIR, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now(KST):%Y-%m-%d %H:%M:%S} 사용자가 실행 중지: {', '.join(w for _, w in bots)}\n")
    return done


try:  # 화면 진행률 (bots/hub_progress.py) — 없거나 허브 밖 실행이면 아무것도 안 함
    sys.path.insert(0, os.path.dirname(HERE))
    from hub_progress import progress
except ImportError:  # pragma: no cover
    def progress(*a, **k):
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
def _import_playwright():
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except ImportError:
        pass
    # IoT업무봇은 하위 봇을 -I 로 실행해 사용자 폴더 설치(pip --user, Mac 기본)를 못 보므로 한 번 더 찾는다
    import importlib, site
    for d in (site.getusersitepackages(),):
        if d not in sys.path:
            sys.path.append(d)
    importlib.invalidate_caches()
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except ImportError:
        return None


def _playwright(log=print):
    sp = _import_playwright()
    if sp:
        return sp
    # 없으면 직접 설치 (사용자 폴더에 설치 — 관리자 권한 불필요)
    import subprocess
    log("Playwright 가 없어 설치합니다 (처음 한 번, 1~2분)…")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "--user", "playwright"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=900)
    sp = _import_playwright()
    if not sp:
        raise BotError("Playwright 자동 설치에 실패했습니다. 명령 창에서 직접 설치해 주세요.\n"
                       f"  {'py -3' if sys.platform == 'win32' else 'python3'} -m pip install --user playwright\n\n"
                       + (r.stderr or r.stdout)[-600:])
    log("Playwright 설치 완료")
    return sp


def _install_chromium(log=print):
    import subprocess
    log("브라우저 실행 파일이 없어 Playwright 용 Chromium 을 내려받습니다 (처음 한 번, 1~3분)…")
    r = subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=900)
    if r.returncode:
        raise BotError("브라우저(Chromium) 설치에 실패했습니다:\n" + (r.stderr or r.stdout)[-600:])


def _open(p, headless=True, log=print):
    opts = dict(headless=headless, locale="ko-KR", timezone_id="Asia/Seoul", viewport={"width": 1400, "height": 900})
    last = None
    # 1) 설치된 Edge/Chrome → 2) Playwright 내장 Chromium → 3) 내장 Chromium 설치 후 다시
    for step in ("channel", "bundled", "install"):
        try:
            if step == "install":
                _install_chromium(log)
            kw = dict(opts, channel=CHANNEL) if step == "channel" else opts
            return p.chromium.launch_persistent_context(PROFILE, **kw)
        except BotError:
            raise
        except Exception as e:  # noqa: BLE001
            msg = str(e).lower()
            if "lock" in msg or "already" in msg or "processsingleton" in msg:
                raise BotError("팀 휴가 봇 브라우저 창이 이미 열려 있습니다. 그 창을 닫고 다시 시도하세요.")
            last = e
            if step == "bundled" and "executable" not in msg and "install" not in msg:
                break
    raise BotError(f"브라우저를 열지 못했습니다: {last}")


def _works_ok(page):
    return page.url.startswith("https://contact.worksmobile.com/") and "login" not in page.url.lower()


def _google_ok(page):
    return page.url.startswith(CAL)


def _banner(ctx, msg):
    """로그인 창 아래쪽에 할 일 안내 띠를 띄운다 (로그인 화면 포함 모든 페이지)"""
    js = """(() => { const add = () => { if (!document.body || document.getElementById('__iotbot')) return;
      const d = document.createElement('div'); d.id = '__iotbot'; d.textContent = %s;
      d.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:2147483647;background:#1d4ed8;color:#fff;'
        + 'font:600 14px/1.5 sans-serif;padding:10px 16px;text-align:center;pointer-events:none';
      document.body.appendChild(d); };
      if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', add); else add(); })();""" % json.dumps(msg, ensure_ascii=False)
    ctx.add_init_script(js)


def login(log=print, sites=("works", "google")):
    """로그인 창을 띄워 사용자가 직접 로그인. 필요한 곳(sites)이 모두 로그인되면 닫고 True.
    10분 초과 또는 사용자가 창을 닫으면 False"""
    c = config()
    sites = [x for x in ("works", "google") if x in sites]
    with _playwright(log)() as p:
        ctx = _open(p, headless=False, log=log)
        tabs = " · ".join(f"{i + 1}번째 탭: {SITE_NAMES[x]}" for i, x in enumerate(sites))
        _banner(ctx, f"IoT업무봇 · 팀 휴가 캘린더 — 이 창에서 로그인해 주세요 ({tabs}). "
                     "로그인되면 창이 저절로 닫히고 하던 작업을 이어갑니다. (비밀번호는 저장하지 않습니다)")
        pages = {}
        for i, x in enumerate(sites):
            pg = ctx.pages[0] if (i == 0 and ctx.pages) else ctx.new_page()
            try:
                pg.goto(c["orgunit_url"] if x == "works" else GOOGLE_LOGIN)
            except Exception:  # noqa: BLE001 — 로그인 화면으로 넘어가며 생기는 이동 오류는 무시
                pass
            pages[x] = pg
        pages[sites[0]].bring_to_front()
        log(f"로그인 창을 열었습니다 — {', '.join(SITE_NAMES[x] for x in sites)} 에 로그인해 주세요 (최대 10분)")
        end = time.time() + 600
        ok = False
        while time.time() < end:
            left = int(end - time.time())
            progress(0, f"🔑 로그인 기다리는 중 — 열린 창에서 {', '.join(SITE_NAMES[x] for x in sites)} 로그인 (남은 {left // 60}:{left % 60:02d})")
            try:
                if all(pg.is_closed() for pg in pages.values()):
                    break  # 사용자가 창을 닫음
                done = []
                for x, pg in pages.items():
                    if pg.is_closed():
                        continue
                    if x == "works" and _works_ok(pg) and pg.locator("span.text strong").count():
                        done.append(x)
                    if x == "google" and _google_ok(pg):
                        done.append(x)
                    elif x == "google" and "workspace.google.com" in pg.url:  # 소개 페이지로 빠졌으면 로그인 화면으로
                        pg.goto(GOOGLE_LOGIN)
                if len(done) == len(pages):
                    ok = True
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(2)
        try:
            ctx.close()
        except Exception:  # noqa: BLE001
            pass
    return ok


def read_team(page, c, log=print):
    """주소록 팀 화면에서 [(이름, 표시 문자열)]"""
    page.goto(c["orgunit_url"], wait_until="domcontentloaded")
    try:
        page.wait_for_selector("span.text strong", timeout=30000)
    except Exception:
        if not _works_ok(page):
            raise LoginNeeded(["works"])
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
        raise LoginNeeded(["google"])
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
        raise LoginNeeded(["google"])
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
def _missing_logins(page, c):
    """네이버웍스·구글 로그인 상태를 미리 확인해 로그인이 필요한 곳 목록"""
    miss = []
    try:
        read_team(page, c)
    except LoginNeeded:
        miss.append("works")
    except BotError:
        pass  # 로그인 말고 다른 문제는 본 작업에서 그대로 알린다
    try:
        page.goto(CAL + "r", wait_until="domcontentloaded")
        time.sleep(1.5)
        if not _google_ok(page):
            miss.append("google")
    except Exception:  # noqa: BLE001
        miss.append("google")
    return miss


def check(log=print, register=True, auto_login=True):
    """check 실행. 로그인이 필요하면(auto_login) 로그인 창을 띄워 로그인을 받은 뒤 이어서 진행한다."""
    for attempt in range(3):
        try:
            return _check(log, register, preflight=auto_login and attempt == 0)
        except LoginNeeded as e:
            if not auto_login or attempt == 2:
                raise BotError(f"{e} '로그인' 탭에서 로그인한 뒤 다시 실행해 주세요.")
            log(f"🔑 {e} 로그인 창을 엽니다 — 열린 브라우저 창에서 로그인해 주세요.")
            if not login(log, e.sites):
                raise BotError(f"{e} 로그인 창에서 10분 안에 로그인되지 않았거나 창이 닫혀 중단했습니다. 다시 실행해 주세요.")
            log("✅ 로그인 확인 — 이어서 진행합니다.")


def _check(log=print, register=True, preflight=False):
    """지금 팀원 상태를 읽고, 대상 상태인 사람을 오늘 날짜로 팀 캘린더에 등록. 결과 dict"""
    c = config()
    st = load_state()
    today = dt.datetime.now(KST).date()
    done_keys = {(d["date"], d["name"], d["status"]) for d in st.get("done", [])}
    people, added, errors = [], [], []
    progress(3, "브라우저 준비 중 (처음이면 프로그램 설치)")
    with _playwright(log)() as p:
        ctx = _open(p, log=log)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            if preflight:  # 두 곳을 한 번에 확인해 로그인 창을 한 번만 띄운다
                progress(10, "네이버웍스·구글 로그인 확인 중")
                miss = _missing_logins(page, c)
                if miss:
                    raise LoginNeeded(miss)
            progress(30, "네이버웍스 주소록에서 팀원 상태 읽는 중")
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
            progress(55, f"구글 캘린더 오늘 일정 확인 중 (등록 대상 {len(todo)}명)")
            existing = day_events(page, today) if todo else []
            for i, x in enumerate(todo):
                progress(65 + 30 * i / max(len(todo), 1), f"캘린더 등록 중 ({i + 1}/{len(todo)}) {x['name']}")
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
                except LoginNeeded:
                    raise
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
