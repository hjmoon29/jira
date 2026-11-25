#!/usr/bin/env python3
"""프로젝트 대응 봇: 프로젝트(제품 대응 등)마다 섹션을 두고, 그 프로젝트에서 오가는 일을 한 화면에서 보고 처리한다.

  - 메일: 프로젝트 키워드로 Gmail 검색 → 새 메일 목록 + Claude 한 줄 요약 + 첨부 받기 + 답장 초안
  - Jira: 에픽 하위·키워드 이슈의 생성·상태·담당·기한 변경과 새 댓글을 변경 이력(changelog)으로 정리 → 댓글·상태 변경 처리
  - 요약·해야 할 일: 새 메일·Jira 변경을 Claude가 읽고 '지금 상황'과 할 일을 갱신 (완료 표시는 직접 또는 자동)
  - 파일: 다운로드 폴더의 엑셀·PPT·워드·PDF 중 프로젝트 키워드가 이름에 든 것 + 직접 연결한 것 → 화면 미리보기·열기

저장: runs/projects/projects.json (프로젝트 정의), runs/projects/data/<id>.json (메일·Jira·할 일·요약)
증분: 프로젝트마다 마지막으로 확인한 시각 뒤의 메일·Jira 변경만 읽는다 (처음은 최근 FIRST_DAYS일).
"""
import base64, datetime, hashlib, html, json, os, re, sys, time, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "my-tasks"), os.path.join(HERE, "..", "spec-doc"), os.path.join(HERE, "..", "jira-tasks"),
                os.path.dirname(HERE)]
from hub_progress import progress, stage, waiting  # noqa: E402
import spec_doc as sd  # noqa: E402  http·Claude 실행
import jira_tasks as jt  # noqa: E402  Jira 호출
import my_tasks as mt  # noqa: E402  Gmail 호출·본문 정리

BotError = sd.BotError
DIR = os.path.join(sd.RUNS, "projects")
DATA = os.path.join(DIR, "data")
PROJECTS = os.path.join(DIR, "projects.json")
PS_PRODUCTS = os.path.join(sd.RUNS, "product-status", "products.json")
CONFIG = os.path.join(HERE, "config.json")
DOWNLOADS = os.path.expanduser("~/Downloads")
SAVE_DIR = os.path.join(DOWNLOADS, "IoT업무봇")  # 메일 첨부를 받아 두는 곳 (프로젝트별 하위 폴더)
FIRST_DAYS = 7
KEEP_MAILS, KEEP_JIRA = 150, 150
FILE_EXT = (".xlsx", ".xlsm", ".xls", ".csv", ".pptx", ".docx", ".pdf")  # .txt 는 제외 (업무메모에 계정 정보가 있음)
PRIVATE_NAME = re.compile(r"업무내용|비번|비밀번호|password|계정", re.I)  # 이름이 이런 파일은 목록·미리보기 모두 제외
FILE_DAYS = 90  # 다운로드 폴더에서 이 기간 안에 받은(수정된) 파일만 자동 연결
SCAN_SKIP = {"bot-hub", "claude-daily-summary", "node_modules", "__pycache__", "venv", ".venv", "site-packages"}  # 프로그램 폴더는 건너뜀
SCAN_DEPTH = 4
IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")  # 메일 본문 그림(서명·캡처)은 받지 않음
TASK_NAME = "WorkBotProjects"
LOCK = os.path.join(DIR, "run.lock")
REGION_WORDS = {"말련": ["말련", "말레이시아", "Malaysia"], "태국": ["태국", "Thailand"], "대만": ["대만", "Taiwan"],
                "미국": ["미국", "북미", "USA"], "중국": ["중국", "China"], "일본": ["일본", "Japan"]}


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def cfg():
    c = {"model": "", "lookback_days": FIRST_DAYS, "auto_time": "08:20",
         # 프로젝트 메일에서 빼는 것: 자동 리포트 메일, Jira 알림 메일(Jira 변경은 Jira에서 직접 읽음)
         "mail_exclude": '-subject:"일일요약" -subject:"데일리 써머리" -subject:"IoT업무봇" -subject:"COWAY-JIRA"'}
    if os.path.isfile(CONFIG):
        c.update(json.load(open(CONFIG, encoding="utf-8")))
    return c


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
    s = (s or "").strip()
    return s if len(s) <= n else s[:n] + "…"


def split_words(s):
    return [w.strip() for w in re.split(r"[,\n]", s or "") if w.strip()] if isinstance(s, str) else [w for w in (s or []) if w]


# ---------- 프로젝트 정의 ----------
def guess_keywords(name, models):
    """제품 대응 표의 제품명·모델명에서 검색 키워드를 추정: ('[말련향] 아이콘 프로', 'CHP-7212N_V2 …') → 키워드·지역어"""
    core = re.sub(r"\[[^\]]*\]", "", name or "").strip()
    kws = [core] if core else []
    if " " in core:
        kws.append(core.replace(" ", ""))
    for code in re.findall(r"\b[A-Z]{2,}[A-Z0-9]*-[A-Z0-9]+", models or ""):
        code = re.sub(r"_V\d+$", "", code)
        if code not in kws:
            kws.append(code)
    region = []
    m = re.search(r"\[([^\]]+?)향?\]", name or "")
    if m:
        for k, words in REGION_WORDS.items():
            if k in m.group(1):
                region = words
    return kws, region


def load_projects():
    d = jload(PROJECTS, None)
    if d is None:  # 처음: 제품별 진행상황 표의 제품으로 시작
        d = {"projects": []}
        for p in jload(PS_PRODUCTS, {}).get("products", []):
            if p.get("done"):
                continue
            kws, region = guess_keywords(p.get("name"), p.get("models"))
            d["projects"].append({"id": p["id"], "name": p["name"], "models": p.get("models", ""), "epic": p.get("epic", ""),
                                  "keywords": kws, "region_words": region, "jql": "", "mail_extra": "", "files": [],
                                  "note": "", "created": now()})
        jsave(PROJECTS, d)
    return d


