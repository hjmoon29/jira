#!/usr/bin/env python3
"""헬스케어 예약: 사내 포털(eMate) 헬스케어 예약시스템의 주간 화면을 읽어 비어 있는 가장 빠른 시간을 찾고,
화면의 '예약가능' 버튼과 같은 흐름(예약 팝업 → 예약신청)으로 예약한다.

포털 로그인은 NETSPASS 추가 인증이 있어 자동으로 못 한다. '로그인' 기능으로 창을 띄워 직접 로그인하면
포털 세션 쿠키(LtpaToken)를 COOKIES 에 저장해 두고, 조회·예약은 그 쿠키로 화면 없이 접속한다. 만료되면 다시 로그인.
"""
import datetime as dt
import json, os, platform, time

BASE = "https://portal.coway.com/emate-k15_02/help_care.nsf/"
WEEK = BASE + "ByWeek?openview&option=&skin=skin01&keydate="
PROFILE = os.environ.get("MEETING_ROOM_PROFILE", os.path.join(os.path.expanduser("~"), ".playwright-edge-profile"))
COOKIES = os.path.join(os.path.expanduser("~"), ".config", "work-bot", "healthcare_cookies.json")
CHANNEL = "msedge" if platform.system() == "Windows" else "chrome"
SLOTS = (-1, 0, 1, 2, 3, 4)  # 화면의 시간 번호. 5(17:10)는 화면에서도 예약 버튼이 없다
LIMIT = {"week": 1, "month": 2, "year": 24}
LIMIT_NAME = {"week": "주 1회", "month": "월 2회", "year": "연 24회"}
SEX_NAME = {"남": "남성 키퍼", "여": "여성 키퍼"}
# 주간 화면에서 읽는 값 (화면 스크립트 전역 변수)
READ = """() => ({dates: Catalogue1.split(";"), rdate: rdate, cnt: personcnt, after2week: after2week, me: myname,
  meName: myirum, dbPath: DbPath, keeper: {"남": g_keeper[0], "여": g_keeper[1]},
  times: Object.fromEntries([-1, 0, 1, 2, 3, 4, 5].map(i => [i, g_time[i]]))})"""


class BotError(Exception):
    pass


def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise BotError("Playwright 가 설치돼 있지 않습니다. 명령 창에서 아래를 실행하세요.\n"
                       "  py -3 -m pip install playwright  (Mac: python3 -m pip install playwright)")
    return sync_playwright


def _save_cookies(ctx):
    os.makedirs(os.path.dirname(COOKIES), exist_ok=True)
    keep = [c for c in ctx.cookies() if c["domain"].lstrip(".").endswith("coway.com") and "nsso" not in c["domain"]]
    with open(COOKIES, "w", encoding="utf-8") as f:
        json.dump(keep, f)
    try:
        os.chmod(COOKIES, 0o600)
    except OSError:
        pass


def login(log=print):
    """창을 띄워 사용자가 직접 포털 로그인(추가 인증 포함)하게 하고, 예약 화면이 열리면 쿠키를 저장한다."""
    with _playwright()() as p:
        try:
            ctx = p.chromium.launch_persistent_context(PROFILE, channel=CHANNEL, headless=False, locale="ko-KR",
                                                       viewport={"width": 1280, "height": 900})
        except Exception as e:
            if "lock" in str(e).lower() or "already" in str(e).lower():
                raise BotError("예약용 브라우저가 이미 열려 있습니다(회의실 예약 등). 그 창을 닫고 다시 시도하세요.")
            raise BotError(f"브라우저를 열지 못했습니다: {e}")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(WEEK + f"{dt.date.today():%Y-%m-%d}")
        log("열린 창에서 포털 로그인(추가 인증 포함)을 해 주세요 (최대 5분 대기)")
        end = time.time() + 300
        while time.time() < end:
            time.sleep(2)
            if any(c["name"] == "LtpaToken" for c in ctx.cookies()):
                time.sleep(2)
                page.goto(WEEK + f"{dt.date.today():%Y-%m-%d}", wait_until="networkidle", timeout=60000)
                if "help_care.nsf" in page.url:
                    _save_cookies(ctx)
                    ctx.close()
                    return True
        ctx.close()
    return False


