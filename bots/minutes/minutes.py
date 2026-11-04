#!/usr/bin/env python3
"""회의록 작성 봇: 회의 음성 → (PC 안에서) 글자로 바꾸기 → Claude가 회의록 정리 → 미리보기·수정 요청 → 위키 게시.

  start    올린 음성(또는 녹취 .txt)으로 작업을 만들고 백그라운드 작업(worker.py)을 띄운다 → 바로 목록으로 돌아감
  worker   음성 인식(faster-whisper, 오프라인) → 회의록 초안(v1). 진행률은 job.json 에 남겨 화면이 읽는다
  revise   수정 요청을 반영한 새 버전
  publish  상위 위키 페이지의 하위 문서로 게시 (같은 제목이 있으면 새 버전으로 갱신)

음성은 PC 밖으로 나가지 않는다. Claude 에는 글자로 바꾼 녹취록만 넘긴다.
저장: runs/minutes/<작업id>/ job.json · transcript.txt · v1.xml, v2.xml …
"""
import datetime, json, os, re, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "spec-doc"))
import spec_doc as sd  # noqa: E402  위키 호출, Claude 실행, storage 미리보기

BotError = sd.BotError
DIR = os.path.join(sd.RUNS, "minutes")
UPLOADS = os.path.join(sd.RUNS, "uploads")
CONFIG = os.path.join(HERE, "config.json")
LOCK = os.path.join(DIR, "stt.lock")
AUDIO_EXT = {".m4a", ".mp3", ".wav", ".webm", ".mp4", ".aac", ".ogg", ".flac", ".wma", ".amr", ".3gp", ".mov", ".mkv"}
TEXT_EXT = {".txt", ".vtt", ".srt"}
STATUS_LABEL = {"queued": "대기 중", "transcribing": "음성 인식 중", "drafting": "회의록 작성 중", "ready": "초안 완료",
                "published": "위키 게시됨", "error": "오류"}


