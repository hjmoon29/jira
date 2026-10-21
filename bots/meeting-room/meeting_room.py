#!/usr/bin/env python3
"""회의실 자동 예약: gapps.coway.com 에서 회의실 목록·빈 시간을 읽고, 조건에 맞는 첫 회의실을
Google Calendar 일정(회의실을 참석자로 추가)으로 예약한다. gapps 도 구글 로그인 사용자는 같은 방식으로 예약한다.

브라우저는 Playwright 로 띄우고, 로그인 상태는 PROFILE 폴더에 남긴다(처음 한 번 '로그인' 기능으로 직접 로그인).
"""
import datetime as dt
import json, os, platform, re, time
from urllib.parse import urlencode

GAPPS = "https://gapps.coway.com/"
PROFILE = os.environ.get("MEETING_ROOM_PROFILE", os.path.join(os.path.expanduser("~"), ".playwright-edge-profile"))
CHANNEL = "msedge" if platform.system() == "Windows" else "chrome"
KST = dt.timezone(dt.timedelta(hours=9))
SKIP_NAMES = ("스튜디오",)  # 회의용이 아닌 방


class BotError(Exception):
    pass


def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise BotError("Playwright 가 설치돼 있지 않습니다. 명령 창에서 아래를 실행하세요.\n"
                       "  py -3 -m pip install playwright  (Mac: python3 -m pip install playwright)")
    return sync_playwright


def _open(p, headless=True):
    try:
        return p.chromium.launch_persistent_context(PROFILE, channel=CHANNEL, headless=headless, locale="ko-KR",
                                                    viewport={"width": 1280, "height": 900})
    except Exception as e:
        if "lock" in str(e).lower() or "already" in str(e).lower():
            raise BotError("예약용 브라우저가 이미 열려 있습니다. 그 창을 닫고 다시 시도하세요.")
        raise BotError(f"브라우저를 열지 못했습니다: {e}")