class Session:
    """저장한 포털 쿠키로 화면 없는 브라우저를 연다."""

    def __enter__(self):
        if not os.path.isfile(COOKIES):
            raise BotError("헬스케어 로그인이 필요합니다. '로그인' 탭에서 한 번 로그인해 주세요.")
        self._pw = _playwright()().start()
        try:
            self.browser = self._pw.chromium.launch(channel=CHANNEL, headless=True)
        except Exception as e:
            self._pw.stop()
            raise BotError(f"브라우저를 열지 못했습니다: {e}")
        self.ctx = self.browser.new_context(locale="ko-KR")
        with open(COOKIES, encoding="utf-8") as f:
            self.ctx.add_cookies(json.load(f))
        self.page = self.ctx.new_page()
        self.dialogs = []
        self.ctx.on("page", lambda pg: pg.on("dialog", self._dialog))
        self.page.on("dialog", self._dialog)
        return self

    def _dialog(self, d):
        self.dialogs.append(d.message)
        d.accept()

    def __exit__(self, *a):
        try:
            _save_cookies(self.ctx)  # 포털이 세션을 연장해 주면 그대로 이어 쓴다
        finally:
            self.browser.close()
            self._pw.stop()

    def week(self, day):
        """keydate=day 부터 7일 화면을 읽는다."""
        self.page.goto(WEEK + f"{day:%Y-%m-%d}", wait_until="networkidle", timeout=60000)
        if "help_care.nsf" not in self.page.url:
            raise BotError("헬스케어 로그인이 만료됐습니다. '로그인' 탭에서 다시 로그인해 주세요.")
        try:
            return self.page.evaluate(READ)
        except Exception:
            raise BotError("헬스케어 예약 화면을 읽지 못했습니다(화면 구조가 바뀌었을 수 있음).")


def _parse_week(w, now):
    """주간 화면 값 → 칸 목록. 칸 = {date, num, sex, time, keeper, state, block}"""
    cells, mine = [], []
    perm = w["me"][:5] in ("02200", "02t00", "02t15")
    for i, day in enumerate(w["dates"]):
        d = dt.date.fromisoformat(day)
        if d.weekday() >= 5 or day > w["after2week"]:
            continue
        taken = {}
        for e in filter(None, (w["rdate"][i] or "").split(";")):
            v = e.split("^")
            if len(v) >= 5 and v[0] != "":
                taken.setdefault((int(v[0]), v[1]), v)
        over = [k for k in ("week", "month", "year") if (w["cnt"].get(k) or {}).get(day, 0) >= LIMIT[k]]
        for num in SLOTS:
            hhmm = w["times"][str(num)].replace(" ", "")
            start = dt.datetime.combine(d, dt.time.fromisoformat(hhmm.split("~")[0]))
            for sex in ("남", "여"):
                c = {"date": d, "num": num, "sex": sex, "time": hhmm.replace("~", " ~ "), "keeper": w["keeper"][sex],
                     "start": start, "state": "free", "block": ""}
                v = taken.get((num, sex))
                if v and v[2] == w["me"]:
                    c["state"] = "mine"
                    mine.append(c)
                elif v:
                    c["state"] = "taken" if v[4] == "N" else v[4]  # 휴가·오전반차·오후반차 등
                elif start <= now:
                    c["state"] = "past"
                elif not perm:
                    c["block"] = "예약 권한 없음"
                elif over:
                    c["block"] = " · ".join(LIMIT_NAME[k] for k in over) + " 한도"
                cells.append(c)
    return cells, mine


