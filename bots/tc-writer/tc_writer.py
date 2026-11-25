#!/usr/bin/env python3
"""통합테스트 TC 작성 봇 — 최근 통합테스트 TC 문서를 양식·예시로, 제품의 IoT기능정의서·Figma 화면기획서를 근거로
'제품-IoT App 연동 통합테스트' TC 초안을 만들고, 검토 후 Google Drive 통합테스트 TC 폴더에 새 구글 시트로 만든다.

  draft   자료 자동 수집(참고 TC·기능정의서 제품 열·Figma) → Claude(헤드리스 claude -p)가 TC 행 JSON 작성 → runs/tc-writer/ 저장
  create  참고 TC 문서를 제품군 폴더에 사본으로 만든 뒤(서식·참고 탭 유지) 표지·문서이력·개요·TC·이슈 탭을 새 내용으로 바꾼다

읽기: spec-doc 봇의 인증(위키·Figma·Drive 읽기) / 쓰기: qr-url 봇의 Google 쓰기 토큰(drive·spreadsheets)
"""
import datetime, json, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "spec-doc"))
sys.path.insert(0, os.path.join(HERE, "..", "qr-url"))
sys.path.insert(0, os.path.join(HERE, ".."))
import spec_doc as sd  # noqa: E402  (읽기: Drive·Figma·Claude 실행)
import qr_url as qu  # noqa: E402  (쓰기 토큰)
try:
    from hub_progress import progress, stage, waiting  # noqa: E402
except ImportError:  # 허브 밖
    progress = lambda *a, **k: None  # noqa: E731

RUNS = os.path.abspath(os.environ.get("BOT_HUB_RUNS") or os.path.join(HERE, "..", "..", "runs"))
OUT = os.path.join(RUNS, "tc-writer")
CFG = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
SHEETS = "https://sheets.googleapis.com/v4/spreadsheets/"
DRIVE = "https://www.googleapis.com/drive/v3/files"
TC_TAB, ISSUE_TAB, OVERVIEW_TAB, COVER_TAB, HIST_TAB = "2. IoT App 통합 테스트_TC", "3. IoT App 통합 테스트_이슈 및 검토 사항", "1. 개요", "표지", "문서이력"
LIMIT = {"ref": 120_000, "func": 160_000, "figma": 260_000, "extra": 80_000}
SPEC_PAGE_SKIP = re.compile(r"cover|표지|수정\s*이력|history|작업|SPEC\s*제외|archive|아카이브|claude|^-+$", re.I)


class BotError(Exception):
    pass


def norm(s):
    return re.sub(r"[\s()\[\]_·,/⏎]+", "", str(s or "")).lower()


def today(fmt="%Y-%m-%d"):
    return datetime.date.today().strftime(fmt)


# ---------- 읽기 (spec-doc 인증 재사용) ----------
def gread(path, params=None, raw=False):
    try:
        return sd.google(path, params, raw)
    except sd.BotError as e:
        raise BotError(str(e))


def sheet_get(sid, params):
    gread("/drive/v3/about", {"fields": "user"})  # 토큰 준비
    try:
        return sd.http("GET", SHEETS + sid, {"Authorization": "Bearer " + sd.GTOKEN}, params=params)
    except sd.BotError as e:
        raise BotError(str(e))


def sheet_values(sid, tabs, rng="A1:AH1200"):
    gread("/drive/v3/about", {"fields": "user"})
    try:
        r = sd.http("GET", f"{SHEETS}{sid}/values:batchGet", {"Authorization": "Bearer " + sd.GTOKEN},
                    params=[("ranges", f"'{t}'!{rng}") for t in tabs])
    except sd.BotError as e:
        raise BotError(str(e))
    return {t: v.get("values", []) for t, v in zip(tabs, r.get("valueRanges", []))}


def drive_list(q, n=50, order="modifiedTime desc"):
    return gread("/drive/v3/files", {"q": q + " and trashed=false", "orderBy": order, "pageSize": n,
                                     "fields": "files(id,name,mimeType,modifiedTime,parents,webViewLink,lastModifyingUser(displayName))",
                                     "supportsAllDrives": "true", "includeItemsFromAllDrives": "true", "corpora": "allDrives"}).get("files", [])


def cell(rows, r, c):
    return str(rows[r][c]).strip() if r < len(rows) and c < len(rows[r]) else ""


# ---------- 제품군 판단 ----------
def category_by_name(name):
    for c in CFG["categories"]:
        if c["name"] == name:
            return c
    return None


def guess_category(product, model):
    t = (product + " " + model).lower()
    best, score = None, 0
    for c in CFG["categories"]:
        s = sum(len(k) for k in c["keys"] if k.lower() in t)
        if s > score:
            best, score = c, s
    return best


