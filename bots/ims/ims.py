#!/usr/bin/env python3
"""IoT IMS(IoCare 통합관리시스템) 조회.

IMS 로그인은 사내 SSO(NETSPASS 추가 인증)라 자동으로 못 한다. '로그인' 기능으로 IMS 전용 브라우저 프로필(PROFILE)에
직접 로그인해 두면, 조회는 그 프로필을 화면 없이 열어 IMS 화면이 스스로 보내는 요청의 인증 헤더를 그대로 써서
API 를 부른다(토큰을 따로 꺼내 저장하지 않는다). IMS 세션이 끝나면 다시 로그인해야 한다.
"""
import json, os, platform, time

ENVS = {"개발": "https://ims.iot.coway.dev", "검증": "https://ims.iot.coway.blue", "운영": "https://ims.iot.coway.com"}
PREFIX = {"api": "/api", "bo": "/bo-api", "export": "/export"}
PROFILE = os.environ.get("IMS_PROFILE", os.path.join(os.path.expanduser("~"), ".playwright-ims-profile"))
CHANNEL = "msedge" if platform.system() == "Windows" else "chrome"
# 화면 없는 브라우저 기본 UA(HeadlessChrome)는 IMS 앞단(CloudFront)에서 막힌다
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


class BotError(Exception):
    pass


def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise BotError("Playwright 가 설치돼 있지 않습니다. 명령 창에서 아래를 실행하세요.\n"
                       "  py -3 -m pip install playwright  (Mac: python3 -m pip install playwright)")
    return sync_playwright


def _open(p, headless):
    try:
        return p.chromium.launch_persistent_context(PROFILE, channel=CHANNEL, headless=headless, locale="ko-KR",
                                                    user_agent=UA, viewport={"width": 1280, "height": 900})
    except Exception as e:
        if "lock" in str(e).lower() or "already" in str(e).lower():
            raise BotError("IMS 브라우저가 이미 열려 있습니다(로그인 창 등). 그 창을 닫고 다시 시도하세요.")
        raise BotError(f"브라우저를 열지 못했습니다: {e}")


def _logged_in(ctx, base):
    return any(c["name"] == "accessToken" for c in ctx.cookies(base))


def login(env="개발", log=print, wait=300):
    """창을 띄워 사용자가 직접 IMS 에 로그인하게 한다. 로그인 후 IMS 화면이 열리면 닫는다."""
    base = ENVS[env]
    with _playwright()() as p:
        ctx = _open(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(base + "/dashboard/siteMap")
        log(f"열린 창에서 IMS({env}) 로그인(추가 인증 포함)을 해 주세요 (최대 {wait // 60}분 대기)")
        end = time.time() + wait
        while time.time() < end:
            time.sleep(2)
            if _logged_in(ctx, base) and page.url.startswith(base) and "/login" not in page.url:
                time.sleep(3)
                ctx.close()
                return True
        ctx.close()
    return False


class Session:
    """IMS 프로필을 화면 없이 열고, IMS 화면이 보내는 요청에서 인증 헤더를 받아 API 를 부른다."""

    def __init__(self, env="개발"):
        if env not in ENVS:
            raise BotError(f"알 수 없는 환경: {env}")
        self.env, self.base = env, ENVS[env]
        self.auth = None

    def __enter__(self):
        self._pw = _playwright()().start()
        self.ctx = None
        try:
            self.ctx = _open(self._pw, headless=True)
            if not _logged_in(self.ctx, self.base):
                raise BotError(f"IMS({self.env}) 로그인이 필요합니다. '로그인' 탭에서 로그인해 주세요.")
            page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()

            def grab(req):
                h = req.headers.get("authorization", "")
                if h.startswith("Bearer ") and req.url.startswith(self.base):
                    self.auth = h
            page.on("request", grab)
            page.goto(self.base + "/dashboard/siteMap", wait_until="networkidle", timeout=60000)
            end = time.time() + 20
            while not self.auth and time.time() < end:
                page.wait_for_timeout(500)
            if "/login" in page.url or not self.auth:
                raise BotError(f"IMS({self.env}) 로그인이 만료됐습니다. '로그인' 탭에서 다시 로그인해 주세요.")
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *a):
        try:
            if self.ctx:
                self.ctx.close()
        finally:
            self._pw.stop()

    def call(self, method, base, path, body=None, params=None, raw=False):
        url = self.base + PREFIX[base] + path
        kw = {"headers": {"Authorization": self.auth, "langCode": "ko", "Content-Type": "application/json"},
              "timeout": 120000}
        if params:
            kw["params"] = {k: v for k, v in params.items() if v not in (None, "")}
        if body is not None:
            kw["data"] = json.dumps(body, ensure_ascii=False)
        r = self.ctx.request.fetch(url, method=method.upper(), **kw)
        if r.status == 401:
            raise BotError(f"IMS({self.env}) 로그인이 만료됐습니다. '로그인' 탭에서 다시 로그인해 주세요.")
        if not r.ok:
            txt = r.text()
            try:
                j = json.loads(txt)
                err = j.get("error", j)
                msg = err.get("message", txt) if isinstance(err, dict) else err
            except ValueError:
                msg = txt[:300]
            raise BotError(f"IMS 오류 {r.status} ({path}): {msg}")
        return r.body() if raw else (r.json() if r.body() else None)
