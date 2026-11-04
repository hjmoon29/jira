#!/usr/bin/env python3
"""내 할 일 봇: 메일·Jira·위키·Google Drive(지정 폴더)·업무메모에서 '내가 해야 할 일'을 찾아
언제까지 / 무엇을 / 누가 / 어떻게 시켰는지 정리한다. 화면 실행 + 매일 아침 워드 문서(다운로드 폴더) 자동 저장(메일은 선택).

증분 방식: 소스마다 마지막으로 읽은 시각을 state.json 에 남기고, 그 뒤에 새로 오거나 수정된 것만 읽는다.
  - Drive: 파일별 마지막 modifiedTime + 읽은 글자 사본(cache/) → 다시 수정되면 바뀐 줄만 Claude에 넘김
  - 찾은 할 일은 tasks.json 에 누적. 새 자료로 기한·내용이 바뀌거나 완료가 보이면 Claude가 갱신
업무메모의 계정·비밀번호 구역(<…비번…> 등)과 비밀번호·카드번호처럼 보이는 줄은 읽기 단계에서 버린다.
"""
import base64, datetime, difflib, glob, html, json, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "spec-doc"), os.path.join(HERE, "..", "jira-tasks")]
import spec_doc as sd  # noqa: E402  위키·Drive 호출, Claude 실행
import jira_tasks as jt  # noqa: E402  Jira 호출

BotError = sd.BotError
DIR = os.path.join(sd.RUNS, "my-tasks")
STATE = os.path.join(DIR, "state.json")
TASKS = os.path.join(DIR, "tasks.json")
CACHE = os.path.join(DIR, "cache")
LOCK = os.path.join(DIR, "run.lock")
CONFIG = os.path.join(HERE, "config.json")
GMAIL_CONF = os.path.expanduser("~/.config/claude-daily/gmail.json")
FIRST_DAYS = 7          # 처음 실행 때 거슬러 읽는 기간
CAP = {"mail": 3000, "jira": 4000, "wiki": 5000, "drive": 6000, "memo": 8000, "total": 700_000}  # total: 1M 컨텍스트 모델 기준
SECRET_HEAD = re.compile(r"비번|비밀번호|패스워드|password|계정|카드|cvc|계좌|otp", re.I)
SECRET_LINE = re.compile(r"(비번|비밀번호|패스워드|pass(word)?|pw\b|cvc|카드\s*번호|계좌)\s*[:=]?|\d{4}[- ]?\d{4}[- ]?\d{4}|\d{8,}", re.I)


def log_default(m):
    print(m, file=sys.stderr)


def cfg():
    c = {"drive_folder": "", "mail_time": "08:10", "mail_to": "", "mail_on": False, "lookback_days": FIRST_DAYS, "model": ""}
    if os.path.isfile(CONFIG):
        c.update(json.load(open(CONFIG, encoding="utf-8")))
    return c


def save_cfg(c):
    tmp = CONFIG + ".tmp"
    json.dump(c, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG)