def find(d, pid):
    p = next((x for x in d["projects"] if x["id"] == pid), None)
    if not p:
        raise BotError("프로젝트를 찾지 못했습니다 (목록을 새로 고쳐 주세요)")
    return p


def save_project(q):
    d = load_projects()
    name = (q.get("name") or "").strip()
    if not name:
        raise BotError("프로젝트 이름을 입력하세요")
    vals = {"name": name, "models": (q.get("models") or "").strip(), "epic": (q.get("epic") or "").strip().upper(),
            "keywords": split_words(q.get("keywords")), "region_words": split_words(q.get("region_words")),
            "jql": (q.get("jql") or "").strip(), "mail_extra": (q.get("mail_extra") or "").strip(), "note": (q.get("note") or "").strip(),
            "category": (q.get("category") or "").strip()}
    if not vals["keywords"]:
        vals["keywords"] = guess_keywords(name, vals["models"])[0]
    if q.get("id"):
        p = find(d, q["id"])
        reset = p.get("keywords") != vals["keywords"] or p.get("region_words") != vals["region_words"] or p.get("epic") != vals["epic"] \
            or p.get("jql") != vals["jql"] or p.get("mail_extra") != vals["mail_extra"]
        p.update(vals)
        if reset:  # 검색 조건이 바뀌면 다음 확인 때 처음처럼 최근 기간을 다시 읽는다
            dd = load_data(p["id"])
            dd.pop("mail_at", None), dd.pop("jira_at", None)
            save_data(p["id"], dd)
    else:
        p = dict(vals, id=hashlib.md5(f"{name}{time.time()}".encode()).hexdigest()[:10], files=[], created=now())
        d["projects"].append(p)
    jsave(PROJECTS, d)
    return p["id"]


def delete_project(pid):
    d = load_projects()
    d["projects"] = [x for x in d["projects"] if x["id"] != pid]
    jsave(PROJECTS, d)


def move_project(pid, step):
    d = load_projects()
    ps = d["projects"]
    i = next((k for k, x in enumerate(ps) if x["id"] == pid), None)
    j = (i or 0) + step
    if i is not None and 0 <= j < len(ps):
        ps[i], ps[j] = ps[j], ps[i]
        jsave(PROJECTS, d)


def reorder(ids):
    """화면에서 끌어 옮긴 순서대로 (목록에 없는 프로젝트는 뒤에 그대로)"""
    d = load_projects()
    pos = {x: k for k, x in enumerate(ids or [])}
    d["projects"].sort(key=lambda x: pos.get(x["id"], len(pos)))
    jsave(PROJECTS, d)


# 제품 종류 (왼쪽 목록 아이콘): 직접 고른 값이 없으면 이름·모델명·키워드로 추정
CATEGORIES = [("정수기", r"정수기|아이콘|엘리트|(?<![A-Z])(CHPI?|CP|WP)-"), ("비데", r"비데|(?<![A-Z])BA[SH]*\d"),
              ("공기청정기", r"청정기|에어메가|airmega|(?<![A-Z])AP-"), ("매트리스", r"매트리스|슬립싱크|모션베드|(?<![A-Z])CM-"),
              ("식기세척기", r"식기세척기|(?<![A-Z])DW"), ("에어컨", r"에어컨|(?<![A-Z])AC-"), ("가습기", r"가습기"), ("제습기", r"제습기")]


def guess_category(p):
    t = " ".join([p.get("name", ""), p.get("models", "")] + list(p.get("keywords") or []))
    return next((c for c, rx in CATEGORIES if re.search(rx, t, re.I)), "기타")


def load_data(pid):
    return jload(os.path.join(DATA, f"{pid}.json"), {"mails": [], "jira": [], "todos": [], "summary": "", "summary_at": "", "last_run": None})


def save_data(pid, dd):
    jsave(os.path.join(DATA, f"{pid}.json"), dd)


# ---------- 키워드 일치 ----------
def norm(s):
    return re.sub(r"[\s_\-·]+", "", (s or "").lower())


def matches(p, text):
    t = norm(text)
    if not any(norm(k) and norm(k) in t for k in p.get("keywords") or []):
        return False
    rw = p.get("region_words") or []
    return not rw or any(norm(w) in t for w in rw)


def gq(w):
    return f'"{w}"' if re.search(r"\s", w) else w


def mail_query(p):
    kws = p.get("keywords") or []
    if not kws and not p.get("mail_extra"):
        return ""
    q = "(" + " OR ".join(gq(k) for k in kws) + ")" if kws else ""
    if p.get("region_words"):
        q += " (" + " OR ".join(gq(w) for w in p["region_words"]) + ")"
    if p.get("mail_extra"):
        q = f"({q}) OR ({p['mail_extra']})" if q else p["mail_extra"]
    return q


def jira_jql(p):
    parts = []
    if p.get("epic"):
        e = p["epic"]
        parts.append(f'("Epic Link" = {e} OR parent = {e} OR key = {e})')
    if p.get("jql"):
        parts.append(f"({p['jql']})")
    return " OR ".join(parts)


# ---------- 메일 ----------
def attachments(payload):
    out = []

    def walk(x):
        b = x.get("body") or {}
        if x.get("filename") and b.get("attachmentId"):
            out.append({"name": x["filename"], "att": b["attachmentId"], "size": b.get("size", 0)})
        for c in x.get("parts") or []:
            walk(c)
    walk(payload)
    return out