def login(log=print):
    """창을 띄워 사용자가 직접 구글 로그인하게 한다. 로그인되면 창을 닫는다."""
    with _playwright()() as p:
        ctx = _open(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(GAPPS)
        log("열린 창에서 구글 로그인을 해 주세요 (최대 5분 대기)")
        end = time.time() + 300
        while time.time() < end:
            if _logged_in(page.url):
                page.wait_for_load_state("networkidle")
                ctx.close()
                return True
            time.sleep(2)
        ctx.close()
    return False


def _logged_in(url):
    return url.startswith(GAPPS) and "googleLogin" not in url


def _user(page):
    page.goto(GAPPS, wait_until="networkidle")
    if not _logged_in(page.url):
        raise BotError("gapps 로그인이 필요합니다. '로그인' 탭에서 한 번 로그인해 주세요.")
    m = re.search(r"var jsonData = (\{.*?\});\s*\n", page.content())
    if not m:
        raise BotError("gapps 화면에서 회의실 목록을 찾지 못했습니다(화면 구조가 바뀌었을 수 있음).")
    return json.loads(m.group(1))["user"]


def _busy(page, emails, day):
    r = page.request.get(GAPPS + "schedule/floor/reservationAjax.json",
                         params={"calendarId": ",".join(emails), "date": day.strftime("%Y/%m/%d"), "timeOffset": "-540"})
    if not r.ok:
        raise BotError(f"회의실 예약 현황을 읽지 못했습니다 (HTTP {r.status})")
    return r.json().get("result") or {}


def _refresh(page, email):
    # gapps 가 예약 직후 하는 것처럼 회의실 캐시를 새로 고친다
    page.request.get(GAPPS + "schedule/updateDataStore.json", params={"resourceEmail": email})


def _overlaps(events, start, end):
    s, e = start.timestamp() * 1000, end.timestamp() * 1000
    return [x for x in events if x.get("status") != "declined" and x["startTime"] < e and x["endTime"] > s]


def _candidates(user, people):
    loc = user.get("location") or {}
    building = loc.get("buildingId") or "G타워"
    home = loc.get("floorName")
    home = int(home[0] if isinstance(home, list) else home) if home else 0
    rooms = [r for r in user["resources"] if r["buildingId"] == building and (r.get("capacity") or 0) >= people
             and not any(k in r["name"] for k in SKIP_NAMES)]
    # 내 층에서 가까운 층 → 인원에 딱 맞는 크기 → 이름 순
    rooms.sort(key=lambda r: (abs(int(r["floorName"]) - home) if r["floorName"].isdigit() else 99,
                              r["capacity"] - people, r["name"]))
    return building, rooms


def label(r):
    return f"{r['buildingId']}-{r['floorName']}층-{r['name']} ({r['capacity']}인)"


def parse(q):
    try:
        day = dt.date.fromisoformat(q.get("date") or "")
        sh, sm = map(int, (q.get("start") or "").split(":"))
        eh, em = map(int, (q.get("end") or "").split(":"))
        people = int(str(q.get("people") or "").strip().rstrip("명"))
    except ValueError:
        raise BotError("일자·시작·종료 시간·참석 인원을 확인해 주세요 (인원은 숫자)")
    start = dt.datetime(day.year, day.month, day.day, sh, sm, tzinfo=KST)
    end = dt.datetime(day.year, day.month, day.day, eh, em, tzinfo=KST)
    if end <= start:
        raise BotError("종료 시간이 시작 시간보다 늦어야 합니다")
    if start < dt.datetime.now(KST) - dt.timedelta(minutes=10):
        raise BotError("이미 지난 시간은 예약할 수 없습니다")
    if people < 1:
        raise BotError("참석 인원은 1명 이상이어야 합니다")
    title = (q.get("title") or "").strip() or "IoT기획 업무 미팅"
    return start, end, people, title


def find(page, start, end, people, log=print):
    """빈 회의실 후보 목록(우선순위 순)과 사용자 정보"""
    user = _user(page)
    building, rooms = _candidates(user, people)
    if not rooms:
        raise BotError(f"{building}에 {people}명 이상 들어가는 회의실이 없습니다")
    busy = _busy(page, [r["email"] for r in rooms], start.date())
    free = [r for r in rooms if not _overlaps(busy.get(r["email"], []), start, end)]
    log(f"{building} {people}명 이상 회의실 {len(rooms)}개 중 빈 곳 {len(free)}개")
    return user, building, rooms, free


def _save_event(page, room, start, end, title):
    fmt = "%Y%m%dT%H%M%SZ"
    url = "https://calendar.google.com/calendar/r/eventedit?" + urlencode({
        "text": title,
        "dates": f"{start.astimezone(dt.timezone.utc):{fmt}}/{end.astimezone(dt.timezone.utc):{fmt}}",
        "add": room["email"],
    })
    page.goto(url, wait_until="networkidle")
    if "accounts.google.com" in page.url:
        raise BotError("Google 캘린더 로그인이 필요합니다. '로그인' 탭에서 다시 로그인해 주세요.")
    save = page.locator("button[jsname='x8hlje']")
    if not save.count():
        save = page.get_by_role("button", name=re.compile(r"^(저장|Save)$"))
    save.first.click()
    end_wait = time.time() + 20
    while time.time() < end_wait:
        if "eventedit" not in page.url:
            return
        # 저장 뒤 확인 창(초대 보내기 등)이 뜨면 기본 버튼으로 넘긴다
        for name in ("보내기", "Send", "저장", "Save", "확인", "OK"):
            b = page.locator("div[role='dialog'], div[role='alertdialog']").get_by_role("button", name=name)
            if b.count() and b.first.is_visible():
                b.first.click()
                break
        time.sleep(1)
    raise BotError("Google 캘린더에서 저장이 끝나지 않았습니다. 캘린더에서 일정이 만들어졌는지 확인해 주세요.")


def book(q, log=print):
    start, end, people, title = parse(q)
    with _playwright()() as p:
        ctx = _open(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            user, building, rooms, free = find(page, start, end, people, log)
            if not free:
                raise BotError(f"{start:%m/%d %H:%M}~{end:%H:%M} 에 {building} {people}명 이상 회의실이 모두 찼습니다")
            for room in free[:3]:
                # 목록이 캐시일 수 있어 예약 직전에 그 방만 새로 고쳐 다시 확인
                _refresh(page, room["email"])
                time.sleep(1)
                if _overlaps(_busy(page, [room["email"]], start.date()).get(room["email"], []), start, end):
                    log(f"{label(room)}: 방금 예약이 생겨 다음 회의실로")
                    continue
                log(f"{label(room)} 예약 중…")
                _save_event(page, room, start, end, title)
                status = _verify(page, room, start, end, title)
                return room, status, start, end, title
            raise BotError("빈 회의실이 연달아 먼저 예약됐습니다. 잠시 뒤 다시 시도해 주세요")
        finally:
            ctx.close()


def _verify(page, room, start, end, title):
    for _ in range(4):
        _refresh(page, room["email"])
        time.sleep(2)
        s, e = start.timestamp() * 1000, end.timestamp() * 1000  # 거절(declined)된 내 일정도 찾아야 하므로 _overlaps 를 쓰지 않음
        mine = [x for x in _busy(page, [room["email"]], start.date()).get(room["email"], [])
                if x["startTime"] < e and x["endTime"] > s and x.get("summary", "").startswith(title)]
        if mine:
            return mine[0].get("status") or "accepted"
    return "unknown"


def free_rooms(q, log=print):
    start, end, people, _ = parse(q)
    with _playwright()() as p:
        ctx = _open(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            return find(page, start, end, people, log)[3], start, end
        finally:
            ctx.close()