# ---------- IoT기능정의서 ----------
def find_func_def():
    files = drive_list(f"name contains '{CFG['func_def_name']}' and mimeType='application/vnd.google-apps.spreadsheet'", 15)
    files = [f for f in files if not re.search(r"사본|백업|backup|FE팀|FE형식", f["name"], re.I)]
    if not files:
        raise BotError("IoT기능정의서(CW_IoCare_제품_기능정의서)를 Drive에서 찾지 못했습니다.")
    files.sort(key=lambda f: [int(x) for x in re.findall(r"v(\d+)\.(\d+)", f["name"])[0]] if re.search(r"v\d+\.\d+", f["name"]) else [0, 0], reverse=True)
    return files[0]


LABEL_HDR = re.compile(r"description|프로토콜|component|화면|^dp|data ?type|비고|^api$|^id$|구성항목|sb ?no", re.I)


def match_cols(rows, hdr, product, model):
    """제목 줄(0~hdr) 에서 제품 열 블록 찾기 → [(줄, 시작 열, 끝 열, 제목)]"""
    toks = [norm(t) for t in re.split(r"\s+", product) if norm(t)]
    m = norm(model)
    hits = []
    for r in range(0, hdr + 1):
        row = rows[r] if r < len(rows) else []
        for c, v in enumerate(row):
            v = str(v).strip()
            if c < 3 or not v or LABEL_HDR.search(v):
                continue
            nv = norm(v)
            if (m and len(m) >= 4 and m in nv) or (toks and all(t in nv for t in toks)):
                end = c + 1
                while end < len(row) and end < c + 6 and (not str(row[end]).strip() or norm(row[end]) == nv):
                    end += 1
                hits.append((r, c, end, v.replace("\n", " ")))
    # 모델명이 맞는 블록이 있으면 그것만
    if m and any(m in norm(h[3]) for h in hits):
        hits = [h for h in hits if m in norm(h[3])]
    seen, out = set(), []
    for h in hits:
        if h[1] not in seen:
            seen.add(h[1])
            out.append(h)
    return out


def func_def_extract(product, model, cat, log=print):
    f = find_func_def()
    sid = f["id"]
    meta = sheet_get(sid, {"fields": "sheets(properties(title,hidden))"})
    tabs = [s["properties"]["title"] for s in meta["sheets"] if not s["properties"].get("hidden") and s["properties"]["title"] not in ("문서이력", "작성 규칙")]
    order = ([cat["tab"]] if cat and cat["tab"] in tabs else []) + [t for t in tabs if not cat or t != cat["tab"]]
    for tab in order:
        rows = sheet_values(sid, [tab], "A1:HZ1200")[tab]
        hdr = next((i for i in range(min(8, len(rows))) if any(str(v).strip() in ("화면명", "화면 ID") for v in rows[i])), None)
        if hdr is None:
            continue
        blocks = match_cols(rows, hdr, product, model)
        if not blocks:
            continue
        first = min(b[1] for b in blocks)
        # 공통 열: 제품 블록 시작 전까지 중 제목이 있는 열 (첫 제품 열 = 같은 줄에서 라벨이 아닌 첫 칸)
        brow = rows[blocks[0][0]]
        p0 = next((c for c, v in enumerate(brow) if c >= 3 and str(v).strip() and not LABEL_HDR.search(str(v))), first)
        common = [c for c in range(p0) if any(cell(rows, r, c) for r in range(hdr + 1))]
        name = lambda c: next((cell(rows, r, c).replace("\n", " ") for r in range(hdr, -1, -1) if cell(rows, r, c)), f"열{c}")
        lines, ff, kept = [], {}, 0
        for b in blocks:
            sub = [name(c) for c in range(b[1], b[2])]
            lines.append(f"\n## 제품 열: {b[3]}  (탭 '{tab}', 열 {b[1]}~{b[2] - 1})\n공통 열: {' | '.join(name(c) for c in common)}\n제품 열: {' | '.join(sub)}")
            ff = {}
            for r in range(hdr + 1, len(rows)):
                for c in common[:3]:
                    if cell(rows, r, c):
                        ff[c] = cell(rows, r, c)
                vals = [cell(rows, r, c) for c in range(b[1], b[2])]
                if not any(v and v != "-" for v in vals):
                    continue
                com = [cell(rows, r, c) or (ff.get(c, "") if c in common[:3] else "") for c in common]
                lines.append(f"[{r + 1}행] " + " | ".join(com) + " ‖ " + " | ".join(vals))
                kept += 1
        text = "\n".join(lines).replace("\r", "")
        log(f"IoT기능정의서 {f['name']} — '{tab}' 탭에서 {', '.join(b[3] for b in blocks)} 열 {kept}행")
        head = f"[IoT기능정의서] {f['name']} · 탭 '{tab}' · 제품 열 {', '.join(b[3] for b in blocks)} · {kept}행 (값이 비었거나 '-'인 행은 미지원으로 보고 뺌)"
        return {"kind": "func", "title": f["name"], "id": sid, "tab": tab, "blocks": [b[3] for b in blocks],
                "head": head, "text": text[:LIMIT["func"]], "url": f"https://docs.google.com/spreadsheets/d/{sid}/edit"}
    raise BotError(f"IoT기능정의서 {f['name']} 에서 '{product}' {model} 열을 찾지 못했습니다. 기능정의서의 제품 제목(예: '좌욕스파 비데 (BASH53-A)')에 있는 이름이나 모델명으로 입력해 주세요.")