def fetch_mails(p, since, log):
    q = mail_query(p)
    if not q:
        return []
    full = f"({q}) after:{int(since.timestamp())} -category:promotions -category:social -in:chats {cfg().get('mail_exclude') or ''}"
    r = mt.gmail("/messages", {"q": full, "maxResults": 50, "includeSpamTrash": "false"})
    out = []
    for m in r.get("messages", []):
        m = mt.gmail(f"/messages/{m['id']}", {"format": "full"})
        h = {x["name"].lower(): x["value"] for x in m["payload"].get("headers", [])}
        body = mt.mail_body(m["payload"])
        out.append({"id": m["id"], "thread": m["threadId"], "when": datetime.datetime.fromtimestamp(int(m["internalDate"]) / 1000).strftime("%Y-%m-%d %H:%M"),
                    "from": h.get("from", ""), "to": h.get("to", ""), "subject": h.get("subject", "(제목 없음)"),
                    "sent": "SENT" in m.get("labelIds", []), "snippet": clip(html.unescape(m.get("snippet", "")), 240),
                    "link": f"https://mail.google.com/mail/u/0/#all/{m['threadId']}", "atts": attachments(m["payload"]),
                    "_body": clip(body, 3000)})
    log(f"메일 {len(out)}통 ({since:%m/%d %H:%M} 이후, 검색: {q})")
    return out


def project_folder(p):
    return os.path.join(SAVE_DIR, re.sub(r'[\\/:*?"<>|]', "_", p["name"]).strip() or p["id"])


def _att_bytes(mail_id, att, name):
    try:
        r = mt.gmail(f"/messages/{mail_id}/attachments/{att}")
    except BotError:  # 첨부 id 가 바뀌었으면 메일을 다시 읽어 같은 이름의 첨부로
        m = mt.gmail(f"/messages/{mail_id}", {"format": "full"})
        a = next((x for x in attachments(m["payload"]) if x["name"] == name), None)
        if not a:
            raise
        r = mt.gmail(f"/messages/{mail_id}/attachments/{a['att']}")
    return base64.urlsafe_b64decode(r["data"] + "==")


def _save_att(p, mail_id, att, name, size=0):
    folder = project_folder(p)
    os.makedirs(folder, exist_ok=True)
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", os.path.basename(name or "첨부"))
    path = os.path.join(folder, name)
    if os.path.isfile(path) and (not size or os.path.getsize(path) == size):
        return path  # 이미 받은 파일
    raw = _att_bytes(mail_id, att, name)
    if os.path.isfile(path) and os.path.getsize(path) != len(raw):  # 같은 이름, 다른 내용(새 버전) → 받은 날짜를 붙여 따로
        b, e = os.path.splitext(name)
        path = os.path.join(folder, f"{b}_{time.strftime('%y%m%d_%H%M')}{e}")
    elif os.path.isfile(path):
        return path
    open(path, "wb").write(raw)
    return path


def save_attachment(pid, mail_id, att, name):
    p = find(load_projects(), pid)
    dd = load_data(pid)
    path = _save_att(p, mail_id, att, name)
    for m in dd["mails"]:
        if m["id"] == mail_id:
            for a in m.get("atts", []):
                if a["name"] == name:
                    a["saved"] = path
    save_data(pid, dd)
    return path


def download_attachments(p, dd, log, prog=None):
    """이 프로젝트 메일의 첨부(본문 그림 제외)를 전부 다운로드\\IoT업무봇\\<프로젝트> 에 받아 둔다. 이미 받은 것은 건너뜀"""
    todo = [(m, a) for m in dd["mails"] for a in m.get("atts", [])
            if not a["name"].lower().endswith(IMG_EXT) and not (a.get("saved") and os.path.isfile(a["saved"]))]
    n = fail = 0
    for k, (m, a) in enumerate(todo):
        if prog:
            prog(k, len(todo), a["name"])
        try:
            a["saved"] = _save_att(p, m["id"], a["att"], a["name"], a.get("size", 0))
            n += 1
        except (BotError, OSError) as e:
            fail += 1
            log(f"첨부 받기 실패: {a['name']} — {e}")
    if todo:
        log(f"첨부 {n}개 저장 → {project_folder(p)}" + (f" (실패 {fail})" if fail else ""))
    return n


def reply_draft(pid, mail_id):
    p = find(load_projects(), pid)
    m = mt.gmail(f"/messages/{mail_id}", {"format": "full"})
    h = {x["name"].lower(): x["value"] for x in m["payload"].get("headers", [])}
    dd = load_data(pid)
    prompt = (f"당신은 코웨이 IoT기획 담당자입니다. 아래 메일에 보낼 회신 초안을 한국어 존댓말로 써 주세요.\n"
              f"- 프로젝트: {p['name']} ({p.get('models', '')})\n- 프로젝트 현재 상황: {dd.get('summary') or '정보 없음'}\n"
              "- 사내 메일 말투, 사람 이름에는 '님'. 모르는 사실은 지어내지 말고 [확인 필요]로 표시.\n"
              "- 인사·본문·맺음말만 (서명 제외). 설명 없이 초안 본문만 출력.\n\n"
              f"제목: {h.get('subject', '')}\n보낸 사람: {h.get('from', '')}\n받는 사람: {h.get('to', '')}\n참조: {h.get('cc', '')}\n\n"
              f"{clip(mt.mail_body(m['payload']), 8000)}")
    r = sd.run_claude(prompt, cfg().get("model") or "sonnet", "IoT업무봇: 프로젝트 메일 답장 초안")
    return {"subject": h.get("subject", ""), "to": h.get("from", ""), "text": (r.get("result") or "").strip(),
            "link": f"https://mail.google.com/mail/u/0/#all/{m['threadId']}"}