def jload(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def jsave(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    json.dump(obj, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def clip(s, n):
    s = re.sub(r"\n{3,}", "\n\n", (s or "").strip())
    return s if len(s) <= n else s[:n] + " …(생략)"


def since_dt(state, key, days):
    v = state.get("last", {}).get(key)
    if v:
        return datetime.datetime.fromisoformat(v)
    return datetime.datetime.now() - datetime.timedelta(days=days)


# ---------- 나 ----------
def whoami():
    me = {"name": "", "wiki_user": "", "jira_user": "", "email": ""}
    try:
        w = sd.wiki("GET", "/user/current")
        me["wiki_user"] = w.get("username", "")
        me["name"] = re.sub(r"\[.*$", "", w.get("displayName", "")).strip()
    except Exception:
        pass
    try:
        j = jt.call("GET", "/myself")
        me["jira_user"] = j.get("name", "")
        me["email"] = j.get("emailAddress", "")
        me["name"] = me["name"] or re.sub(r"\[.*$", "", j.get("displayName", "")).strip()
    except Exception:
        pass
    return me


# ---------- 메일 (Gmail) ----------
GTOK = None


def gmail(path, params=None):
    global GTOK
    if not GTOK:
        if not os.path.isfile(GMAIL_CONF):
            raise BotError(f"메일 설정이 없습니다: {GMAIL_CONF}")
        c = json.load(open(GMAIL_CONF, encoding="utf-8"))
        body = sd.urllib.parse.urlencode({"client_id": c["client_id"], "client_secret": c["client_secret"],
                                          "refresh_token": c["refresh_token"], "grant_type": "refresh_token"}).encode()
        GTOK = sd.http("POST", "https://oauth2.googleapis.com/token", {"Content-Type": "application/x-www-form-urlencoded"}, body)["access_token"]
    return sd.http("GET", "https://gmail.googleapis.com/gmail/v1/users/me" + path, {"Authorization": f"Bearer {GTOK}"}, params=params)


def mail_body(payload):
    """본문 텍스트(plain 우선, 없으면 html 을 글자로). 인용된 이전 메일은 잘라낸다."""
    def walk(p, want):
        if p.get("mimeType") == want and (p.get("body") or {}).get("data"):
            return base64.urlsafe_b64decode(p["body"]["data"] + "==").decode("utf-8", "replace")
        for c in p.get("parts") or []:
            t = walk(c, want)
            if t:
                return t
        return ""
    t = walk(payload, "text/plain")
    if not t:
        h = walk(payload, "text/html")
        h = re.sub(r"(?is)<(style|script)[^>]*>.*?</\1>", "", h)
        h = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", h)
        t = html.unescape(re.sub(r"<[^>]+>", " ", h))
    t = re.sub(r"[ \t ]+", " ", t)
    cut = re.search(r"(?m)^\s*(-{3,}\s*Original Message|From:\s|보낸 사람:|-----Original|On .+ wrote:|\d{4}년 \d+월 \d+일.+작성:)", t)
    if cut and cut.start() > 40:
        t = t[:cut.start()]
    return "\n".join(l for l in t.splitlines() if not l.lstrip().startswith(">"))


def src_mail(state, days, log):
    since = since_dt(state, "mail", days)
    q = f"after:{int(since.timestamp())} -category:promotions -category:social -in:chats"
    ids, tok = [], None
    while len(ids) < 300:
        p = {"q": q, "maxResults": 100, "includeSpamTrash": "false"}
        if tok:
            p["pageToken"] = tok
        r = gmail("/messages", p)
        ids += [m["id"] for m in r.get("messages", [])]
        tok = r.get("nextPageToken")
        if not tok:
            break
    out = []
    for mid in ids:
        m = gmail(f"/messages/{mid}", {"format": "full"})
        h = {x["name"].lower(): x["value"] for x in m["payload"].get("headers", [])}
        labels = m.get("labelIds", [])
        sent = "SENT" in labels
        when = datetime.datetime.fromtimestamp(int(m["internalDate"]) / 1000).strftime("%Y-%m-%d %H:%M")
        out.append({"src": "메일(보냄)" if sent else "메일", "id": f"mail:{m['threadId']}", "when": when,
                    "link": f"https://mail.google.com/mail/u/0/#all/{m['threadId']}",
                    "head": f"제목: {h.get('subject', '')}\n보낸 사람: {h.get('from', '')}\n받는 사람: {h.get('to', '')}"
                            + (f"\n참조: {h['cc']}" if h.get("cc") else ""),
                    "text": clip(mail_body(m["payload"]), CAP["mail"])})
    log(f"메일 {len(out)}통 ({since:%m/%d %H:%M} 이후)")
    return out


# ---------- Jira ----------
def src_jira(state, days, me, log):
    since = since_dt(state, "jira", days)
    s = since.strftime("%Y-%m-%d %H:%M")
    jql = f'updated >= "{s}" AND (assignee = currentUser() OR reporter = currentUser() OR watcher = currentUser()'
    jql += f' OR comment ~ "{me["jira_user"]}")' if me.get("jira_user") else ")"
    issues = jt.search(jql + " ORDER BY updated DESC", "summary,status,duedate,assignee,reporter,priority,updated,created,description,comment", 100)
    out = []
    for i in issues:
        f = i["fields"]
        nm = lambda u: (u or {}).get("displayName", "")
        cm = [c for c in (f.get("comment") or {}).get("comments", []) if c.get("updated", "")[:16].replace("T", " ") >= s] or (f.get("comment") or {}).get("comments", [])[-2:]
        text = (f"상태: {(f.get('status') or {}).get('name', '')} · 기한: {f.get('duedate') or '없음'} · 담당: {nm(f.get('assignee'))} · 보고자: {nm(f.get('reporter'))}"
                f" · 생성: {(f.get('created') or '')[:10]}\n설명: {clip(f.get('description') or '', 1500)}\n"
                + "\n".join(f"[댓글 {c['updated'][:16].replace('T', ' ')} {nm(c.get('author'))}] {clip(c.get('body', ''), 800)}" for c in cm[-5:]))
        out.append({"src": "Jira", "id": f"jira:{i['key']}", "when": (f.get("updated") or "")[:16].replace("T", " "),
                    "link": f"https://jira.coway.com/browse/{i['key']}", "head": f"{i['key']} {f.get('summary', '')}", "text": clip(text, CAP["jira"])})
    log(f"Jira {len(out)}건 ({since:%m/%d %H:%M} 이후 수정)")
    return out


# ---------- 위키 ----------
def around(text, keys, width=600):
    """긴 페이지는 내 이름·멘션 주변만"""
    if len(text) <= CAP["wiki"]:
        return text
    spans = []
    for k in [k for k in keys if k]:
        for m in re.finditer(re.escape(k), text):
            spans.append((max(0, m.start() - width), m.end() + width))
    if not spans:
        return text[:CAP["wiki"]]
    spans.sort()
    merged = [list(spans[0])]
    for a, b in spans[1:]:
        if a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return clip(" … ".join(text[a:b] for a, b in merged), CAP["wiki"])


def src_wiki(state, days, me, log):
    since = since_dt(state, "wiki", days)
    s = since.strftime("%Y/%m/%d %H:%M")
    cqls = [f'mention = currentUser() and lastmodified >= "{s}"']
    if me.get("name"):
        cqls.append(f'text ~ "{me["name"]}" and lastmodified >= "{s}" and type = page')
    seen, out = set(), []
    for cql in cqls:
        r = sd.wiki("GET", "/content/search", params={"cql": cql, "limit": 50, "expand": "body.storage,version,space"})
        for p in r.get("results", []):
            if p["id"] in seen:
                continue
            seen.add(p["id"])
            body = p.get("body", {}).get("storage", {}).get("value", "")
            if me.get("wiki_user"):  # 멘션 태그를 이름으로 보이게
                body = re.sub(r'<ri:user ri:username="%s"\s*/>' % re.escape(me["wiki_user"]), f"@{me['name']}", body)
            text = sd.storage_text(body)
            v = p.get("version", {})
            out.append({"src": "위키", "id": f"wiki:{p['id']}", "when": (v.get("when") or "")[:16].replace("T", " "),
                        "link": f"{sd.WIKI}/pages/viewpage.action?pageId={p['id']}",
                        "head": f"{p['title']} (스페이스 {p.get('space', {}).get('key', '')}, v{v.get('number', '')} 수정: {(v.get('by') or {}).get('displayName', '')})",
                        "text": around(text, [me.get("name"), "@" + me.get("name", "")])})
    log(f"위키 {len(out)}건 ({since:%m/%d %H:%M} 이후 수정, 멘션·내 이름)")
    return out


# ---------- Google Drive (지정 폴더, 바뀐 파일만) ----------
def folder_map(drive_id, refresh=False):
    path = os.path.join(DIR, "folders.json")
    m = jload(path, {})
    if m.get("drive") == drive_id and not refresh and time.time() - m.get("at", 0) < 7 * 86400:
        return m["parents"]
    par, tok = {}, None
    while True:
        p = {"corpora": "drive", "driveId": drive_id, "q": "mimeType='application/vnd.google-apps.folder' and trashed=false",
             "fields": "nextPageToken,files(id,parents)", "pageSize": 1000, "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
        if tok:
            p["pageToken"] = tok
        r = sd.google("/drive/v3/files", p)
        par.update({x["id"]: (x.get("parents") or [None])[0] for x in r["files"]})
        tok = r.get("nextPageToken")
        if not tok:
            break
    jsave(path, {"drive": drive_id, "at": time.time(), "parents": par})
    return par


def changed_lines(old, new, keys):
    """다시 수정된 문서: 추가·바뀐 줄만 (앞 줄 1개를 맥락으로). 처음 읽는 문서: 내 이름이 있는 줄과 그 위아래"""
    nl = new.splitlines()
    if old is None:
        idx = [i for i, l in enumerate(nl) if any(k and k in l for k in keys)]
        keep = sorted({j for i in idx for j in (i - 1, i, i + 1) if 0 <= j < len(nl)} | {i for i, l in enumerate(nl) if l.startswith("## 시트:")})
        return "\n".join(nl[i] for i in keep) if idx else ""
    out = []
    for op, a1, a2, b1, b2 in difflib.SequenceMatcher(None, old.splitlines(), nl, autojunk=False).get_opcodes():
        if op in ("replace", "insert"):
            if b1 > 0:
                out.append("  " + nl[b1 - 1])
            out += ["+ " + l for l in nl[b1:b2]]
    return "\n".join(out)


def src_drive(state, days, me, conf, log):
    u = (conf.get("drive_folder") or "").strip()
    if not u:
        return []
    m = re.search(r"/folders/([A-Za-z0-9_-]{10,})", u)
    fid = m.group(1) if m else sd.drive_id(u)
    root = sd.google(f"/drive/v3/files/{fid}", {"fields": "id,name,driveId", "supportsAllDrives": "true"})
    since = since_dt(state, "drive", days)
    q = f"modifiedTime > '{since.astimezone(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')}' and mimeType != 'application/vnd.google-apps.folder' and trashed = false"
    files, tok = [], None
    while True:
        p = {"q": q, "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,parents,webViewLink,lastModifyingUser(displayName))",
             "pageSize": 1000, "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
        p.update({"corpora": "drive", "driveId": root["driveId"]} if root.get("driveId") else {"corpora": "user"})
        if tok:
            p["pageToken"] = tok
        r = sd.google("/drive/v3/files", p)
        files += r["files"]
        tok = r.get("nextPageToken")
        if not tok:
            break
    par = folder_map(root["driveId"]) if root.get("driveId") else {}

    def under(pid, again=True):
        for _ in range(60):
            if pid == fid:
                return True
            if pid not in par:
                return False
            pid = par[pid]
        return False
    files = [f for f in files if under((f.get("parents") or [None])[0])]
    seen = state.setdefault("drive_seen", {})
    readable = re.compile(r"google-apps\.(spreadsheet|document|presentation)$|spreadsheetml|wordprocessingml|presentationml")
    keys = [me.get("name"), me.get("name", "")[1:] if len(me.get("name", "")) == 3 else None]
    out, skipped = [], 0
    os.makedirs(CACHE, exist_ok=True)
    for f in sorted(files, key=lambda f: f["modifiedTime"], reverse=True)[:60]:
        if seen.get(f["id"]) == f["modifiedTime"]:
            continue
        if not readable.search(f["mimeType"]):
            seen[f["id"]] = f["modifiedTime"]
            skipped += 1
            continue
        try:
            text = sd.drive_source(f["id"])["text"]
        except Exception as e:
            log(f"  Drive 읽기 실패: {f['name']} ({str(e)[:80]})")
            continue
        cpath = os.path.join(CACHE, f["id"] + ".txt")
        old = open(cpath, encoding="utf-8").read() if os.path.isfile(cpath) else None
        open(cpath, "w", encoding="utf-8").write(text)
        seen[f["id"]] = f["modifiedTime"]
        diff = changed_lines(old, text, keys)
        if not diff.strip():
            continue
        out.append({"src": "구글 Drive", "id": f"drive:{f['id']}", "when": f["modifiedTime"][:16].replace("T", " "),
                    "link": f.get("webViewLink", ""),
                    "head": f"{f['name']} (수정: {(f.get('lastModifyingUser') or {}).get('displayName', '')})"
                            + (" — 처음 읽음: 내 이름이 있는 줄만" if old is None else " — 지난번 이후 바뀐 줄만(+)"),
                    "text": clip(diff, CAP["drive"])})
    log(f"Drive '{root['name']}' 수정된 문서 {len(files)}개 중 새로 읽을 것 {len(out)}개" + (f" (읽을 수 없는 형식 {skipped}개 제외)" if skipped else ""))
    return out


# ---------- 업무메모 (~/Downloads/yymmdd_업무내용.txt) ----------
def memo_clean(text):
    """계정·비밀번호 구역과 비밀값처럼 보이는 줄을 버린다. 구역 = '<제목>' 또는 '[제목]' 줄부터 다음 구역/구분선까지"""
    out, drop = [], False
    for l in text.splitlines():
        s = l.strip()
        if re.match(r"^[<\[].{1,40}[>\]]\s*.*$", s):
            drop = bool(SECRET_HEAD.search(s))
        elif re.match(r"^-{6,}$", s):
            drop = False
            out.append(l)
            continue
        if drop or SECRET_LINE.search(s):
            continue
        out.append(l)
    return "\n".join(out)


def src_memo(state, log):
    paths = sorted(glob.glob(os.path.expanduser("~/Downloads/[0-9][0-9][0-9][0-9][0-9][0-9]_업무내용.txt")))
    if not paths:
        return []
    p = paths[-1]
    mt = str(os.path.getmtime(p))
    seen = state.setdefault("memo_seen", {})
    if seen.get(os.path.basename(p)) == mt:
        log("업무메모 변경 없음")
        return []
    raw = open(p, "rb").read()
    for enc in ("utf-8", "cp949"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    text = memo_clean(text)
    cpath = os.path.join(CACHE, "memo.txt")
    os.makedirs(CACHE, exist_ok=True)
    old = open(cpath, encoding="utf-8").read() if os.path.isfile(cpath) else None
    open(cpath, "w", encoding="utf-8").write(text)
    seen[os.path.basename(p)] = mt
    body = text if old is None else changed_lines(old, text, [])
    if not body.strip():
        return []
    log(f"업무메모 {os.path.basename(p)} ({'전체' if old is None else '바뀐 줄만'})")
    return [{"src": "업무메모", "id": "memo:" + os.path.basename(p), "when": datetime.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M"),
             "link": "", "head": f"내가 직접 적은 업무메모 {os.path.basename(p)}" + ("" if old is None else " — 지난번 이후 바뀐 줄만(+)"),
             "text": clip(body, CAP["memo"])}]


# ---------- Claude 판단 ----------
TASK_SHAPE = """{
  "new": [{"title": "할 일 (동사로 끝나게, 한 줄)", "detail": "무엇을 어떻게 해야 하는지 2~4줄",
           "due": "YYYY-MM-DD 또는 빈 문자열", "due_text": "자료에 적힌 기한 표현 그대로 (예: '이번 주 금요일까지')",
           "requester": "시킨 사람 (예: 김보라님). 내가 스스로 적은 일이면 '본인'",
           "how": "어떻게 시켰는지: 언제·어느 채널·어떤 형태 (예: '10/27 메일로 직접 요청(수신)', '10/28 위키 회의록 액션아이템에 @멘션', 'Jira 이슈 담당자로 배정')",
           "quote": "근거 원문 한두 문장 그대로", "evidence": ["자료 id (예: mail:..., jira:IOCARE-1)"],
           "priority": "높음|보통|낮음 (중요도, 아래 기준으로 직접 판단)", "guessed": false}],
  "update": [{"id": "기존 할 일 id", "changes": {"바뀐 필드만": "새 값"}, "why": "어떤 자료로 바뀌었는지", "evidence": ["자료 id"]}],
  "done": [{"id": "기존 할 일 id", "why": "완료로 보는 근거 (예: 10/29 결과 회신 메일 보냄)", "evidence": ["자료 id"]}]
}"""


def build_prompt(me, items, open_tasks, today):
    wd = "월화수목금토일"[today.weekday()]
    p = [f"당신은 코웨이 IoT기획팀 {me.get('name') or '사용자'}님(위키 {me.get('wiki_user', '')}, 메일 {me.get('email', '')})의 업무 비서입니다.",
         f"오늘은 {today:%Y-%m-%d}({wd})입니다. 아래 새 자료에서 {me.get('name')}님이 '해야 할 일'을 찾아 기존 할 일 목록에 반영합니다.",
         "판단 규칙:",
         f"- 할 일 = {me.get('name')}님에게 직접 요청·배정된 일, {me.get('name')}님이 하겠다고 약속한 일(보낸 메일·댓글), 회의록 액션아이템에 이름이 있는 일, 업무메모에 적힌 할 일.",
         "- 할 일이 아닌 것: 단순 공유·공지·참조(CC)로만 받은 메일, 자동 알림 중 행동이 필요 없는 것, 다른 사람의 일, 이미 끝났다고 자료에 나온 일.",
         "- 기한은 오늘 날짜 기준으로 실제 날짜로 바꿉니다('금요일까지' → 이번 주 금요일). 기한이 없으면 due 는 빈 문자열.",
         "- 시킨 사람·방식이 자료에 분명하지 않으면 추정해서 적고 guessed: true. 지어내지 말고, 모르면 '확인 필요'.",
         "- 기존 할 일과 같은 일이면 new 가 아니라 update 로 (기한 변경·추가 요청 등). 완료 근거가 보이면 done.",
         "- status 가 hold 인 기존 할 일은 사용자가 일부러 보류한 일입니다. 같은 일로 new 를 만들지 말고, 내용이 바뀌면 update, 끝났으면 done.",
         "- priority(중요도)는 직접 판단합니다. 높음 = 기한이 지났거나 3일 안, 팀장·임원·법인·협력사·여러 부서가 결과를 기다림, 출시·인증·배포·회의 일정에 영향. "
         "낮음 = 참고·확인 수준이거나 기한이 멀고 늦어져도 영향이 작음. 나머지는 보통. "
         "기존 할 일도 새 자료나 오늘 날짜 때문에 중요도가 달라졌으면 update 의 changes.priority 로 다시 매깁니다.",
         "- 새 자료에 할 일이 없으면 빈 배열. 출력은 아래 형식의 JSON 하나만 (설명·코드블록 없이).",
         TASK_SHAPE,
         "\n## 기존 할 일 (아직 안 끝난 것 · status open=진행, hold=보류)",
         json.dumps([{k: t.get(k) for k in ("id", "title", "due", "requester", "how", "priority", "status")} for t in open_tasks], ensure_ascii=False) if open_tasks else "(없음)",
         "\n## 새 자료"]
    blocks, total = [], 0
    for i, it in enumerate(items):
        block = f"\n\n===== [{it['id']}] {it['src']} · {it['when']}\n{it['head']}\n{it['text']}"
        if total + len(block) > CAP["total"]:
            blocks.append(f"\n\n(자료가 많아 나머지 {len(items) - i}건은 생략)")
            break
        total += len(block)
        blocks.append(block)
    return "\n".join(p) + "".join(blocks)


def parse(text):
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    try:
        r = json.loads(t[t.find("{"): t.rfind("}") + 1])
    except ValueError as e:
        raise BotError(f"결과 형식 오류(JSON): {e}\n{(text or '')[:500]}")
    return {k: r.get(k) or [] for k in ("new", "update", "done")}


# ---------- 실행 ----------
def run(log=log_default, days=None, mail=False, doc=True):
    os.makedirs(DIR, exist_ok=True)
    if os.path.isfile(LOCK) and time.time() - os.path.getmtime(LOCK) < 3600:
        raise BotError("이미 할 일을 찾는 중입니다 (화면 또는 아침 자동 실행). 잠시 후 다시 시도하세요.")
    open(LOCK, "w").write(str(os.getpid()))
    try:
        return _run(log, days, mail, doc)
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass


def _run(log, days, mail, doc=True):
    conf = cfg()
    days = int(days or conf.get("lookback_days") or FIRST_DAYS)
    state = jload(STATE, {})
    started = datetime.datetime.now()
    me = whoami()
    log(f"사용자: {me.get('name') or '(이름 확인 실패)'}")
    items, errors, done_src = [], [], []
    steps = [("mail", "메일", lambda: src_mail(state, days, log)),
             ("jira", "Jira", lambda: src_jira(state, days, me, log)),
             ("wiki", "위키", lambda: src_wiki(state, days, me, log)),
             ("drive", "Drive", lambda: src_drive(state, days, me, conf, log)),
             ("memo", "업무메모", lambda: src_memo(state, log))]
    for key, label, fn in steps:
        try:
            items += fn()
            done_src.append(key)
        except Exception as e:
            errors.append(f"{label}: {str(e)[:200]}")
            log(f"{label} 읽기 실패: {str(e)[:200]}")
    tasks = jload(TASKS, [])
    open_tasks = [t for t in tasks if t.get("status") in ("open", "hold")]
    stat = {"items": len(items), "new": 0, "updated": 0, "done": 0, "cost": 0}
    if items:
        prompt = build_prompt(me, items, open_tasks, datetime.date.today())
        open(os.path.join(DIR, "last_prompt.txt"), "w", encoding="utf-8").write(prompt)
        log(f"Claude 판단 중… (새 자료 {len(items)}건, {len(prompt):,}자)")
        r = sd.run_claude(prompt, conf.get("model") or None, "IoT업무봇: 내 할 일 찾기")
        res = parse(r.get("result"))
        stat["cost"] = r.get("total_cost_usd") or 0
        by_id = {t["id"]: t for t in tasks}
        link = {it["id"]: it["link"] for it in items}
        now = started.strftime("%Y-%m-%d %H:%M")
        n = max([int(t["id"][1:]) for t in tasks if re.fullmatch(r"t\d+", t.get("id", ""))] or [0])
        for t in res["new"]:
            n += 1
            ev = [e for e in t.get("evidence") or [] if e in link]
            tasks.append(dict(t, id=f"t{n}", status="open", found=now, updated=now, evidence=ev, links=[link[e] for e in ev if link[e]], history=[]))
            stat["new"] += 1
        for u in res["update"]:
            t = by_id.get(u.get("id"))
            if t and t.get("status") in ("open", "hold"):
                ch = {k: v for k, v in (u.get("changes") or {}).items() if k in ("title", "detail", "due", "due_text", "requester", "how", "quote", "priority")}
                t["history"].append({"at": now, "why": u.get("why", ""), "before": {k: t.get(k) for k in ch}})
                t.update(ch, updated=now)
                for e in u.get("evidence") or []:
                    if e in link and e not in t["evidence"]:
                        t["evidence"].append(e)
                        if link[e]:
                            t["links"].append(link[e])
                stat["updated"] += 1
        for d in res["done"]:
            t = by_id.get(d.get("id"))
            if t and t.get("status") in ("open", "hold"):
                t.pop("held", None)
                t.update(status="done", closed=now, closed_by="자동", closed_why=d.get("why", ""), updated=now)
                stat["done"] += 1
        jsave(TASKS, tasks)
    # 성공한 소스만 '마지막으로 읽은 시각'을 옮긴다 (실패한 소스는 다음에 같은 기간을 다시 읽음)
    last = state.setdefault("last", {})
    for k in done_src:
        last[k] = started.isoformat(timespec="seconds")
    state["last_run"] = {"at": started.strftime("%Y-%m-%d %H:%M"), "sec": round((datetime.datetime.now() - started).total_seconds()),
                         "errors": errors, **stat}
    jsave(STATE, state)
    log(f"새 자료 {stat['items']}건 → 새 할 일 {stat['new']} · 갱신 {stat['updated']} · 자동 완료 {stat['done']}")
    view = board()
    if doc or mail:
        view["docx"] = save_docx(view, me.get("name", ""))
        log(f"워드 저장: {view['docx']}")
    if mail:
        view["mailed"] = send_mail(view, view["docx"])
        log(f"메일 발송: {view['mailed']}")
    return view


PRI = {"높음": 0, "보통": 1, "낮음": 2}
GROUP_LABEL = {"지난 기한": "기한 지남 · 아직 완료 안 됨", "오늘": "오늘", "이번 주": "이번 주", "다음 주 이후": "다음 주 이후", "기한 미정": "기한 미정"}


def board():
    """화면·워드·메일 공용 보기.
    open: 할 일(기한 묶음, 묶음 안은 중요도→기한 순) / hold: 보류 / done_days: 완료(날짜별, 최근 30일) / notmine: 내 일 아님(최근 30일)"""
    tasks = jload(TASKS, [])
    state = jload(STATE, {})
    td = datetime.date.today()
    today = td.isoformat()
    week_end = (td + datetime.timedelta(days=6 - td.weekday())).isoformat()

    def grp(t):
        d = t.get("due") or ""
        if not d:
            return "기한 미정"
        if d < today:
            return "지난 기한"
        if d == today:
            return "오늘"
        if d <= week_end:
            return "이번 주"
        return "다음 주 이후"
    order = ["지난 기한", "오늘", "이번 주", "다음 주 이후", "기한 미정"]
    open_t = [t for t in tasks if t.get("status") == "open"]
    for t in open_t:
        t["group"] = grp(t)
        t["priority"] = t.get("priority") if t.get("priority") in PRI else "보통"
        if t["group"] == "지난 기한":
            try:
                t["overdue_days"] = (td - datetime.date.fromisoformat(t["due"])).days
            except ValueError:
                t["overdue_days"] = 0
    open_t.sort(key=lambda t: (order.index(t["group"]), PRI[t["priority"]], t.get("due") or "9999", t.get("found", "")))
    hold = sorted([t for t in tasks if t.get("status") == "hold"], key=lambda t: (PRI.get(t.get("priority"), 1), t.get("held", "")))
    cut = (td - datetime.timedelta(days=30)).isoformat()
    done = sorted([t for t in tasks if t.get("status") == "done" and (t.get("closed") or "") >= cut], key=lambda t: t.get("closed", ""), reverse=True)
    days = {}
    for t in done:
        days.setdefault((t.get("closed") or "")[:10], []).append(t)
    done_days = [{"date": d, "items": l} for d, l in days.items()]
    notmine = sorted([t for t in tasks if t.get("status") == "notmine" and (t.get("closed") or "") >= cut], key=lambda t: t.get("closed", ""), reverse=True)
    return {"open": open_t, "hold": hold, "done_days": done_days, "notmine": notmine, "closed": done + notmine,
            "groups": order, "group_label": GROUP_LABEL, "last_run": state.get("last_run"), "today": today,
            "conf": {k: cfg().get(k) for k in ("drive_folder", "mail_time", "mail_to", "mail_on")}, "schedule": schedule_status(),
            "docx": docx_today()}


def docx_today():
    import docx_report
    p = docx_report.default_path()
    return p if os.path.isfile(p) else ""


def mark(tid, status, due="", priority=""):
    """status: done 완료 / notmine 내 일 아님 / hold 보류 / open 다시 할 일로(보류에서 꺼낼 땐 due 필수) / deleted 삭제"""
    if status not in ("done", "notmine", "hold", "open", "deleted"):
        raise BotError(f"알 수 없는 상태: {status}")
    tasks = jload(TASKS, [])
    for t in tasks:
        if t["id"] != tid:
            continue
        now = time.strftime("%Y-%m-%d %H:%M")
        prev = t.get("status")
        for k in ("closed", "closed_by", "closed_why", "held"):
            t.pop(k, None)
        if status == "open":
            if prev == "hold" and not due:
                raise BotError("보류에서 다시 할 일로 옮길 때는 기한을 정해 주세요")
            ch = {}
            if due and due != t.get("due"):
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due):
                    raise BotError("기한은 YYYY-MM-DD")
                ch.update(due=due, due_text=f"직접 지정 ({due[5:].replace('-', '/')})")
            if priority in PRI and priority != t.get("priority"):
                ch["priority"] = priority
            if ch:
                t.setdefault("history", []).append({"at": now, "why": "보류에서 다시 할 일로 (직접 지정)" if prev == "hold" else "직접 수정",
                                                    "before": {k: t.get(k) for k in ch}})
                t.update(ch)
            t.update(status="open", updated=now)
        elif status == "hold":
            t.update(status="hold", held=now, updated=now)
        else:
            t.update(status=status, closed=now, closed_by="직접", closed_why="", updated=now)
        jsave(TASKS, tasks)
        return t
    raise BotError(f"할 일을 찾지 못했습니다: {tid}")


# ---------- 워드 문서 ----------
def save_docx(v, name=None):
    """다운로드 폴더에 '[IoT업무봇] 내 할 일_yymmdd.docx' 저장 (같은 날은 덮어씀, 워드로 열려 있으면 시각 붙여 새로 저장)"""
    import docx_report
    if name is None:
        try:
            name = whoami().get("name", "")
        except Exception:
            name = ""
    return docx_report.build(v, name)


# ---------- 메일 ----------
def mail_html(v):
    e = html.escape
    rows = []
    wd = "월화수목금토일"
    for g in v["groups"]:
        ts = [t for t in v["open"] if t["group"] == g]
        if not ts:
            continue
        color = {"지난 기한": "#C2412D", "오늘": "#C2412D", "이번 주": "#A86B00"}.get(g, "#2F5BD3")
        rows.append(f'<tr><td colspan="4" style="padding:14px 8px 6px;font-weight:700;color:{color};border-bottom:2px solid {color}">{e(g)} ({len(ts)})</td></tr>')
        for t in ts:
            due = t.get("due") or ""
            dd = f"{due[5:].replace('-', '/')}({wd[datetime.date.fromisoformat(due).weekday()]})" if due else "-"
            links = " ".join(f'<a href="{e(l)}">원문{i + 1 if len(t["links"]) > 1 else ""}</a>' for i, l in enumerate(t.get("links") or []))
            rows.append(
                f'<tr style="vertical-align:top"><td style="padding:8px;border-bottom:1px solid #DDE1E6;white-space:nowrap">{e(dd)}'
                f'{"<br><span style=color:#616A75;font-size:12px>" + e(t.get("due_text") or "") + "</span>" if t.get("due_text") else ""}</td>'
                f'<td style="padding:8px;border-bottom:1px solid #DDE1E6"><b>{e(t["title"])}</b>{" <span style=color:#A86B00>(추정)</span>" if t.get("guessed") else ""}'
                f'<br><span style="font-size:13px">{e(t.get("detail") or "")}</span>'
                f'{"<br><span style=font-size:12px;color:#616A75>“" + e(t.get("quote") or "") + "”</span>" if t.get("quote") else ""}</td>'
                f'<td style="padding:8px;border-bottom:1px solid #DDE1E6;white-space:nowrap">{e(t.get("requester") or "")}</td>'
                f'<td style="padding:8px;border-bottom:1px solid #DDE1E6;font-size:13px">{e(t.get("how") or "")}<br>{links}</td></tr>')
    lr = v.get("last_run") or {}
    errs = "".join(f"<li>{e(x)}</li>" for x in lr.get("errors") or [])
    return (f'<div style="font-family:Pretendard,Malgun Gothic,sans-serif;font-size:14px;color:#1B1F24">'
            f'<p>오늘({v["today"]}) 해야 할 일 <b>{len(v["open"])}건</b>입니다. 새로 찾은 일 {lr.get("new", 0)}건 · 바뀐 일 {lr.get("updated", 0)}건 · 자동 완료 {lr.get("done", 0)}건 '
            f'(새 자료 {lr.get("items", 0)}건 확인).</p>'
            + (f'<p style="color:#C2412D">읽지 못한 자료:</p><ul>{errs}</ul>' if errs else "")
            + '<table style="border-collapse:collapse;width:100%"><thead><tr style="background:#F4F5F7;text-align:left">'
              '<th style="padding:8px">기한</th><th style="padding:8px">할 일</th><th style="padding:8px">시킨 사람</th><th style="padding:8px">어떻게</th></tr></thead><tbody>'
            + "".join(rows) + '</tbody></table><p style="color:#616A75;font-size:12px">IoT업무봇 · 내 할 일 — 완료 표시는 IoT업무봇 화면에서 할 수 있습니다.</p></div>')


def send_mail(v, docx_path=None):
    """워드 파일이 있으면 첨부하고 본문은 요약만, 없으면 예전처럼 표 본문"""
    from email.mime.application import MIMEApplication
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    c = json.load(open(GMAIL_CONF, encoding="utf-8"))
    to = cfg().get("mail_to") or c.get("to")
    gmail("/profile")  # 토큰 준비
    over = sum(1 for t in v["open"] if t["group"] in ("지난 기한", "오늘"))
    if docx_path and os.path.isfile(docx_path):
        lr = v.get("last_run") or {}
        msg = MIMEMultipart()
        msg.attach(MIMEText(
            f'<div style="font-family:Pretendard,Malgun Gothic,sans-serif;font-size:14px">오늘({v["today"]}) 해야 할 일 <b>{len(v["open"])}건</b>'
            f' (새로 {lr.get("new", 0)} · 바뀜 {lr.get("updated", 0)} · 자동 완료 {lr.get("done", 0)}). 자세한 내용은 첨부한 워드 문서를 보세요.'
            f'<br><span style="color:#616A75;font-size:12px">PC 저장 위치: {html.escape(docx_path)}</span></div>', "html", "utf-8"))
        part = MIMEApplication(open(docx_path, "rb").read(), Name=os.path.basename(docx_path))
        part.add_header("Content-Disposition", "attachment", filename=("utf-8", "", os.path.basename(docx_path)))
        msg.attach(part)
    else:
        msg = MIMEText(mail_html(v), "html", "utf-8")
    msg["To"] = to
    msg["Subject"] = f"[IoT업무봇] {datetime.date.today():%m/%d} 내 할 일 {len(v['open'])}건" + (f" · 오늘/지난 기한 {over}건" if over else "")
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    sd.http("POST", "https://gmail.googleapis.com/gmail/v1/users/me/messages/send", {"Authorization": f"Bearer {GTOK}"}, {"raw": raw})
    return to


# ---------- 아침 자동 실행 (Windows 작업 스케줄러 / Mac launchd) ----------
TASK_NAME = "WorkBotMyTasks"


def schedule_status():
    import subprocess
    if sys.platform == "win32":
        p = subprocess.run(["schtasks", "/query", "/tn", TASK_NAME, "/fo", "list"], capture_output=True, text=True, encoding="cp949", errors="replace")
        if p.returncode:
            return {"on": False}
        nxt = re.search(r"(다음 실행 시간|Next Run Time):\s*(.+)", p.stdout)
        return {"on": True, "next": nxt.group(2).strip() if nxt else ""}
    if sys.platform == "darwin":
        return {"on": os.path.isfile(os.path.expanduser(f"~/Library/LaunchAgents/com.workbot.mytasks.plist"))}
    return {"on": False}


def set_schedule(on, at):
    import subprocess
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", at or ""):
        raise BotError("시각은 HH:MM (예: 08:10)")
    script = os.path.join(HERE, "morning.py")
    if sys.platform == "win32":
        subprocess.run(["schtasks", "/delete", "/tn", TASK_NAME, "/f"], capture_output=True)
        if not on:
            return "아침 자동 실행을 껐습니다"
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        exe = pyw if os.path.isfile(pyw) else sys.executable
        # PC가 꺼져 있어 놓친 경우 켜진 뒤 바로 실행(StartWhenAvailable), 배터리여도 실행
        ps = (f"$a = New-ScheduledTaskAction -Execute '{exe}' -Argument '-X utf8 \"{script}\"' -WorkingDirectory '{HERE}';"
              f"$t = New-ScheduledTaskTrigger -Daily -At '{at}';"
              "$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2);"
              f"Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $a -Trigger $t -Settings $s -Force | Out-Null")
        p = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, encoding="cp949", errors="replace")
        if p.returncode:
            raise BotError("작업 스케줄러 등록 실패: " + (p.stderr or p.stdout)[-400:])
        return f"매일 {at} 자동 실행·워드 저장 (Windows 작업 스케줄러 '{TASK_NAME}', PC가 꺼져 있었으면 켠 뒤 바로 실행)"
    if sys.platform == "darwin":
        label = "com.workbot.mytasks"
        plist = os.path.expanduser(f"~/Library/LaunchAgents/{label}.plist")
        if os.path.isfile(plist):
            subprocess.run(["launchctl", "unload", plist], capture_output=True)
            os.remove(plist)
        if not on:
            return "아침 자동 실행을 껐습니다"
        h, m = at.split(":")
        x = html.escape
        open(plist, "w").write(f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>{label}</string>
<key>ProgramArguments</key><array><string>{x(sys.executable)}</string><string>{x(script)}</string></array>
<key>EnvironmentVariables</key><dict><key>PATH</key><string>{x(os.environ.get("PATH", "/usr/bin:/bin"))}</string></dict>
<key>StartCalendarInterval</key><dict><key>Hour</key><integer>{int(h)}</integer><key>Minute</key><integer>{int(m)}</integer></dict>
</dict></plist>''')
        subprocess.run(["launchctl", "load", "-w", plist], capture_output=True)
        return f"매일 {at} 자동 실행·워드 저장 (launchd '{label}')"
    raise BotError("이 OS는 자동 실행 등록을 지원하지 않습니다")