# ---------- Figma ----------
def figma_text(keys, urls, log=print):
    srcs = []
    for u in urls:
        s = sd.figma_source(u)
        srcs.append(s)
        log(f"Figma {s['title']} (직접 지정)")
    for k in keys:
        meta = sd.figma(f"/files/{k}", {"depth": 1})
        pages = [p for p in meta["document"]["children"] if not SPEC_PAGE_SKIP.search(p["name"])]
        for p in pages:
            s = sd.figma_source(f"https://www.figma.com/design/{k}/?node-id={p['id'].replace(':', '-')}")
            s["head"] = f"[Figma] {meta['name']} / {p['name']} — https://www.figma.com/design/{k}/?node-id={p['id'].replace(':', '-')} · 최종 수정 {meta.get('lastModified', '')[:10]}"
            s["title"] = f"{meta['name']} / {p['name']}"
            srcs.append(s)
            log(f"Figma {s['title']} {len(s['text']):,}자")
    cap = LIMIT["figma"] // max(len(srcs), 1)
    for s in srcs:
        if len(s["text"]) > cap:
            s["text"] = s["text"][:cap]
            s["head"] += f" (앞부분 {cap:,}자만)"
    return srcs


# ---------- 참고 TC 문서 ----------
def tc_candidates(cat=None):
    folders = []
    for c in ([cat] if cat else CFG["categories"]):
        folders.append(CFG["folders"][c["folder"]])
        folders += CFG.get("extra_ref_folders", {}).get(c["folder"], [])
    folders = list(dict.fromkeys(folders))
    q = "(" + " or ".join(f"'{f}' in parents" for f in folders) + ") and mimeType='application/vnd.google-apps.spreadsheet' and name contains '통합'"
    files = [f for f in drive_list(q, 40) if not re.search(r"리포트|backup|백업|사전점검|사본", f["name"], re.I)]
    return files


def find_reference(cat, url=None):
    if url:
        fid = sd.drive_id(url)
        f = gread(f"/drive/v3/files/{fid}", {"fields": "id,name,modifiedTime,webViewLink,mimeType", "supportsAllDrives": "true"})
        if f["mimeType"] != "application/vnd.google-apps.spreadsheet":
            raise BotError("참고 TC 문서는 구글 시트여야 합니다 (사본을 만들어 양식을 그대로 쓰므로).")
        return f
    for f in tc_candidates(cat) + (tc_candidates() if cat else []):
        meta = sheet_get(f["id"], {"fields": "sheets(properties(title))"})
        if TC_TAB in [s["properties"]["title"] for s in meta["sheets"]]:
            return f
    raise BotError("통합테스트 TC 폴더에서 양식이 같은 참고 문서를 찾지 못했습니다. '참고 TC 문서 주소'를 직접 넣어 주세요.")


def tc_layout(rows):
    """TC 탭: 머리줄(시나리오 ID) 위치·열 구성"""
    hr = next((i for i, r in enumerate(rows[:40]) if any(str(v).strip() == "시나리오 ID" for v in r)), None)
    if hr is None:
        raise BotError(f"참고 문서 '{TC_TAB}' 탭에서 '시나리오 ID' 머리줄을 찾지 못했습니다.")
    top, sub = rows[hr], rows[hr + 1] if hr + 1 < len(rows) else []
    cols = {}
    for c in range(max(len(top), len(sub))):
        t, s = cell(rows, hr, c), cell(rows, hr + 1, c)
        if t:
            cols[t] = c
        if s and s not in cols:
            cols[s] = c
    need = ["시나리오 ID", "테스트 구분", "테스트 항목", "기대결과"]
    miss = [k for k in need if k not in cols]
    if miss:
        raise BotError(f"참고 TC 탭 머리줄에 {', '.join(miss)} 칸이 없습니다.")
    mids = [(k, c) for k, c in sorted(cols.items(), key=lambda x: x[1]) if cols["테스트 항목"] < c < cols["기대결과"]]
    res = [(k, c) for k, c in sorted(cols.items(), key=lambda x: x[1]) if c > cols["기대결과"] and k not in ("통합테스트", "STG 검증")]
    return {"hdr": hr, "cols": cols, "mid": mids, "result": res}