# ---------- Jira ----------
FIELD_KO = {"status": "상태", "assignee": "담당자", "duedate": "기한", "summary": "제목", "priority": "우선순위", "resolution": "해결",
            "description": "설명", "Attachment": "첨부", "labels": "레이블", "Fix Version": "수정 버전", "issuetype": "유형",
            "Epic Link": "에픽", "Link": "연결", "reporter": "보고자"}


def jira_clean(s):
    s = re.sub(r"\[~([^\]]+)\]", lambda m: "@" + m.group(1).split("@")[0], s or "")
    return re.sub(r"\s+", " ", s).strip()


def jira_val(fld, v):
    v = v or "없음"
    return v[:10] if fld == "duedate" and re.match(r"\d{4}-\d\d-\d\d", v) else v


def siblings(p):
    """같은 에픽을 쓰는 다른 프로젝트 (말련향 아이콘 프로·엘리트 빌트인처럼 한 에픽에 제품 여러 개)"""
    return [x for x in load_projects()["projects"] if x["id"] != p["id"] and p.get("epic") and x.get("epic") == p["epic"]]


def fetch_jira(p, since, log):
    jql = jira_jql(p)
    sib = siblings(p)
    if not jql:
        return []
    s = since.strftime("%Y-%m-%d %H:%M")
    r = jt.call("GET", "/search", params={"jql": f"({jql}) AND updated >= \"{s}\" ORDER BY updated DESC", "maxResults": 100,
                                          "fields": "summary,status,assignee,reporter,duedate,priority,created,updated,comment,issuetype,description",
                                          "expand": "changelog"})
    iso = since.strftime("%Y-%m-%dT%H:%M")
    nm = lambda u: (u or {}).get("displayName", "")
    out = []
    for i in r.get("issues", []):
        f = i["fields"]
        # 같은 에픽을 다른 프로젝트와 나눠 쓰면: 제목이 이 프로젝트 키워드에 맞거나, 어느 형제 프로젝트에도 안 맞는(공통) 이슈만
        if sib and i["key"] != p["epic"] and not matches(dict(p, region_words=[]), f.get("summary", ""))                 and any(matches(dict(x, region_words=[]), f.get("summary", "")) for x in sib):
            continue
        ch = []
        if (f.get("created") or "") >= iso:
            ch.append({"when": f["created"][:16].replace("T", " "), "who": nm(f.get("reporter")), "what": "이슈 생성", "kind": "new"})
        for hst in (i.get("changelog") or {}).get("histories", []):
            if hst.get("created", "") < iso:
                continue
            for it in hst.get("items", []):
                fld = it.get("field", "")
                if fld == "Epic Child":
                    what = f"하위 이슈 추가: {it.get('toString') or ''}" if it.get("toString") else f"하위 이슈 제외: {it.get('fromString') or ''}"
                elif fld in ("description", "RemoteIssueLink", "Rank", "timespent", "WorklogId"):
                    what = f"{FIELD_KO.get(fld, fld)} 수정"
                else:
                    what = f"{FIELD_KO.get(fld, fld)}: {jira_val(fld, it.get('fromString'))} → {jira_val(fld, it.get('toString'))}"
                ch.append({"when": hst["created"][:16].replace("T", " "), "who": nm(hst.get("author")), "what": what,
                           "kind": "status" if fld == "status" else "change"})
        for c in (f.get("comment") or {}).get("comments", []):
            if c.get("created", "") >= iso:
                ch.append({"when": c["created"][:16].replace("T", " "), "who": nm(c.get("author")), "what": "댓글: " + clip(jira_clean(c.get("body", "")), 400), "kind": "comment"})
        if not ch:
            continue
        ch.sort(key=lambda x: x["when"])
        out.append({"key": i["key"], "summary": f.get("summary", ""), "status": (f.get("status") or {}).get("name", ""),
                    "type": (f.get("issuetype") or {}).get("name", ""), "assignee": nm(f.get("assignee")), "due": f.get("duedate") or "",
                    "link": f"https://jira.coway.com/browse/{i['key']}", "changes": ch, "at": ch[-1]["when"],
                    "_desc": clip(f.get("description") or "", 1200)})
    log(f"Jira 변경 {len(out)}건 ({since:%m/%d %H:%M} 이후)")
    return out


def jira_transitions(key):
    r = jt.call("GET", f"/issue/{key}/transitions")
    return [{"id": t["id"], "name": t["name"], "to": (t.get("to") or {}).get("name", "")} for t in r.get("transitions", [])]


def jira_do(key, comment="", transition=""):
    if not re.fullmatch(r"[A-Z][A-Z0-9_]+-\d+", key or ""):
        raise BotError("이슈 키가 올바르지 않습니다")
    done = []
    if comment.strip():
        jt.call("POST", f"/issue/{key}/comment", {"body": comment.strip()})
        done.append("댓글 등록")
    if transition:
        jt.call("POST", f"/issue/{key}/transitions", {"transition": {"id": str(transition)}})
        st = (jt.call("GET", f"/issue/{key}", params={"fields": "status"})["fields"].get("status") or {}).get("name", "")
        done.append(f"상태 변경 → {st}")
    if not done:
        raise BotError("댓글이나 바꿀 상태를 입력하세요")
    return f"{key}: " + " · ".join(done)