def scan(s, sex="", period="", log=print):
    """오늘부터 예약 가능 기간(2주)까지 비어 있는 칸을 시간 순으로 모은다."""
    now = dt.datetime.now()
    day, cells, mine, w = now.date(), [], [], None
    while True:
        w = s.week(day)
        c, m = _parse_week(w, now)
        cells += c
        mine += m
        day += dt.timedelta(days=7)
        if f"{day:%Y-%m-%d}" > w["after2week"]:
            break
    log(f"{now:%m/%d}~{w['after2week'][5:].replace('-', '/')} 예약 현황을 읽었습니다 (예약 가능 기간: 2주)")
    free = [c for c in cells if c["state"] == "free" and (not sex or c["sex"] == sex)
            and (period != "오전" or c["start"].hour < 12) and (period != "오후" or c["start"].hour >= 12)]
    free.sort(key=lambda c: (c["start"], c["sex"]))
    return free, mine, w


def label(c):
    return f"{c['date']:%m/%d}({'월화수목금토일'[c['date'].weekday()]}) {c['time']} · {c['keeper']}"


def book(q, log=print):
    """조건에 맞는 첫 빈칸(희망 일자·시간이 있으면 그 중 첫 칸)을 예약하고, 화면에 내 이름이 들어갔는지 확인한다."""
    sex, period = _sex(q), q.get("period", "")
    want_date = (q.get("date") or "").strip()
    want_time = q.get("slot", "")
    with Session() as s:
        free, mine, w = scan(s, sex, period, log=log)
        if want_date:
            free = [c for c in free if f"{c['date']:%Y-%m-%d}" == want_date]
        if want_time and want_time != "가장 빠른 시간":
            free = [c for c in free if c["time"].startswith(want_time)]
        ok = [c for c in free if not c["block"]]
        if not ok:
            why = f" (빈 시간은 있으나 {free[0]['block']})" if free else ""
            raise BotError("조건에 맞는 예약 가능한 시간이 없습니다" + why)
        c = ok[0]
        log(f"예약 시도: {label(c)}")
        s.week(c["date"])  # 예약 팝업은 그 날짜가 보이는 주간 화면에서 연다(팝업이 opener 의 횟수 정보를 확인함)
        url = f"/{w['dbPath']}/entry?OpenForm&form=헬스케어&caredate={c['date']:%Y-%m-%d}&carenum={c['num']}&caresex={c['sex']}"
        n = len(s.dialogs)
        with s.ctx.expect_page(timeout=20000) as pinfo:
            s.page.evaluate("u => openpage(u)", url)
        pop = pinfo.value
        pop.wait_for_load_state("networkidle")
        time.sleep(1)
        if pop.is_closed() or s.dialogs[n:]:
            raise BotError("예약 창이 열리지 않았습니다 — " + (" / ".join(s.dialogs[n:]) or "원인 불명"))
        if not pop.evaluate("() => !!document.forms[0] && typeof savedoc === 'function' && !!$('input[name=userTel]').val()"):
            raise BotError("예약 창의 입력 양식을 찾지 못했습니다(연락처가 비어 있거나 화면 구조가 바뀜). 포털에서 직접 예약해 주세요.")
        try:
            with pop.expect_navigation(timeout=20000):
                pop.evaluate("() => savedoc()")
        except Exception:
            pass  # 제출 후 창이 바로 닫히는 경우
        if s.dialogs[n:]:
            log("포털 안내: " + " / ".join(s.dialogs[n:]))
        for _ in range(5):  # 저장이 반영됐는지 주간 화면으로 확인
            time.sleep(2)
            _, mine2 = _parse_week(s.week(c["date"]), dt.datetime.now() - dt.timedelta(days=1))
            if any(m["date"] == c["date"] and m["num"] == c["num"] and m["sex"] == c["sex"] for m in mine2):
                return c, True
        return c, False


def _sex(q):
    k = q.get("keeper", "")
    return "남" if k.startswith("남") else "여" if k.startswith("여") else ""