def reference_text(ref, log=print):
    sid = ref["id"]
    vals = sheet_values(sid, [COVER_TAB, OVERVIEW_TAB, TC_TAB, ISSUE_TAB])
    lay = tc_layout(vals[TC_TAB])
    out = [f"[참고 TC 문서] {ref['name']} — https://docs.google.com/spreadsheets/d/{sid}/edit"]
    for t in (OVERVIEW_TAB, TC_TAB, ISSUE_TAB):
        rows = vals[t][:400 if t == TC_TAB else 60]
        out.append(f"\n## 탭: {t}")
        for i, r in enumerate(rows):
            if any(str(v).strip() for v in r):
                out.append(f"{i + 1}행: " + " | ".join(str(v).strip().replace("\n", "⏎") for v in r).strip(" |"))
    text = "\n".join(out)
    log(f"참고 TC: {ref['name']} ({len(text):,}자)")
    return {"kind": "ref", "title": ref["name"], "id": sid, "layout": lay, "text": text[:LIMIT["ref"]],
            "head": f"[참고 TC] {ref['name']} · 수정 {ref.get('modifiedTime', '')[:10]}"}


# ---------- Claude ----------
GUIDE = """너는 코웨이 IoT기획팀의 통합테스트 담당자다. 'IoT기획팀'이 주관하는 '제품-IoT App 연동 통합테스트' TC 문서를 새 제품용으로 작성한다.

[작성 원칙]
1. 참고 TC 문서의 구성·순서·문장 스타일·시나리오 ID 체계(T-PAIR, T-CERT, T-DATA, T-IOCA 등, 4자리 번호, 하위는 -1 -2)를 그대로 따른다.
   참고 문서와 연결 방식(Wi-Fi / BLE / BLE 콤보 / Tuya)이나 제품군이 다르면 그 제품에 맞는 항목으로 바꾼다 (예: BLE only 제품엔 인증서버(T-CERT) 없음).
2. 테스트 항목의 근거는 [IoT기능정의서 제품 열]과 [Figma 화면기획서]다. 기능정의서에서 이 제품이 지원하는 모든 상태·제어 항목(API/ID·옵션값·APP 표출 옵션명)을
   빠짐없이 TC로 만든다. 옵션값은 기대결과에 '- 값' 줄로 나열한다 (APP 표출 옵션명 기준, 필요하면 '0 (OFF)'처럼 프로토콜 값 병기).
3. 앱→제품 제어, 제품→앱 동기화(상태 업로드), 홈/상태 모듈 표시, 제품 관리, 소모품 관리, 알림, 페어링(지원하는 등록 방식별)을 참고 문서처럼 구분한다.
   Figma에만 있는 화면 동작(예: 제어 불가 조건, 팝업, 툴팁)도 확인할 수 있는 것은 TC로 넣는다.
4. 기능정의서에 '미정'·'TBD'·빈 값이거나 Figma와 기능정의서가 서로 다르면 TC는 확인된 내용으로 쓰고, review_points 에 근거 위치와 함께 적는다.
5. 통합테스트 당일 확인이 불가능한 항목(예: 정해진 시각 알림, 월간 리포트)은 TC에 넣되 result 를 '검증제외' 로 하고 overview.excluded 에도 적는다.
   참고 문서에서 '검증제외'였던 내부 프로토콜 항목도 같은 방식으로 따른다.
6. 추측으로 기능을 만들지 않는다. 자료에 없는 기능은 쓰지 않는다. 모든 글은 한국어, 참고 문서와 같은 '~된다/~가능하다' 문체.

[출력] 설명 없이 아래 JSON 하나만 ```json 코드블록으로 출력한다.
{
  "overview": {
    "project_name": "프로젝트명(제품명)", "project_code": "프로젝트 코드 또는 TBD",
    "models": [{"model": "모델명(색상 등 자재 구분 포함)", "material": "자재코드 또는 TBD", "pop": "POP코드 또는 TBD"}],
    "app": "IoCare+ 또는 NECOA 등 지원 IoT 앱", "app_desc": " - 앱 설명 (참고 문서 형식)", "languages": "지원 언어(줄바꿈 구분)",
    "target_app": "대상 앱 (예: IoCare+ STG APP)",
    "scope": " - 제품등록 (페어링)\\n - Device > 홈\\n ...", "excluded": "테스트 제외 항목 (없으면 -)"
  },
  "tc": [
    {"id": "T-PAIR-0001", "group": "테스트 구분 (같은 구분의 첫 행에만, 이어지는 행은 빈 문자열)", "item": "테스트 항목",
     "mid": {"<참고 TC의 테스트 항목~기대결과 사이 칸 이름>": "값"}, "expected": "기대결과", "result": "", "note": "비고(선택)", "source": "근거(기능정의서 n행 / Figma 화면명)"}
  ],
  "review_points": ["확인이 필요한 점 — 근거"],
  "summary": "어떤 기준으로 몇 개 TC를 만들었는지 2~3문장"
}
"""