# ---------- Claude: 요약·할 일 ----------
def build_prompt(p, dd, mails, jira):
    today = datetime.date.today()
    todo = [t for t in dd.get("todos", []) if t.get("status") == "open"]
    held = [t for t in dd.get("todos", []) if t.get("status") == "hold"]
    items = []
    for m in mails:
        items.append(f"### [mail:{m['id']}] 메일{'(내가 보냄)' if m['sent'] else ''} {m['when']}\n제목: {m['subject']}\n보낸 사람: {m['from']}\n받는 사람: {m['to']}"
                     + (f"\n첨부: {', '.join(a['name'] for a in m['atts'])}" if m["atts"] else "") + f"\n{m['_body']}")
    for j in jira:
        items.append(f"### [jira:{j['key']}] Jira {j['key']} {j['summary']} (상태 {j['status']} · 담당 {j['assignee'] or '없음'} · 기한 {j['due'] or '없음'})\n"
                     + "\n".join(f"- {c['when']} {c['who']}: {c['what']}" for c in j["changes"]) + (f"\n설명: {j['_desc']}" if j["_desc"] else ""))
    pname = re.sub(r'^\[[^\]]*\]\s*', '', p['name'])  # Python 3.9(Mac): f-string 안에 백슬래시를 못 써서 밖에서 계산
    return f"""당신은 코웨이 IoT기획팀 담당자의 업무 비서입니다. 오늘은 {today:%Y-%m-%d}({'월화수목금토일'[today.weekday()]})입니다.
프로젝트 '{p['name']}' (모델: {p.get('models') or '미정'}, 에픽: {p.get('epic') or '없음'})에 새로 들어온 메일과 Jira 변경을 읽고 정리해 주세요.
{('프로젝트 메모: ' + p['note']) if p.get('note') else ''}

## 지금까지의 상황 요약
{dd.get('summary') or '(처음 정리)'}

## 열려 있는 할 일 (id: 내용 · 기한)
{chr(10).join(f"- {t['id']}: {t['title']} · {t.get('due') or '기한 없음'}" for t in todo) or '(없음)'}

## 보류한 할 일 (사용자가 일단 미뤄 둠 — 같은 일을 새 할 일로 다시 만들지 말 것)
{chr(10).join(f"- {t['title']}" for t in held) or '(없음)'}

## 새 자료
{chr(10).join(items)}

## 출력: 아래 JSON 하나만 (설명·코드블록 없이)
{{"summary": "프로젝트의 지금 상황 5~10줄. 줄마다 '결정:' '진행:' '이슈:' '미정:' '다음:' 중 하나로 시작하고 한 줄에 한 가지만(80자 안팎). 날짜는 YY/MM/DD 또는 M/D. 결정→진행→이슈→미정→다음 순. 이전 요약을 새 자료로 갱신",
 "mail_notes": {{"<mail id 그대로, 예 mail:abc>": "그 메일 핵심 한 줄 (누가 무엇을 요청/공유)"}},
 "jira_notes": {{"<jira:KEY>": "이번 변경 핵심 한 줄"}},
 "todos_new": [{{"title": "내가 해야 할 일 (동사로 끝나게, 구체적으로)", "due": "YYYY-MM-DD 또는 빈 문자열", "priority": "높음|보통|낮음", "src": "근거 자료 id (mail:… / jira:…)", "why": "누가 어떻게 요청했는지 한 줄"}}],
 "todos_update": [{{"id": "열린 할 일 id", "due": "바뀐 기한(없으면 생략)", "title": "바뀐 내용(없으면 생략)"}}],
 "todos_done": ["새 자료로 끝난 게 확실한 열린 할 일 id"]}}
규칙: 제품 명칭은 프로젝트 이름의 표기(띄어쓰기 포함)를 그대로 쓴다 — 예: '{pname}'.
규칙: 할 일은 '나(메일 받는 사람·Jira 담당자/보고자)'가 직접 해야 하는 것만. 이미 열린 할 일과 같은 일은 새로 만들지 말고 todos_update 로.
단순 공유·참고 메일은 할 일 없이 mail_notes 만. 사람 이름에는 '님'. 사적인 내용은 적지 않는다. 존댓말 대신 간결한 개조식."""


