#!/usr/bin/env python3
"""IoT업무봇 인증 설정 (Mac·Windows 공용):  python3 auth_setup.py  (Windows: py -3 auth_setup.py)

내 할 일·기획 사양 정리·QA 대응 봇이 읽는 자료의 인증을 이 PC에 저장한다. 값은 모두 저장소 밖(이 PC 전용)에 둔다.
1. 위키 개인 액세스 토큰 → ~/.config/work-bot/credentials.json 의 "wiki_token"
2. Google 로그인 한 번으로 메일 읽기·보내기 + Drive 읽기 권한을 받는다
   - 메일: ~/.config/claude-daily/gmail.json 의 refresh_token 을 새 권한으로 바꿈(토큰 사용량 메일 발송도 계속 동작)
   - Drive: ~/.config/work-bot/gdrive_token.json · gdrive_oauth.json 을 만들고 credentials.json 에 경로 기록
   OAuth 클라이언트(client_id/secret)는 gmail.json 에 있는 것을 그대로 쓴다.
"""
import getpass, hashlib, base64, http.server, json, os, secrets, shutil, sys, threading, time, urllib.error, urllib.parse, urllib.request, webbrowser

HOME = os.path.expanduser("~")
WB = os.path.join(HOME, ".config", "work-bot")
CRED = os.path.join(WB, "credentials.json")
GMAIL = os.path.join(HOME, ".config", "claude-daily", "gmail.json")
WIKI = "https://wiki.coway.com"
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send",
          "https://www.googleapis.com/auth/drive.readonly"]
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 회사 프록시가 인증 헤더를 바꾸지 않게


def step(msg):
    print("\n▶ " + msg)


def jload(p, default):
    try:
        return json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def jsave(p, d):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    if os.name != "nt":
        os.chmod(p, 0o600)


def call(method, url, headers=None, data=None):
    req = urllib.request.Request(url, data, method=method, headers=headers or {})
    try:
        with OPENER.open(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode(errors="replace")[:400]}


# ---------- 1. 위키 ----------
def wiki():
    step("위키 개인 액세스 토큰")
    c = jload(CRED, {})
    if c.get("wiki_token"):
        st, me = call("GET", WIKI + "/rest/api/user/current", {"Authorization": "Bearer " + c["wiki_token"]})
        if st == 200 and me.get("type") != "anonymous":
            print(f"  이미 있음 — {me.get('displayName', '')} 로 확인됨")
            return
        print("  저장된 토큰이 동작하지 않습니다. 새로 넣어 주세요.")
    print(f"  {WIKI} → 오른쪽 위 프로필 → 설정 → 개인 액세스 토큰 → 토큰 만들기 에서 발급한 값을 붙여 넣으세요 (화면에 안 보임).")
    print("  건너뛰려면 그냥 Enter.")
    t = getpass.getpass("  토큰: ").strip()
    if not t:
        print("  건너뜀")
        return
    st, me = call("GET", WIKI + "/rest/api/user/current", {"Authorization": "Bearer " + t})
    if st != 200 or me.get("type") == "anonymous":
        print(f"  위키 확인 실패({st}) — 토큰을 다시 확인해 주세요. 저장하지 않았습니다.")
        return
    c = jload(CRED, {})
    c["wiki_token"] = t
    jsave(CRED, c)
    print(f"  저장함 — {me.get('displayName', '')}")


# ---------- 2. Google (메일 + Drive) ----------
def google():
    step("Google 로그인 (메일 읽기·보내기 + Drive 읽기)")
    g = jload(GMAIL, {})
    if not (g.get("client_id") and g.get("client_secret")):
        print(f"  OAuth 클라이언트 정보가 없습니다: {GMAIL} — 문효준님께 문의하세요.")
        return
    code_box = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" not in q and "error" not in q:
                self.send_response(404); self.end_headers(); return
            code_box.update({k: v[0] for k, v in q.items()})
            ok = "code" in q
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(("<h2>" + ("로그인 완료. 이 창을 닫고 터미널로 돌아가세요." if ok else "로그인이 취소되었습니다: " + q["error"][0]) + "</h2>").encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    redirect = f"http://127.0.0.1:{srv.server_port}"
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": g["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": " ".join(SCOPES),
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256", "login_hint": g.get("to", "")})
    threading.Thread(target=lambda: [srv.handle_request() for _ in range(5) if not code_box], daemon=True).start()
    print("  브라우저에서 회사 Google 계정으로 로그인하고 '허용'을 눌러 주세요. (5분 안에)")
    print("  창이 안 열리면 이 주소를 브라우저에 붙여 넣으세요:\n  " + url)
    webbrowser.open(url)
    t0 = time.time()
    while not code_box and time.time() - t0 < 300:
        time.sleep(0.5)
    if code_box.get("state") != state or "code" not in code_box:
        print("  로그인을 받지 못했습니다: " + (code_box.get("error") or "시간 초과"))
        return
    st, tok = call("POST", "https://oauth2.googleapis.com/token", {"Content-Type": "application/x-www-form-urlencoded"},
                   urllib.parse.urlencode({"client_id": g["client_id"], "client_secret": g["client_secret"], "code": code_box["code"],
                                           "code_verifier": verifier, "redirect_uri": redirect, "grant_type": "authorization_code"}).encode())
    if st != 200 or not tok.get("refresh_token"):
        print(f"  토큰 발급 실패({st}): {tok}")
        return
    granted = set(tok.get("scope", "").split())
    auth = {"Authorization": "Bearer " + tok["access_token"]}

    # 메일: 기존 gmail.json 은 백업하고 refresh_token 만 바꾼다
    if {SCOPES[0], SCOPES[1]} <= granted:
        st, prof = call("GET", "https://gmail.googleapis.com/gmail/v1/users/me/profile", auth)
        if st == 200:
            shutil.copy2(GMAIL, GMAIL + f".bak-{time.strftime('%Y%m%d%H%M%S')}")
            g["refresh_token"] = tok["refresh_token"]
            jsave(GMAIL, g)
            print(f"  메일: 저장함 — {prof.get('emailAddress', '')} (읽기·보내기)")
        else:
            print(f"  메일: 확인 실패({st}) {prof.get('error', '')[:200]}")
    else:
        print("  메일: 권한이 허용되지 않았습니다 (동의 화면에서 메일 항목을 체크해 주세요)")

    # Drive
    if SCOPES[2] in granted:
        st, about = call("GET", "https://www.googleapis.com/drive/v3/about?fields=user(emailAddress)", auth)
        if st == 200:
            tp, op = os.path.join(WB, "gdrive_token.json"), os.path.join(WB, "gdrive_oauth.json")
            jsave(tp, {"refresh_token": tok["refresh_token"]})
            jsave(op, {"installed": {"client_id": g["client_id"], "client_secret": g["client_secret"]}})
            c = jload(CRED, {})
            c.update(gdrive_credentials=tp, gdrive_oauth=op)
            jsave(CRED, c)
            print(f"  Drive: 저장함 — {about.get('user', {}).get('emailAddress', '')} (읽기)")
        else:
            print(f"  Drive: 확인 실패({st}) {about.get('error', '')[:300]}")
    else:
        print("  Drive: 권한이 허용되지 않았습니다 (동의 화면에서 Drive 항목을 체크해 주세요)")


if __name__ == "__main__":
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    if only in ("", "wiki"):
        wiki()
    if only in ("", "google"):
        google()
    print("\n끝. IoT업무봇 화면에서 다시 실행해 보세요.")
    if not sys.stdin.isatty():
        sys.exit(0)
    input("Enter 를 누르면 창이 닫힙니다.")