def build_prompt(q, ref, func, figs, extras):
    lay = ref["layout"]
    mids = ", ".join(f"'{k}'" for k, _ in lay["mid"]) or "(없음)"
    parts = [GUIDE,
             f"\n[대상 제품] 제품명: {q['product']} / 모델명: {q.get('model') or '(기능정의서 제품 열 기준)'} / 제품군: {q['category']}",
             f"[참고 TC의 '테스트 항목'과 '기대결과' 사이 칸] {mids} — tc[].mid 의 키로 이 이름을 그대로 쓴다. 칸이 없으면 mid 는 빈 객체.",
             ("[추가 요청]\n" + q["notes"]) if q.get("notes") else "",
             "\n\n==================== 참고 TC 문서 (양식·문체·ID 체계 예시. 내용은 다른 제품일 수 있음) ====================\n" + ref["text"],
             "\n\n==================== IoT기능정의서 — 대상 제품 열 ====================\n" + func["head"] + "\n" + func["text"]]
    for s in figs:
        parts.append("\n\n==================== Figma 화면기획서 ====================\n" + s["head"] + "\n" + s["text"])
    for s in extras:
        parts.append("\n\n==================== 추가 자료 ====================\n" + s["head"] + "\n" + s["text"][:LIMIT["extra"]])
    return "\n".join(p for p in parts if p)


def parse_json(s):
    m = re.search(r"```json\s*(\{.*\})\s*```", s, re.S) or re.search(r"(\{.*\})", s, re.S)
    if not m:
        raise BotError("Claude 결과에서 JSON을 찾지 못했습니다.\n" + s[:500])
    try:
        return json.loads(m.group(1))
    except ValueError as e:
        raise BotError(f"Claude 결과 JSON 형식 오류: {e}\n" + m.group(1)[:500])


# ---------- 초안 ----------
def past_sec():
    try:
        xs = [json.load(open(os.path.join(OUT, n), encoding="utf-8"))["meta"].get("sec") for n in os.listdir(OUT) if n.startswith("tc-") and n.endswith(".json")]
        xs = [x for x in xs if x]
        return sum(xs[-5:]) / len(xs[-5:]) if xs else 420
    except OSError:
        return 420