def cfg():
    c = {"parent": "", "stt_model": "large-v3-turbo", "threads": 4, "claude_model": "", "glossary": ""}
    try:
        c.update(json.load(open(CONFIG, encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return c


def save_cfg(c):
    json.dump(c, open(CONFIG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def jdir(jid):
    if not re.fullmatch(r"m\d{8}-\d{6}", jid or ""):
        raise BotError("작업을 찾을 수 없습니다")
    d = os.path.join(DIR, jid)
    if not os.path.isfile(os.path.join(d, "job.json")):
        raise BotError("작업을 찾을 수 없습니다")
    return d


def load(jid):
    return json.load(open(os.path.join(jdir(jid), "job.json"), encoding="utf-8"))


def save(job):
    d = os.path.join(DIR, job["id"])
    tmp = os.path.join(d, "job.json.tmp")
    json.dump(job, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(d, "job.json"))


def fmt_ts(sec):
    sec = int(sec)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60:02d}:{sec % 60:02d}"


# ---------- 시작 ----------
def start(q):
    src = (q.get("audio") or "").strip()
    if not src or not os.path.isfile(src):
        raise BotError("회의 음성 파일을 첨부하세요")
    src = os.path.abspath(src)
    if os.path.commonpath([src, os.path.abspath(UPLOADS)]) != os.path.abspath(UPLOADS):
        raise BotError("첨부 파일 경로가 올바르지 않습니다")
    ext = os.path.splitext(src)[1].lower()
    if ext not in AUDIO_EXT | TEXT_EXT:
        raise BotError(f"지원하지 않는 파일 형식입니다: {ext} (음성: m4a·mp3·wav·mp4 등 / 녹취록: txt·vtt·srt)")
    jid = "m" + time.strftime("%Y%m%d-%H%M%S")
    d = os.path.join(DIR, jid)
    os.makedirs(d)
    dst = os.path.join(d, "source" + ext)
    shutil.move(src, dst)
    job = {"id": jid, "created": time.strftime("%Y-%m-%d %H:%M"), "status": "queued", "progress": 0,
           "file": q.get("audio_name") or os.path.basename(src).split("_", 1)[-1], "source": os.path.basename(dst),
           "kind": "text" if ext in TEXT_EXT else "audio",
           "title": (q.get("title") or "").strip(), "date": (q.get("date") or "").strip() or time.strftime("%Y-%m-%d"),
           "place": (q.get("place") or "").strip(), "attendees": (q.get("attendees") or "").strip(),
           "notes": (q.get("notes") or "").strip(), "parent": (q.get("parent") or "").strip() or cfg().get("parent", ""),
           "transcript_in_wiki": q.get("transcript_in_wiki") in (True, "true"), "versions": [], "log": []}
    save(job)
    spawn(jid)
    return job


def spawn(jid):
    """화면 요청과 상관없이 끝까지 돌도록 따로 띄운다 (창 없음, 낮은 우선순위라 PC가 덜 느려짐)"""
    exe = sys.executable
    kw = {}
    if sys.platform == "win32":
        pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
        exe = pyw if os.path.isfile(pyw) else exe
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000 | 0x00004000  # DETACHED · NEW_GROUP · NO_WINDOW · BELOW_NORMAL
    else:
        kw["start_new_session"] = True
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", HF_HUB_DISABLE_SYMLINKS_WARNING="1")
    log = open(os.path.join(DIR, jid, "worker.log"), "a", encoding="utf-8")
    subprocess.Popen([exe, "-X", "utf8", os.path.join(HERE, "worker.py"), jid], cwd=HERE, env=env,
                     stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kw)


# ---------- 음성 인식 ----------
def transcribe(job, set_progress):
    d = os.path.join(DIR, job["id"])
    src = os.path.join(d, job["source"])
    if job["kind"] == "text":
        raw = open(src, encoding="utf-8", errors="replace").read()
        if src.endswith((".vtt", ".srt")):  # 자막 형식: 번호·시각 줄 정리
            raw = re.sub(r"^WEBVTT.*$|^\d+\s*$", "", raw, flags=re.M)
            raw = re.sub(r"^(\d{1,2}:)?(\d{2}:\d{2})[.,]\d{3} --> .*$", r"[\2]", raw, flags=re.M)
        return re.sub(r"\n{3,}", "\n\n", raw).strip(), None
    from faster_whisper import WhisperModel
    c = cfg()
    set_progress(0, "음성 인식 모델 불러오는 중… (처음 한 번은 내려받기 때문에 오래 걸립니다)")
    model = WhisperModel(c["stt_model"], device="cpu", compute_type="int8", cpu_threads=int(c.get("threads") or 4))
    attendees = re.sub(r"\s+", " ", job.get("attendees", ""))
    hint = "코웨이 IoT기획팀 회의. IoCare+, 정수기, 비데, 매트리스, Jira, 위키, Figma, 기능정의서." + (f" 참석자: {attendees}." if attendees else "")
    segs, info = model.transcribe(src, language="ko", vad_filter=True, beam_size=5, initial_prompt=hint + " " + c.get("glossary", ""),
                                  condition_on_previous_text=False)
    dur = info.duration or 1
    lines, last = [], 0
    t0 = time.time()
    for s in segs:
        txt = s.text.strip()
        if txt:
            lines.append(f"[{fmt_ts(s.start)}] {txt}")
        if time.time() - last > 5:
            last = time.time()
            p = min(99, s.end / dur * 100)
            el = time.time() - t0
            eta = el / max(p, 1) * (100 - p)
            set_progress(p, f"음성 인식 중 {fmt_ts(s.end)} / {fmt_ts(dur)} · 남은 시간 약 {max(1, round(eta / 60))}분")
    return "\n".join(lines), round(dur)


# ---------- Claude 회의록 ----------
FORMAT = """위키(Confluence storage XML) 회의록 형식 — 아래 순서와 태그를 지킵니다.
<ac:structured-macro ac:name="info"><ac:rich-text-body><p><strong>일시</strong> … · <strong>장소</strong> … · <strong>참석자</strong> …님, …님</p>
<p><strong>작성</strong> {작성자} (IoT업무봇 회의록 — 음성 자동 인식 후 정리, 작성자 검토)</p></ac:rich-text-body></ac:structured-macro>
<h2>1. 회의 목적</h2> 한두 문장
<h2>2. 논의 내용</h2> 안건마다 <h3>2-1. 안건명</h3> + <ul><li>요점</li></ul> (누가 어떤 의견을 냈는지 '○○님:' 으로)
<h2>3. 결정 사항</h2> <table><tbody><tr><th>No.</th><th>결정 내용</th><th>비고</th></tr> … </tbody></table>
<h2>4. 액션 아이템</h2> <table><tbody><tr><th>No.</th><th>할 일</th><th>담당</th><th>기한</th><th>상태</th></tr> … 상태 칸은
  <ac:structured-macro ac:name="status"><ac:parameter ac:name="colour">Grey</ac:parameter><ac:parameter ac:name="title">진행 전</ac:parameter></ac:structured-macro></table>
<h2>5. 미결 · 추가 확인 사항</h2> <ul> (없으면 '없음')
<h2>6. 다음 회의</h2> 언급된 경우만 (일시·안건). 없으면 이 장을 뺍니다.
불확실한 값은 <ac:structured-macro ac:name="status"><ac:parameter ac:name="colour">Yellow</ac:parameter><ac:parameter ac:name="title">확인 필요</ac:parameter></ac:structured-macro> 를 붙입니다."""


def build_prompt(job, transcript, author, prev_xml=None, feedback=None):
    p = [f"당신은 코웨이 IoT기획팀 {author}님의 회의록 작성을 돕습니다. 아래 녹취록(음성 자동 인식 결과)으로 위키에 올릴 회의록을 작성합니다.",
         "규칙:",
         "- 녹취록에 없는 내용은 지어내지 않습니다. 모르는 값(담당·기한·장소 등)은 '확인 필요' 상태 표시.",
         "- 자동 인식이라 오타·잘못 들린 단어가 있습니다. 문맥과 참석자 이름·용어로 바로잡되, 확신이 없으면 원래 표현 옆에 '확인 필요'.",
         "- 화자 구분이 없는 녹취록입니다. 발언자는 문맥(호칭, '○○님이 해 주세요' 등)으로 추정하고, 추정이 어려우면 이름을 쓰지 않습니다.",
         "- 사람 이름에는 '님'을 붙이고 존댓말 명사형(…함, …하기로 함)으로 간결하게 씁니다. 잡담·인사는 뺍니다.",
         "- 기한은 회의 날짜 기준 실제 날짜(MM/DD(요일))로 바꿉니다. 예: '다음 주 금요일' → 11/14(금).",
         "- 액션 아이템은 '누가 무엇을 언제까지'가 분명한 것만, 결정 사항은 합의된 것만 씁니다.",
         FORMAT.replace("{작성자}", author),
         "출력: 첫 줄에 <<<TITLE 회의 제목 TITLE>>> (회의명이 주어졌으면 그대로), 이어서 <<<XML ... XML>>> 사이에 본문 storage XML 만. 다른 설명 금지.",
         "\n## 회의 정보",
         f"- 회의명: {job.get('title') or '(없음 — 내용으로 짧게 정해 주세요)'}",
         f"- 날짜: {job['date']} ({'월화수목금토일'[datetime.date.fromisoformat(job['date']).weekday()]})" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", job["date"]) else f"- 날짜: {job['date']}",
         f"- 장소: {job.get('place') or '(확인 필요)'}",
         f"- 참석자: {job.get('attendees') or '(녹취록에서 추정)'}",
         f"- 작성자 메모·안건: {job.get('notes') or '(없음)'}"]
    if cfg().get("glossary"):
        p.append(f"- 용어: {cfg()['glossary']}")
    if prev_xml:
        p += ["\n## 지금 초안 (이 초안을 고칩니다. 수정 요청 외의 부분은 그대로 둡니다)", prev_xml, "\n## 수정 요청", feedback or ""]
    p += ["\n## 녹취록", transcript]
    return "\n".join(p)


def parse(text, fallback_title):
    m = re.search(r"<<<XML\s*(.*?)\s*XML>>>", text or "", re.S)
    body = re.sub(r"^```(?:xml|html)?\s*|\s*```$", "", (m.group(1) if m else text or "").strip())
    t = re.search(r"<<<TITLE\s*(.*?)\s*TITLE>>>", text or "", re.S)
    title = (t.group(1).strip() if t else "") or fallback_title
    err = None
    try:
        sd.check_xml(body)
    except Exception as e:  # noqa: BLE001
        err = f"XML 형식 오류: {e}"
    return title, body, err


def author_name():
    try:
        me = sd.wiki("GET", "/user/current")
        return re.sub(r"\[.*$", "", me.get("displayName", "")).strip() or "작성자"
    except Exception:  # noqa: BLE001
        return "작성자"


def page_title(job, title):
    d = job["date"].replace("-", "")[2:] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", job["date"]) else time.strftime("%y%m%d")
    title = re.sub(r"^\[회의록\]\s*(\d{6}\s*)?", "", title).strip()
    return f"[회의록] {d} {title}"


def write_version(job, prompt, feedback=None):
    d = os.path.join(DIR, job["id"])
    v = len(job["versions"]) + 1
    open(os.path.join(d, f"v{v}.prompt.txt"), "w", encoding="utf-8").write(prompt)
    t0 = time.time()
    r = sd.run_claude(prompt, cfg().get("claude_model") or None, f"IoT업무봇: 회의록 ({job.get('title') or job['file']})")
    title, body, err = parse(r.get("result"), job.get("title") or os.path.splitext(job["file"])[0])
    open(os.path.join(d, f"v{v}.xml"), "w", encoding="utf-8").write(body)
    job["versions"].append({"v": v, "at": time.strftime("%Y-%m-%d %H:%M"), "feedback": feedback or "", "sec": round(time.time() - t0),
                            "cost": r.get("total_cost_usd"), "error": err})
    job["page_title"] = page_title(job, title)
    return job


def draft(job):
    transcript = open(os.path.join(DIR, job["id"], "transcript.txt"), encoding="utf-8").read()
    job["author"] = job.get("author") or author_name()
    return write_version(job, build_prompt(job, transcript, job["author"]))


def revise(jid, feedback):
    if not (feedback or "").strip():
        raise BotError("고칠 점을 적어 주세요")
    job = load(jid)
    if not job["versions"]:
        raise BotError("아직 초안이 없습니다")
    d = jdir(jid)
    prev = open(os.path.join(d, f"v{len(job['versions'])}.xml"), encoding="utf-8").read()
    transcript = open(os.path.join(d, "transcript.txt"), encoding="utf-8").read()
    write_version(job, build_prompt(job, transcript, job.get("author") or author_name(), prev, feedback.strip()), feedback.strip())
    save(job)
    return job


# ---------- 보기 · 게시 ----------
def body_for_wiki(job, xml):
    if not job.get("transcript_in_wiki"):
        return xml
    from html import escape
    tr = open(os.path.join(DIR, job["id"], "transcript.txt"), encoding="utf-8").read()
    paras = "".join(f"<p>{escape(x)}</p>" for x in tr.splitlines() if x.strip())
    return (xml + '<ac:structured-macro ac:name="expand"><ac:parameter ac:name="title">녹취록 원문 (음성 자동 인식 — 오타가 있을 수 있음)</ac:parameter>'
            f"<ac:rich-text-body>{paras}</ac:rich-text-body></ac:structured-macro>")


def view(jid, v=None):
    job = load(jid)
    out = {"job": job, "html": "", "transcript": ""}
    d = jdir(jid)
    tp = os.path.join(d, "transcript.txt")
    if os.path.isfile(tp):
        out["transcript"] = open(tp, encoding="utf-8").read()
    if job["versions"]:
        v = int(v or len(job["versions"]))
        xml = open(os.path.join(d, f"v{v}.xml"), encoding="utf-8").read()
        out["v"] = v
        try:
            out["html"] = sd.preview_html(body_for_wiki(job, xml))
        except Exception as e:  # noqa: BLE001
            out["html"] = f"<pre>{e}</pre>"
    return out


def publish(jid, parent=None, title=None):
    job = load(jid)
    if not job["versions"]:
        raise BotError("아직 초안이 없습니다")
    last = job["versions"][-1]
    if last.get("error"):
        raise BotError(f"마지막 초안에 형식 오류가 있어 게시할 수 없습니다 — 수정 요청으로 다시 만들어 주세요 ({last['error']})")
    parent = (parent or job.get("parent") or cfg().get("parent") or "").strip()
    if not parent:
        raise BotError("올릴 위치(상위 위키 페이지 주소)를 입력하세요")
    pp = sd.wiki_page(sd.page_id(parent))
    title = (title or "").strip() or job["page_title"]
    xml = body_for_wiki(job, open(os.path.join(jdir(jid), f"v{last['v']}.xml"), encoding="utf-8").read())
    sd.check_xml(xml)
    sp = pp["space"]["key"]
    same = sd.wiki("GET", "/content", params={"spaceKey": sp, "title": title, "expand": "version"}).get("results", [])
    if same:
        cur = same[0]
        r = sd.wiki("PUT", f"/content/{cur['id']}", {"id": cur["id"], "type": "page", "title": title,
                    "version": {"number": cur["version"]["number"] + 1, "message": "IoT업무봇 회의록 갱신"},
                    "body": {"storage": {"value": xml, "representation": "storage"}}})
        how = f"기존 페이지 v{cur['version']['number']} → v{r['version']['number']} 갱신"
    else:
        r = sd.wiki("POST", "/content", {"type": "page", "title": title, "space": {"key": sp}, "ancestors": [{"id": pp["id"]}],
                    "body": {"storage": {"value": xml, "representation": "storage"}}})
        how = "새 하위 문서 생성"
    job.update(status="published", parent=parent, page_title=title,
               published={"id": r["id"], "url": f"{sd.WIKI}/pages/viewpage.action?pageId={r['id']}", "v": last["v"],
                          "at": time.strftime("%Y-%m-%d %H:%M"), "how": how, "parent_title": pp["title"]})
    save(job)
    c = cfg()
    c["parent"] = parent  # 다음 회의록 기본 위치
    save_cfg(c)
    return job


def jobs():
    out = []
    for n in sorted(os.listdir(DIR) if os.path.isdir(DIR) else [], reverse=True):
        try:
            j = load(n)
        except (BotError, OSError, ValueError):
            continue
        j.pop("log", None)
        j["status_label"] = STATUS_LABEL.get(j["status"], j["status"])
        out.append(j)
    return out


def delete(jid):
    job = load(jid)
    if job["status"] in ("queued", "transcribing", "drafting"):
        raise BotError("진행 중인 작업은 끝난 뒤 삭제할 수 있습니다")
    shutil.rmtree(jdir(jid))