def parse_json(text):
    t = re.sub(r"^```(json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    s, e = t.find("{"), t.rfind("}")
    try:
        return json.loads(t[s:e + 1])
    except ValueError:
        raise BotError("Claude 응답을 읽지 못했습니다: " + clip(text, 300))


def link_of(src, mails, jira):
    if src.startswith("mail:"):
        m = next((x for x in mails if x["id"] == src[5:]), None)
        return (m["link"], f"메일 · {clip(m['subject'], 40)}") if m else ("", "메일")
    if src.startswith("jira:"):
        return f"https://jira.coway.com/browse/{src[5:]}", f"Jira {src[5:]}"
    return "", ""


def refresh_one(p, log, prog=None):
    t0 = time.time()
    dd = load_data(p["id"])
    days = int(cfg().get("lookback_days") or FIRST_DAYS)
    start = datetime.datetime.now()
    since = lambda k: datetime.datetime.strptime(dd[k], "%Y-%m-%d %H:%M") - datetime.timedelta(minutes=5) if dd.get(k) \
        else start - datetime.timedelta(days=days)
    prog = prog or (lambda pct, msg: None)
    prog(5, f"{p['name']} · 메일 확인")
    seen = {m["id"] for m in dd["mails"]}
    mails = [m for m in fetch_mails(p, since("mail_at"), log) if m["id"] not in seen]
    prog(35, f"{p['name']} · Jira 확인")
    jira = fetch_jira(p, since("jira_at"), log)
    old = {(j["key"], c["when"], c["what"]) for j in dd["jira"] for c in j["changes"]}
    for j in jira:  # 지난번에 이미 본 변경은 빼고 새 변경만
        j["changes"] = [c for c in j["changes"] if (j["key"], c["when"], c["what"]) not in old]
    jira = [j for j in jira if j["changes"]]
    res = {}
    if mails or jira:
        prog(55, f"{p['name']} · Claude가 요약·할 일 정리 (메일 {len(mails)} · Jira {len(jira)})")
        r = sd.run_claude(build_prompt(p, dd, mails, jira), cfg().get("model") or None, f"IoT업무봇: 프로젝트 대응 · {p['name']}")
        res = parse_json(r.get("result"))
    mn, jn = res.get("mail_notes") or {}, res.get("jira_notes") or {}
    for m in mails:
        m["note"] = mn.get(f"mail:{m['id']}", "")
        m["new"] = True
        m.pop("_body", None)
    for m in dd["mails"]:
        m["new"] = False
    dd["mails"] = sorted(mails + dd["mails"], key=lambda x: x["when"], reverse=True)[:KEEP_MAILS]
    for j in dd["jira"]:
        j["new"] = False
    byk = {j["key"]: j for j in dd["jira"]}
    for j in jira:
        j.pop("_desc", None)
        j["note"], j["new"] = jn.get(f"jira:{j['key']}", ""), True
        if j["key"] in byk:  # 같은 이슈의 이전 변경 이력 뒤에 이어 붙임
            j["changes"] = (byk[j["key"]]["changes"] + j["changes"])[-30:]
        byk[j["key"]] = j
    dd["jira"] = sorted(byk.values(), key=lambda x: x["at"], reverse=True)[:KEEP_JIRA]
    n_new = n_done = 0
    todos = dd.setdefault("todos", [])
    tid = {t["id"]: t for t in todos}
    for t in res.get("todos_new") or []:
        if not t.get("title"):
            continue
        link, lab = link_of(t.get("src", ""), mails, jira)
        todos.append({"id": hashlib.md5(f"{p['id']}{t['title']}{time.time()}".encode()).hexdigest()[:8], "title": t["title"],
                      "due": t.get("due") or "", "priority": t.get("priority") or "보통", "why": t.get("why", ""), "src": t.get("src", ""),
                      "link": link, "src_label": lab, "status": "open", "created": now()})
        n_new += 1
    for u in res.get("todos_update") or []:
        t = tid.get(u.get("id"))
        if t and t["status"] == "open":
            for k in ("due", "title"):
                if u.get(k):
                    t[k] = u[k]
            t["updated"] = now()
    for i in res.get("todos_done") or []:
        t = tid.get(i)
        if t and t["status"] == "open":
            t.update(status="done", done_at=now(), done_by="자동 (새 자료로 완료 확인)")
            n_done += 1
    if res.get("summary"):
        dd["summary"], dd["summary_at"] = res["summary"], now()
    dd["mail_at"] = dd["jira_at"] = start.strftime("%Y-%m-%d %H:%M")
    save_data(p["id"], dd)
    prog(90, f"{p['name']} · 메일 첨부 받기")
    n_att = download_attachments(p, dd, log, lambda i, n, nm: prog(90 + 9 * i / max(n, 1), f"{p['name']} · 첨부 받기 {nm}"))
    dd["last_run"] = {"at": now(), "mails": len(mails), "jira": len(jira), "todo_new": n_new, "todo_done": n_done, "atts": n_att, "sec": round(time.time() - t0)}
    save_data(p["id"], dd)
    return f"{p['name']}: 새 메일 {len(mails)} · Jira 변경 {len(jira)} → 할 일 +{n_new}" + (f" · 자동 완료 {n_done}" if n_done else "") + (f" · 첨부 {n_att}개 저장" if n_att else "")


def refresh(pid, log):
    os.makedirs(DIR, exist_ok=True)
    if os.path.isfile(LOCK) and time.time() - os.path.getmtime(LOCK) < 2 * 3600:
        raise BotError("프로젝트 새로 확인이 이미 실행 중입니다 (아침 자동 실행 등) — 끝난 뒤 다시 눌러 주세요")
    open(LOCK, "w").write(now())
    try:
        return _refresh(pid, log)
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass


def _refresh(pid, log):
    d = load_projects()
    targets = [find(d, pid)] if pid else d["projects"]
    lines = []
    for k, p in enumerate(targets):
        a, b = 100 * k / len(targets), 100 * (k + 1) / len(targets)
        try:
            lines.append(refresh_one(p, log, lambda pct, msg: progress(a + (b - a) * pct / 100, msg)))
        except BotError as e:
            lines.append(f"{p['name']}: 실패 — {e}")
    progress(100, "완료")
    return lines


def mark(pid, tid, status):
    dd = load_data(pid)
    t = next((x for x in dd.get("todos", []) if x["id"] == tid), None)
    if not t:
        raise BotError("할 일을 찾지 못했습니다")
    if status == "delete":
        dd["todos"].remove(t)
    else:
        if status not in ("open", "done", "hold"):
            raise BotError("알 수 없는 상태입니다")
        t["status"] = status
        t["done_at"], t["done_by"] = (now(), "직접") if status == "done" else ("", "")
        t["hold_at"] = now() if status == "hold" else ""
    save_data(pid, dd)


def add_todo(pid, title, due=""):
    if not (title or "").strip():
        raise BotError("할 일 내용을 입력하세요")
    dd = load_data(pid)
    dd.setdefault("todos", []).append({"id": hashlib.md5(f"{pid}{title}{time.time()}".encode()).hexdigest()[:8], "title": title.strip(),
                                       "due": due or "", "priority": "보통", "why": "직접 추가", "src": "", "link": "", "src_label": "",
                                       "status": "open", "created": now()})
    save_data(pid, dd)


# ---------- 파일 ----------
def safe_path(path):
    """다운로드 폴더 안의 파일만 다룬다 (화면에서 받은 경로로 다른 곳을 열지 않게)"""
    rp = os.path.realpath(path or "")
    root = os.path.realpath(DOWNLOADS)
    if not (rp.startswith(root + os.sep) and os.path.isfile(rp)):
        raise BotError("다운로드 폴더 안의 파일만 열 수 있습니다")
    if PRIVATE_NAME.search(os.path.basename(rp)) or rp.lower().endswith(".txt"):
        raise BotError("개인 메모·계정 정보가 있을 수 있는 파일은 업무봇에서 열지 않습니다")
    return rp


def scan_downloads():
    """다운로드 폴더 전체(하위 폴더 포함, 프로그램 폴더 제외)에서 최근 FILE_DAYS일 문서 파일"""
    out, cut = [], time.time() - FILE_DAYS * 86400
    base = DOWNLOADS
    if os.path.isdir(base):
        for root, dirs, files in os.walk(base):
            depth = root[len(base):].count(os.sep)
            dirs[:] = [x for x in dirs if not x.startswith((".", "$")) and x not in SCAN_SKIP and depth < SCAN_DEPTH
                       and not re.search(r"개인|private|personal", x, re.I)]  # 개인 폴더는 보지 않음
            for f in files:
                if f.lower().endswith(FILE_EXT) and not f.startswith("~$") and not PRIVATE_NAME.search(f):
                    fp = os.path.join(root, f)
                    try:
                        st = os.stat(fp)
                    except OSError:
                        continue
                    if st.st_mtime >= cut:
                        rel = os.path.relpath(root, base)
                        out.append({"path": fp, "name": f, "size": st.st_size, "folder": "" if rel == "." else rel,
                                    "mtime": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")})
    uniq = {x["path"]: x for x in out}
    return sorted(uniq.values(), key=lambda x: x["mtime"], reverse=True)


def files_for(p, scan):
    pinned = set(p.get("files") or [])
    hidden = set(p.get("hidden_files") or [])
    out = []
    for f in scan:
        if f["path"] in hidden:
            continue
        if f["path"] in pinned or f["path"].startswith(project_folder(p) + os.sep) or matches(p, f["name"]) \
                or (f.get("folder") and not f["path"].startswith(SAVE_DIR) and matches(p, f["folder"])):  # 폴더 이름이 프로젝트 키워드여도
            out.append(dict(f, pinned=f["path"] in pinned))
    for fp in pinned - {f["path"] for f in scan}:  # 기간이 지났거나 다른 폴더에서 연결한 파일
        if os.path.isfile(fp):
            st = os.stat(fp)
            out.append({"path": fp, "name": os.path.basename(fp), "size": st.st_size, "pinned": True,
                        "mtime": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")})
    return sorted(out, key=lambda x: x["mtime"], reverse=True)


def link_file(pid, path, on=True):
    d = load_projects()
    p = find(d, pid)
    path = safe_path(path)
    files, hidden = p.setdefault("files", []), p.setdefault("hidden_files", [])
    if on:
        if path not in files:
            files.append(path)
        if path in hidden:
            hidden.remove(path)
    else:
        if path in files:
            files.remove(path)
        if path not in hidden:
            hidden.append(path)
    jsave(PROJECTS, d)


def open_file(path):
    path = safe_path(path)
    if os.name == "nt":
        os.startfile(path)  # noqa: S606 — 연결 프로그램(엑셀·파워포인트 등)으로 열기
    else:
        import subprocess
        subprocess.Popen(["open", path])


def _xml_text(xml, para_tag):
    """OOXML 조각에서 문단 단위 글자만 뽑는다"""
    paras = re.findall(rf"<{para_tag}[ >].*?</{para_tag}>", xml, re.S)
    out = []
    for p in paras:
        t = "".join(re.findall(r"<(?:a|w):t(?: [^>]*)?>([^<]*)</(?:a|w):t>", p))
        if t.strip():
            out.append(html.unescape(t))
    return out


def preview(path):
    path = safe_path(path)
    ext = os.path.splitext(path)[1].lower()
    name = os.path.basename(path)
    if ext in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        sheets = []
        for ws in wb.worksheets[:12]:
            rows = []
            for r in ws.iter_rows(max_row=80, max_col=30, values_only=True):
                rows.append(["" if v is None else str(v)[:200] for v in r])
            while rows and not any(rows[-1]):
                rows.pop()
            w = max((max((i + 1 for i, v in enumerate(r) if v), default=0) for r in rows), default=0)
            sheets.append({"name": ws.title, "rows": [r[:w] for r in rows], "more": (ws.max_row or 0) > 80})
        wb.close()
        return {"kind": "sheet", "name": name, "sheets": sheets, "more_sheets": len(wb.sheetnames) > 12}
    if ext == ".csv":
        import csv
        raw = open(path, "rb").read()
        for enc in ("utf-8-sig", "cp949"):
            try:
                txt = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        rows = list(csv.reader(txt.splitlines()))[:80]
        return {"kind": "sheet", "name": name, "sheets": [{"name": name, "rows": rows, "more": False}]}
    if ext == ".pptx":
        z = zipfile.ZipFile(path)
        nums = sorted(int(m.group(1)) for n in z.namelist() if (m := re.fullmatch(r"ppt/slides/slide(\d+)\.xml", n)))
        slides = []
        for n in nums[:80]:
            texts = _xml_text(z.read(f"ppt/slides/slide{n}.xml").decode("utf-8", "replace"), "a:p")
            note = f"ppt/notesSlides/notesSlide{n}.xml"
            notes = _xml_text(z.read(note).decode("utf-8", "replace"), "a:p") if note in z.namelist() else []
            slides.append({"no": n, "texts": texts[:60], "notes": [t for t in notes if not t.strip().isdigit()][:10]})
        return {"kind": "slides", "name": name, "slides": slides, "total": len(nums)}
    if ext == ".docx":
        z = zipfile.ZipFile(path)
        paras = [l for l in _xml_text(z.read("word/document.xml").decode("utf-8", "replace"), "w:p") if not mt.SECRET_LINE.search(l)]
        return {"kind": "text", "name": name, "lines": paras[:600], "more": len(paras) > 600}
    return {"kind": "none", "name": name, "text": "이 형식은 화면 미리보기를 지원하지 않습니다. [열기]로 확인하세요."}


# ---------- 아침 자동 실행 ----------
def save_cfg(c):
    tmp = CONFIG + ".tmp"
    json.dump(c, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG)


def schedule_status():
    import subprocess
    st = {"on": False, "at": cfg().get("auto_time") or "08:20", "last": ""}
    try:
        lines = [l for l in open(os.path.join(DIR, "morning.log"), encoding="utf-8").read().splitlines() if l.strip()]
        ends = [l for l in lines if l[20:].startswith(("끝", "실패"))]
        st["last"] = (ends[-1][:16] + (" 실패" if "실패" in ends[-1][17:22] else " 완료")) if ends else lines[-1][:16] + " 실행 중"
    except (OSError, IndexError):
        pass
    if sys.platform == "win32":
        # PC 시작 시 전부 새로 받아오기(IoTWorkBot_Startup)가 켜져 있으면 그게 자동 확인
        q = subprocess.run(["schtasks", "/query", "/tn", "IoTWorkBot_Startup", "/fo", "list"], capture_output=True, text=True, encoding="cp949", errors="replace")
        if not q.returncode and not re.search(r"(상태|Status):\s*(사용 안 함|Disabled)", q.stdout):
            st.update(on=True, at="PC 켤 때", startup=True)
            return st
        p = subprocess.run(["schtasks", "/query", "/tn", TASK_NAME, "/fo", "list"], capture_output=True, text=True, encoding="cp949", errors="replace")
        if not p.returncode and not re.search(r"(상태|Status):\s*(사용 안 함|Disabled)", p.stdout):
            nxt = re.search(r"(다음 실행 시간|Next Run Time):\s*(.+)", p.stdout)
            st.update(on=True, next=nxt.group(2).strip() if nxt else "")
    elif sys.platform == "darwin":
        st["on"] = os.path.isfile(os.path.expanduser("~/Library/LaunchAgents/com.workbot.projects.plist"))
    return st


def set_schedule(on, at):
    import subprocess
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", at or ""):
        raise BotError("시각은 HH:MM (예: 08:20)")
    c = cfg()
    c["auto_time"] = at
    save_cfg(c)
    script = os.path.join(HERE, "morning.py")
    if sys.platform == "win32":
        subprocess.run(["schtasks", "/delete", "/tn", TASK_NAME, "/f"], capture_output=True)
        if not on:
            return "아침 자동 확인을 껐습니다"
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        exe = pyw if os.path.isfile(pyw) else sys.executable
        # 창 없이(pythonw) · PC가 꺼져 있어 놓쳤으면 켠 뒤 바로(StartWhenAvailable) · 배터리여도 실행
        ps = (f"$a = New-ScheduledTaskAction -Execute '{exe}' -Argument '-X utf8 \"{script}\"' -WorkingDirectory '{HERE}';"
              f"$t = New-ScheduledTaskTrigger -Daily -At '{at}';"
              "$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2);"
              f"Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $a -Trigger $t -Settings $s -Force | Out-Null")
        p = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, encoding="cp949", errors="replace")
        if p.returncode:
            raise BotError("작업 스케줄러 등록 실패: " + (p.stderr or p.stdout)[-400:])
        return f"매일 {at} 모든 프로젝트를 자동으로 새로 확인합니다 (메일·Jira·첨부·파일, 작업 스케줄러 '{TASK_NAME}')"
    if sys.platform == "darwin":
        label = "com.workbot.projects"
        plist = os.path.expanduser(f"~/Library/LaunchAgents/{label}.plist")
        if os.path.isfile(plist):
            subprocess.run(["launchctl", "unload", plist], capture_output=True)
            os.remove(plist)
        if not on:
            return "아침 자동 확인을 껐습니다"
        h, m = at.split(":")
        x = html.escape
        open(plist, "w").write(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>Label</key><string>{label}</string>
<key>ProgramArguments</key><array><string>{x(sys.executable)}</string><string>{x(script)}</string></array>
<key>EnvironmentVariables</key><dict><key>PATH</key><string>{x(os.environ.get("PATH", "/usr/bin:/bin"))}</string></dict>
<key>StartCalendarInterval</key><dict><key>Hour</key><integer>{int(h)}</integer><key>Minute</key><integer>{int(m)}</integer></dict>
</dict></plist>""")
        subprocess.run(["launchctl", "load", "-w", plist], capture_output=True)
        return f"매일 {at} 모든 프로젝트를 자동으로 새로 확인합니다 (launchd '{label}')"
    raise BotError("이 OS는 자동 실행 등록을 지원하지 않습니다")


# ---------- 화면 데이터 ----------
def board(sel=None):
    d = load_projects()
    scan = scan_downloads()
    today = datetime.date.today().isoformat()
    out = []
    for p in d["projects"]:
        dd = load_data(p["id"])
        todos = dd.get("todos", [])
        opn = [t for t in todos if t["status"] == "open"]
        files = files_for(p, scan)
        out.append(dict(p, cat=p.get("category") or guess_category(p), data={**dd, "files": files}, query={"mail": mail_query(p), "jql": jira_jql(p)},
                        counts={"todo": len(opn), "late": sum(1 for t in opn if t.get("due") and t["due"] <= today),
                                "new_mail": sum(1 for m in dd["mails"] if m.get("new")), "new_jira": sum(1 for j in dd["jira"] if j.get("new")),
                                "files": len(files)}))
    linked = {f["path"] for p in out for f in p["data"]["files"]}
    return {"projects": out, "sel": sel or (out[0]["id"] if out else ""), "today": today, "categories": [c for c, _ in CATEGORIES] + ["기타"],
            "other_files": [f for f in scan if f["path"] not in linked][:80], "store": DIR, "downloads": DOWNLOADS,
            "schedule": schedule_status(), "running": os.path.isfile(LOCK)}