def draft(q, log=print):
    q = {k: (v.strip() if isinstance(v, str) else v) for k, v in q.items()}
    if not q.get("product"):
        raise BotError("제품명을 입력해 주세요.")
    q.setdefault("model", "")
    cat = category_by_name(q.get("category")) if q.get("category") not in (None, "", "자동") else None
    t0 = time.time()
    progress(3, "IoT기능정의서에서 제품 열 찾는 중")
    func = func_def_extract(q["product"], q["model"], cat or guess_category(q["product"], q["model"]), log)
    if not cat:
        cat = next((c for c in CFG["categories"] if c["tab"] == func["tab"]), None) or guess_category(q["product"], q["model"])
    if not cat:
        raise BotError("제품군을 알 수 없습니다. '제품군'을 직접 골라 주세요.")
    q["category"] = cat["name"]
    log(f"제품군: {cat['name']}")
    progress(12, "참고 TC 문서 찾는 중")
    ref = find_reference(cat, q.get("ref_url") or None)
    refsrc = reference_text(ref, log)
    progress(20, "Figma 화면기획서 읽는 중")
    furls = [u for u in sd.lines(q.get("figma")) if "figma.com" in u]
    keys = [] if furls else list(cat["figma"])
    if q.get("with_register") in (True, "true", "on", 1):
        keys.append(CFG["register_figma"])
    figs = figma_text(keys, furls, log)
    progress(38, "추가 자료 읽는 중")
    extras = []
    for u in sd.lines(q.get("drive")):
        s = sd.drive_source(u)
        extras.append(s)
        log(f"추가 자료 {s['title']} {len(s['text']):,}자")
    prompt = build_prompt(q, refsrc, func, figs, extras)
    log(f"자료 합계 {len(prompt):,}자 → Claude 작성 중 (보통 5~10분)")
    est = past_sec()
    run = lambda: sd.run_claude(prompt, label=f"통합테스트 TC 작성 · {q['product']}")
    try:
        r = waiting(45, 95, "Claude가 TC 작성 중", run, est) if "waiting" in globals() else run()
    except sd.BotError as e:
        raise BotError(str(e))
    data = parse_json(r.get("result", ""))
    tcs = data.get("tc") or []
    if not tcs:
        raise BotError("Claude가 TC 행을 만들지 못했습니다.")
    sec = round(time.time() - t0)
    os.makedirs(OUT, exist_ok=True)
    name = "tc-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    meta = {"name": name, "product": q["product"], "model": q["model"], "category": cat["name"], "folder": CFG["folders"][cat["folder"]],
            "author": q.get("author") or "", "notes": q.get("notes") or "",
            "ref": {"id": ref["id"], "name": ref["name"], "url": f"https://docs.google.com/spreadsheets/d/{ref['id']}/edit"},
            "mid_cols": [k for k, _ in refsrc["layout"]["mid"]],
            "sources": [refsrc["head"], func["head"]] + [s["head"] for s in figs] + [s["head"] for s in extras],
            "func": {"name": func["title"], "url": func["url"], "tab": func["tab"], "blocks": func["blocks"]},
            "chars": len(prompt), "sec": sec, "cost": r.get("total_cost_usd"), "created": datetime.datetime.now().isoformat(timespec="seconds"),
            "doc": None}
    json.dump({"meta": meta, "data": data}, open(os.path.join(OUT, name + ".json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(os.path.join(OUT, name + ".sources.txt"), "w", encoding="utf-8").write(prompt)
    progress(100, "완료")
    log(f"TC {len(tcs)}행 · 확인 필요 {len(data.get('review_points') or [])}건 · {sec // 60}분 {sec % 60}초" + (f" · ${r['total_cost_usd']:.2f}" if r.get("total_cost_usd") else ""))
    return meta, data


def load(name):
    p = os.path.join(OUT, os.path.basename(name or "") + ".json")
    if not name or not os.path.isfile(p):
        raise BotError("초안을 찾지 못했습니다. [TC 초안 만들기]부터 실행해 주세요.")
    d = json.load(open(p, encoding="utf-8"))
    return d["meta"], d["data"]


def save(meta, data):
    json.dump({"meta": meta, "data": data}, open(os.path.join(OUT, meta["name"] + ".json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def list_drafts():
    out = []
    if os.path.isdir(OUT):
        for n in sorted(os.listdir(OUT), reverse=True):
            if n.startswith("tc-") and n.endswith(".json"):
                try:
                    d = json.load(open(os.path.join(OUT, n), encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                m = d["meta"]
                out.append({"name": m["name"], "created": m["created"], "product": m["product"], "model": m["model"], "category": m["category"],
                            "count": len(d["data"].get("tc") or []), "doc": m.get("doc")})
    return out


# ---------- Google Drive에 문서 만들기 ----------
def wb(sid, reqs):
    for i in range(0, len(reqs), 400):
        qu.batch(sid, reqs[i:i + 400])


def rng(gid, r0, r1, c0, c1):
    return {"sheetId": gid, "startRowIndex": r0, "endRowIndex": r1, "startColumnIndex": c0, "endColumnIndex": c1}


def A1(tab, r, c):
    col = ""
    c += 1
    while c:
        c, x = divmod(c - 1, 26)
        col = chr(65 + x) + col
    return f"'{tab}'!{col}{r + 1}"


def find_label(rows, pat, maxr=80):
    for r, row in enumerate(rows[:maxr]):
        for c, v in enumerate(row):
            if re.fullmatch(pat, str(v).strip()):
                return r, c
    return None


def right_of(rows, r, c, default=2):
    row = rows[r] if r < len(rows) else []
    return next((x for x in range(c + 1, len(row)) if str(row[x]).strip()), c + default)


def create(name, log=print):
    meta, data = load(name)
    ov, tcs = data.get("overview") or {}, data.get("tc") or []
    model = meta["model"] or ", ".join(m.get("model", "") for m in ov.get("models") or [] if m.get("model")) or "TBD"
    title = f"제품-IoT App 연동 통합테스트_{meta['product']} ({model})_v0.1_{today('%Y%m%d')}"
    author = meta.get("author") or ""
    progress(5, "참고 문서 사본 만드는 중")
    f = qu.g("POST", f"{DRIVE}/{meta['ref']['id']}/copy?supportsAllDrives=true&fields=id,name,webViewLink", {"name": title, "parents": [meta["folder"]]})
    sid = f["id"]
    log(f"사본 생성: {title}")
    sh = qu.g("GET", f"{SHEETS}{sid}?fields=sheets(properties(sheetId,title),merges)")
    gid = {s["properties"]["title"]: s["properties"]["sheetId"] for s in sh["sheets"]}
    merges = {s["properties"]["title"]: s.get("merges", []) for s in sh["sheets"]}
    tabs = [t for t in (COVER_TAB, HIST_TAB, OVERVIEW_TAB, TC_TAB, ISSUE_TAB) if t in gid]
    vals = {t: qu.values(sid, t, "A1:AH1200") for t in tabs}
    put, clear = [], []

    # 표지: 제품명(모델) · 버전 · 최종 수정일
    rows = vals.get(COVER_TAB, [])
    t = find_label(rows, r".*연동\s*통합\s*테스트.*")
    if t:
        r = next((x for x in range(t[0] + 1, min(t[0] + 4, len(rows))) if cell(rows, x, t[1])), t[0] + 1)
        put.append((A1(COVER_TAB, r, t[1]), f"{meta['product']} ({model})"))
    t = find_label(rows, r"v\d+(\.\d+)*")
    if t:
        put.append((A1(COVER_TAB, *t), "v0.1"))
    t = find_label(rows, r"최종\s*수정일")
    if t:
        put.append((A1(COVER_TAB, t[0], right_of(rows, t[0], t[1])), today()))

    # 문서이력: 기존 이력 지우고 v0.1 초안 한 줄
    rows = vals.get(HIST_TAB, [])
    t = find_label(rows, r"번호")
    if t:
        hr = t[0]
        hc = {cell(rows, hr, c): c for c in range(len(rows[hr])) if cell(rows, hr, c)}
        last = max((r for r in range(hr + 1, len(rows)) if any(cell(rows, r, c) for c in hc.values() if c != t[1])), default=hr)
        c0, c1 = min(c for c in hc.values() if c != t[1]), max(hc.values())
        if last > hr:
            clear.append(f"'{HIST_TAB}'!{A1(HIST_TAB, hr + 1, c0).split('!')[1]}:{A1(HIST_TAB, last, c1).split('!')[1]}")
        for k, v in (("버전", "ver.0.1"), ("날짜", today()), ("내용", "초안 작성 (IoT업무봇 자동 작성)"), ("작성자", author)):
            if k in hc:
                put.append((A1(HIST_TAB, hr + 1, hc[k]), v))

    # 1. 개요: 프로젝트 정보·진행 방향·일정
    rows = vals.get(OVERVIEW_TAB, [])
    t = find_label(rows, r"프로젝트명")
    if t:
        hr = t[0]
        hc = {cell(rows, x, c): c for x in (hr, hr + 1) for c in range(len(rows[x]) if x < len(rows) else 0) if cell(rows, x, c)}
        end = next((r for r in range(hr + 2, len(rows)) if re.match(r"\s*1\.3", cell(rows, r, 1) or " ".join(str(v) for v in rows[r]))), hr + 6)
        data_rows = [r for r in range(hr + 2, end) if r < end - 1 or any(cell(rows, r, c) for c in hc.values())] or [hr + 2]
        c0, c1 = min(hc.values()), max(hc.values())
        clear.append(f"'{OVERVIEW_TAB}'!{A1(OVERVIEW_TAB, data_rows[0], c0).split('!')[1]}:{A1(OVERVIEW_TAB, data_rows[-1], c1).split('!')[1]}")
        first = {"프로젝트명": ov.get("project_name") or meta["product"], "프로젝트 코드": ov.get("project_code") or "TBD",
                 "지원 IoT 앱": ov.get("app") or "", "설명": ov.get("app_desc") or "", "지원 언어": ov.get("languages") or ""}
        for k, v in first.items():
            if k in hc:
                put.append((A1(OVERVIEW_TAB, data_rows[0], hc[k]), v))
        ms = ov.get("models") or [{"model": model}]
        for i, m in enumerate(ms):
            r = data_rows[min(i, len(data_rows) - 1)]
            if i >= len(data_rows) - 1 and i < len(ms) - 1 and len(ms) > len(data_rows):  # 줄이 모자라면 마지막 줄에 모아 쓴다
                rest = ms[i:]
                for k, key in (("모델명", "model"), ("자재코드", "material"), ("POP코드", "pop")):
                    if k in hc:
                        put.append((A1(OVERVIEW_TAB, r, hc[k]), "\n".join(str(x.get(key) or "TBD") for x in rest)))
                break
            for k, key in (("모델명", "model"), ("자재코드", "material"), ("POP코드", "pop")):
                if k in hc:
                    put.append((A1(OVERVIEW_TAB, r, hc[k]), m.get(key) or "TBD"))
    for lab, v in (("일시", "(TBD)"), ("대상 앱", ov.get("target_app")), ("해당 서비스 범위", ov.get("scope")), ("테스트 제외 항목", ov.get("excluded"))):
        t = find_label(rows, re.escape(lab))
        if t and v:
            put.append((A1(OVERVIEW_TAB, t[0], right_of(rows, t[0], t[1])), v))
    t = find_label(rows, r"진행\s*일자")
    if t:
        hr = t[0]
        done = next((c for c in range(len(rows[hr])) if cell(rows, hr, c) == "진행 여부"), None)
        app = ov.get("app") or ""
        for r in range(hr + 1, min(hr + 20, len(rows))):
            if not cell(rows, r, t[1] - 1) and not cell(rows, r, t[1]):
                break
            put.append((A1(OVERVIEW_TAB, r, t[1]), "(TBD)"))
            if done is not None and cell(rows, r, done):
                put.append((A1(OVERVIEW_TAB, r, done), ""))
            for c in range(len(rows[r])):
                v = cell(rows, r, c)
                if app and re.search(r"(IoCare\+|NECOA)앱", v):
                    put.append((A1(OVERVIEW_TAB, r, c), re.sub(r"(IoCare\+|NECOA)앱", app + "앱", v)))

    # 3. 이슈: 번호만 남기고 비움
    rows = vals.get(ISSUE_TAB, [])
    t = find_label(rows, r"No\.?")
    if t:
        hr = t[0]
        start = next((r for r in range(hr + 1, min(hr + 5, len(rows))) if re.fullmatch(r"\d+", cell(rows, r, t[1]))), hr + 3)
        last = max((r for r in range(start, len(rows)) if any(str(v).strip() for c, v in enumerate(rows[r]) if c != t[1])), default=start - 1)
        width = max(len(r) for r in rows[hr:hr + 3])
        if last >= start:
            clear.append(f"'{ISSUE_TAB}'!{A1(ISSUE_TAB, start, t[1] + 1).split('!')[1]}:{A1(ISSUE_TAB, last, width).split('!')[1]}")

    # 2. TC: 머리 정보 + 행 다시 만들기
    rows = vals[TC_TAB]
    lay = tc_layout(rows)
    t = find_label(rows, r"제품명", 15)
    if t:
        hr, vr = t[0], t[0] + 2
        hc = {cell(rows, hr, c): c for c in range(len(rows[hr])) if cell(rows, hr, c)}
        for k, v in (("제품명", meta["product"]), ("모델명", model), ("TC 작성자", f"IoT 기획팀 / {author}" if author else "IoT 기획팀")):
            if k in hc:
                put.append((A1(TC_TAB, vr, hc[k]), v))
        for r in range(hr, lay["hdr"]):
            for c in range(len(rows[r]) if r < len(rows) else 0):
                v = cell(rows, r, c)
                if re.fullmatch(r"\d{4}[.\-/]\s?\d{1,2}[.\-/]\s?\d{1,2}\.?|PASS|FAIL|N/?A|STG\.?[\d.]+.*|ITG\.?[\d.]+.*", v, re.I):
                    put.append((A1(TC_TAB, r, c), ""))
    D = lay["hdr"] + 2
    C = lay["cols"]
    width = max(len(r) for r in rows[lay["hdr"]:lay["hdr"] + 2])
    last = max((r for r in range(D, len(rows)) if any(str(v).strip() for v in rows[r])), default=D)
    N, M = len(tcs), last - D + 1
    g = gid[TC_TAB]
    hpat = sorted({(m["startColumnIndex"], m["endColumnIndex"]) for m in merges[TC_TAB]
                   if m["startRowIndex"] == D and m["endRowIndex"] == D + 1})
    reqs = [{"unmergeCells": {"range": rng(g, D, last + 1, 0, max(width, 26))}}]
    if N > M:
        reqs.append({"insertDimension": {"range": {"sheetId": g, "dimension": "ROWS", "startIndex": last + 1, "endIndex": last + 1 + N - M}, "inheritFromBefore": True}})
    elif N < M:
        reqs.append({"deleteDimension": {"range": {"sheetId": g, "dimension": "ROWS", "startIndex": D + N, "endIndex": last + 1}}})
    if N > 1:
        reqs.append({"copyPaste": {"source": rng(g, D, D + 1, 0, max(width, 26)), "destination": rng(g, D + 1, D + N, 0, max(width, 26)), "pasteType": "PASTE_FORMAT"}})
    for c0, c1 in hpat:
        reqs.append({"mergeCells": {"range": rng(g, D, D + N, c0, c1), "mergeType": "MERGE_ROWS"}})
    gc = C["테스트 구분"]
    i = 0
    while i < N:
        j = i + 1
        while j < N and not str(tcs[j].get("group") or "").strip():
            j += 1
        if j - i > 1 and not any(c0 <= gc < c1 for c0, c1 in hpat):
            reqs.append({"mergeCells": {"range": rng(g, D + i, D + j, gc, gc + 1), "mergeType": "MERGE_COLUMNS"}})
        i = j
    progress(35, "TC 행 서식 맞추는 중")
    wb(sid, reqs)
    qu.g("POST", f"{SHEETS}{sid}/values:batchClear", {"ranges": [f"'{TC_TAB}'!{A1(TC_TAB, D, 0).split('!')[1]}:{A1(TC_TAB, D + N - 1, max(width, 26) - 1).split('!')[1]}"] + clear})
    res_cols = [c for k, c in lay["result"] if k.upper() in ("AOS", "IOS", "IOS ")]
    note_col = next((c for k, c in lay["result"] if "비고" in k), None)
    for i, t in enumerate(tcs):
        r = D + i
        row = {C["시나리오 ID"]: t.get("id", ""), gc: t.get("group", ""), C["테스트 항목"]: t.get("item", ""), C["기대결과"]: t.get("expected", "")}
        for k, c in lay["mid"]:
            row[c] = (t.get("mid") or {}).get(k, "")
        if t.get("result"):
            for c in res_cols:
                row[c] = t["result"]
        if note_col is not None and t.get("note"):
            row[note_col] = t["note"]
        for c, v in row.items():
            if str(v).strip():
                put.append((A1(TC_TAB, r, c), str(v)))
    progress(60, "내용 쓰는 중")
    for i in range(0, len(put), 500):
        qu.g("POST", f"{SHEETS}{sid}/values:batchUpdate", {"valueInputOption": "RAW", "data": [{"range": a, "values": [[v]]} for a, v in put[i:i + 500]]})
    url = f"https://docs.google.com/spreadsheets/d/{sid}/edit"
    meta["doc"] = {"id": sid, "name": title, "url": url, "at": datetime.datetime.now().isoformat(timespec="seconds")}
    save(meta, data)
    progress(100, "완료")
    log(f"TC {N}행 작성 · 폴더 https://drive.google.com/drive/folders/{meta['folder']}")
    return meta
