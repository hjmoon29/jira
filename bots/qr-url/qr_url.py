#!/usr/bin/env python3
"""앱 연결 QR·URL 등록 — 'CW_IoCare_IoT서비스 소개페이지 URL정의서' 갱신 + 제품별 QR코드 이미지 생성·업로드.

하는 일
1) 'URL QR코드 리스트' 탭 마지막 행 아래 빈 행에 제품(제품군·프로젝트명·모델명·매뉴얼·제품 내 사용·기타사항)을 추가
   (순서·URL 은 시트 수식이 자동 생성)
2) 모델명마다 QR코드 이미지(기존 파일과 같은 규격: 200×200 JPEG, 바깥 1px 흰 여백, ECC L, 품질 75)를 만들어
   'CW_IoT_IoCare2.0 제품별 QR코드 이미지' 폴더에 올리고, 시트 F열(이미지 링크)·J열(입력 일시)을 채움
3) '문서이력' 탭에 기록 — 오늘 날짜 버전 행이 있으면 같은 버전으로 이어 쓰고(작성자가 다르면 행을 나누고 버전·일자 병합),
   새 버전이면 원본을 같은 폴더에 사본으로 백업한 뒤 파일명 버전·일자를 올리고 새 행 추가

인증: Google Drive + Sheets 쓰기 권한이 있는 OAuth 토큰
  ~/.config/work-bot/credentials.json 의 "gdrive_write_token"(refresh_token 이 든 JSON 경로) + "gdrive_oauth"(클라이언트 파일)
  없으면 ~/.gmail-mcp/gsheets-token.json + ~/.gmail-mcp/gcp-oauth.keys.json 을 찾는다.
"""
import datetime
import io
import json
import math
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.abspath(os.environ.get("BOT_HUB_RUNS") or os.path.join(HERE, "..", "..", "runs"))
STATE = os.path.join(RUNS, "qr-url", "state.json")
CFG = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))

LIST_TAB = "URL QR코드 리스트"
HIST_TAB = "문서이력"
DEFAULT_NOTE = "신규 URL 정책_v2 적용 (https:// 추가)"
MODEL_RE = re.compile(r"^[A-Za-z0-9-]+(?:_[A-Za-z0-9-]+)*$")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 회사 프록시가 인증 헤더를 바꾸지 않게


class BotError(Exception):
    pass


# ---------- 상태(마지막 작성자 등) ----------
def state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(d):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(d, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


# ---------- Google 인증·호출 ----------
TOKEN = None


def _jload(p):
    try:
        return json.load(open(os.path.expanduser(p), encoding="utf-8"))
    except (OSError, ValueError):
        return None


def token():
    global TOKEN
    if TOKEN:
        return TOKEN
    c = _jload("~/.config/work-bot/credentials.json") or {}
    pairs = []
    if c.get("gdrive_write_token"):
        pairs.append((c["gdrive_write_token"], c.get("gdrive_oauth") or "~/.gmail-mcp/gcp-oauth.keys.json"))
    pairs.append(("~/.gmail-mcp/gsheets-token.json", "~/.gmail-mcp/gcp-oauth.keys.json"))
    for tp, kp in pairs:
        t, k = _jload(tp), _jload(kp)
        if not t or not k or not t.get("refresh_token"):
            continue
        k = k.get("installed") or k.get("web") or k
        body = urllib.parse.urlencode({"client_id": k["client_id"], "client_secret": k["client_secret"],
                                       "refresh_token": t["refresh_token"], "grant_type": "refresh_token"}).encode()
        r = http("POST", "https://oauth2.googleapis.com/token", body, {"Content-Type": "application/x-www-form-urlencoded"})
        scope = r.get("scope", "")
        if "auth/drive" in scope.replace("drive.readonly", "") or "spreadsheets" in scope:
            TOKEN = r["access_token"]
            return TOKEN
    raise BotError("Google Drive·Sheets 쓰기 권한 토큰이 없습니다. ~/.config/work-bot/credentials.json 에 "
                   "\"gdrive_write_token\"(drive·spreadsheets 범위 refresh_token 파일 경로)을 넣거나, "
                   "Claude Code에 'QR·URL 등록 봇 Google 쓰기 인증해줘'라고 요청하세요.")


def http(method, url, body=None, headers=None, raw=False):
    data = body if (body is None or isinstance(body, bytes)) else json.dumps(body).encode()
    h = dict(headers or {})
    if body is not None and not isinstance(body, bytes):
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data, method=method, headers=h)
    try:
        with OPENER.open(req, timeout=120) as r:
            b = r.read()
            return b if raw else (json.loads(b) if b else {})
    except urllib.error.HTTPError as e:
        raise BotError(f"{e.code} {method} {url.split('?')[0]}: {e.read().decode(errors='replace')[:300]}")


def g(method, url, body=None, raw=False, headers=None):
    return http(method, url, body, dict(headers or {}, Authorization="Bearer " + token()), raw)


Q = urllib.parse.quote
SHEETS = "https://sheets.googleapis.com/v4/spreadsheets/"
DRIVE = "https://www.googleapis.com/drive/v3/files"


def a1(tab, rng):
    return Q(f"'{tab}'!{rng}")


def values(sid, tab, rng, formula=False):
    opt = "&valueRenderOption=FORMULA" if formula else ""
    return g("GET", f"{SHEETS}{sid}/values/{a1(tab, rng)}?{opt}").get("values", [])


def put(sid, data):
    g("POST", f"{SHEETS}{sid}/values:batchUpdate", {"valueInputOption": "USER_ENTERED", "data": data})


def batch(sid, reqs):
    if reqs:
        g("POST", f"{SHEETS}{sid}:batchUpdate", {"requests": reqs})


# ---------- 문서 찾기 ----------
def find_doc():
    q = (f"name contains '{CFG['doc_name']}' and mimeType='application/vnd.google-apps.spreadsheet' "
         "and trashed=false and not name contains '사본'")
    r = g("GET", f"{DRIVE}?q={Q(q)}&orderBy=modifiedTime%20desc&pageSize=10&supportsAllDrives=true"
                 "&includeItemsFromAllDrives=true&corpora=allDrives&fields=files(id,name,modifiedTime,parents)")
    files = [f for f in r.get("files", []) if re.search(r"Ver\.?\s*\d", f["name"])]
    if not files:
        raise BotError(f"'{CFG['doc_name']}' 문서를 찾지 못했습니다.")

    def ver(f):
        m = re.search(r"Ver\.?\s*(\d+)\.(\d+)", f["name"])
        return (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    return max(files, key=ver)


def sheet_ids(sid):
    m = g("GET", f"{SHEETS}{sid}?fields=sheets.properties(sheetId,title)")
    return {s["properties"]["title"]: s["properties"]["sheetId"] for s in m["sheets"]}


# ---------- 입력 파싱 ----------
def parse_items(text):
    """한 줄에 제품 하나: 제품군 | 프로젝트명 | 모델명 | 매뉴얼(Y/N) | 제품 내 사용(Y/N) | 기타사항
    모델명이 아직 없으면 비우거나 TBD — 행만 넣고 URL·QR은 만들지 않는다."""
    items = []
    for n, line in enumerate((text or "").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        p = [x.strip() for x in re.split(r"\s*[|\t]\s*", line)]
        p += [""] * (6 - len(p))
        cat, proj, model, man, inprod, note = p[:6]
        if not cat or not proj:
            raise BotError(f"{n}번째 줄: 제품군과 프로젝트명은 꼭 적어 주세요. ({line})")
        if model.upper() in ("TBD", "-", "미정"):
            model = ""
        if model and not MODEL_RE.match(model):
            raise BotError(f"{n}번째 줄: 모델명 '{model}' — 영문·숫자·'-'만, 국가 구분은 '_국가코드 2글자'(예: CHP-7212N_V2_MY)")
        yn = lambda v, d: (v or d).strip().upper()[:1] if (v or d).strip().upper()[:1] in ("Y", "N") else d
        note = note or DEFAULT_NOTE
        if not model:
            note += " · 모델명 확정 후 입력"
        items.append({"cat": cat, "proj": proj, "model": model, "manual": yn(man, "Y"), "inprod": yn(inprod, "N"), "note": note})
    if not items:
        raise BotError("추가할 제품을 한 줄 이상 적어 주세요.")
    return items


# ---------- QR 이미지 ----------
def _ensure(mod, pkg):
    try:
        return __import__(mod)
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--user", pkg], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return __import__(mod)


def make_qr(url):
    """기존 QR 파일과 같은 규격: 200×200, 바깥 1px 흰 여백, 모듈 경계 floor(1 + k×198/n), ECC L, JPEG 품질 75·96dpi."""
    qrcode = _ensure("qrcode", "qrcode")
    _ensure("PIL", "Pillow")
    from PIL import Image
    q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, border=0)
    q.add_data(url)
    q.make(fit=True)
    m = q.get_matrix()
    n = len(m)
    b = [math.floor(1 + k * 198 / n) for k in range(n + 1)]
    b[-1] = 199
    mod = [None] * 200
    for k in range(n):
        for p in range(b[k], b[k + 1]):
            mod[p] = k
    im = Image.new("L", (200, 200), 255)
    px = im.load()
    for y in range(200):
        for x in range(200):
            if mod[x] is not None and mod[y] is not None and m[mod[y]][mod[x]]:
                px[x, y] = 0
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=75, dpi=(96, 96))
    data = buf.getvalue()
    verified = verify_qr(data, url)
    return data, verified


def verify_qr(data, url):
    """OpenCV 가 있으면 생성한 이미지를 다시 읽어 URL 이 맞는지 확인. 없으면 None."""
    try:
        import cv2
        import numpy as np
        from PIL import Image, ImageOps
    except ImportError:
        return None
    im = ImageOps.expand(Image.open(io.BytesIO(data)).convert("L"), border=30, fill=255)
    val = cv2.QRCodeDetector().detectAndDecode(np.array(im))[0]
    if val != url:
        raise BotError(f"QR 검증 실패: 읽은 값 '{val}' ≠ '{url}'")
    return True


def upload_jpg(name, data, folder):
    b = "----" + uuid.uuid4().hex
    meta = json.dumps({"name": name, "parents": [folder], "mimeType": "image/jpeg"})
    body = (f"--{b}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n--{b}\r\n"
            "Content-Type: image/jpeg\r\n\r\n").encode() + data + f"\r\n--{b}--".encode()
    f = g("POST", "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&supportsAllDrives=true&fields=id",
          body, headers={"Content-Type": "multipart/related; boundary=" + b})
    return f"https://drive.google.com/file/d/{f['id']}/view?usp=drivesdk"


def existing_qr(name, folder):
    q = f"'{folder}' in parents and name='{name}' and trashed=false"
    return g("GET", f"{DRIVE}?q={Q(q)}&supportsAllDrives=true&includeItemsFromAllDrives=true&fields=files(id)").get("files", [])


def sheet_now():
    return (datetime.datetime.now() - datetime.datetime(1899, 12, 30)).total_seconds() / 86400


# ---------- 리스트 탭 ----------
def list_rows(sid):
    """(rows[FORMULA], 마지막 데이터 행 번호, 모델→행 번호)"""
    rows = values(sid, LIST_TAB, "A1:J400", formula=True)
    last, models = 0, {}
    for i, r in enumerate(rows, 1):
        if i >= 5 and len(r) > 2 and str(r[2]).strip():
            last = i
            if len(r) > 3 and str(r[3]).strip():
                models[str(r[3]).strip().upper()] = i
    return rows, last, models


def plan(q, log=print):
    items = parse_items(q.get("items"))
    doc = find_doc()
    sid = doc["id"]
    rows, last, models = list_rows(sid)
    dup = [it["model"] for it in items if it["model"] and it["model"].upper() in models]
    if dup:
        raise BotError("이미 목록에 있는 모델명: " + ", ".join(f"{d}({models[d.upper()]}행)" for d in dup))
    start = last + 1
    for k, it in enumerate(items):
        it["row"] = start + k
        it["url"] = (CFG["url_prefix"] + it["model"]) if it["model"] else ""
    return doc, items, rows, last


def preview(q, log=print):
    doc, items, rows, last = plan(q, log)
    hist = history_plan(doc, q, items, preview_only=True)
    lines = [f"문서: {doc['name']}", f"'{LIST_TAB}' 탭 마지막 행 {last}행 아래에 추가합니다.", ""]
    for it in items:
        lines.append(f"  {it['row']}행 · {it['cat']} · {it['proj']} · {it['model'] or '(모델명 없음 — URL·QR 생략)'} · "
                     f"매뉴얼 {it['manual']} · 제품 내 사용 {it['inprod']}")
        if it["url"]:
            lines.append(f"        URL {it['url']}  →  QR {it['model']}.jpg 생성·업로드" if q.get("make_qr", True) else f"        URL {it['url']}")
    lines += ["", "문서이력: " + hist["desc"]]
    return {"doc": doc, "items": items, "hist": hist, "text": "\n".join(lines)}


# ---------- 문서이력 ----------
def history_plan(doc, q, items, preview_only=False):
    sid = doc["id"]
    rows = values(sid, HIST_TAB, "A1:F200")
    today = datetime.date.today().strftime("%Y.%m.%d")
    author = (q.get("author") or "").strip() or "IoT기획팀"
    content = (q.get("history") or "").strip() or default_history(items)
    if not content.lstrip().startswith("-"):
        content = " - " + content
    last_i, last_ver, last_date, last_author, ver_i = 0, "", "", "", 0
    for i, r in enumerate(rows, 1):
        r = r + [""] * (6 - len(r))
        if i > 3 and (r[1].strip() or r[4].strip()):
            last_i = i
            last_author = r[5].strip()
            if r[2].strip():
                last_ver, last_date, ver_i = r[2].strip(), r[3].strip(), i
    mode = q.get("version_mode") or "자동"
    same = (mode == "같은 버전에 이어 쓰기") or (mode == "자동" and last_date.rstrip(".") == today)
    nums = [int(r[1]) for r in rows[3:] if len(r) > 1 and str(r[1]).strip().isdigit()]
    num = (max(nums) + 1) if nums else 1
    if same:
        if last_author == author:
            desc = f"v{last_ver} {last_i}행 '변경 내용'에 이어 쓰기 (작성자 같음)"
            act = {"kind": "append", "row": last_i}
        else:
            desc = f"v{last_ver}({last_date}) 같은 버전 — {last_i + 1}행에 작성자별 새 행(번호 {num}), 버전·일자 칸 {ver_i}~{last_i + 1}행 병합"
            act = {"kind": "split", "row": last_i + 1, "ver_row": ver_i, "num": num}
    else:
        mj, mn = (last_ver.split(".") + ["0"])[:2] if last_ver else ("1", "-1")
        new_ver = f"{int(mj)}.{int(mn) + 1}"
        desc = f"새 버전 v{new_ver}({today}) — {last_i + 1}행 추가, 원본 사본 백업 후 파일명 버전·일자 변경"
        act = {"kind": "new", "row": last_i + 1, "num": num, "ver": new_ver}
    act.update(content=content, author=author, today=today, desc=desc)
    return act


def default_history(items):
    by = {}
    for it in items:
        by.setdefault(it["cat"], []).append(it["proj"] + ("" if it["model"] else "(모델명 TBD)"))
    return "\n".join(f" - {c} {len(p)}종({'·'.join(p)}) 추가" for c, p in by.items())


def write_history(doc, h, sids):
    sid, tab = doc["id"], sids[HIST_TAB]
    r = h["row"]
    if h["kind"] == "append":
        cur = values(doc["id"], HIST_TAB, f"E{r}")
        cur = cur[0][0] if cur and cur[0] else ""
        g("PUT", f"{SHEETS}{sid}/values/{a1(HIST_TAB, f'E{r}')}?valueInputOption=RAW", {"values": [[cur + "\n" + h["content"]]]})
        return
    if h["kind"] == "split":
        g("PUT", f"{SHEETS}{sid}/values/{a1(HIST_TAB, f'B{r}:F{r}')}?valueInputOption=RAW",
          {"values": [[str(h["num"]), "", "", h["content"], h["author"]]]})
        reqs = [{"unmergeCells": {"range": {"sheetId": tab, "startRowIndex": h["ver_row"] - 1, "endRowIndex": r, "startColumnIndex": 2, "endColumnIndex": 4}}}]
        for c in (2, 3):
            reqs.append({"mergeCells": {"mergeType": "MERGE_ALL", "range": {"sheetId": tab, "startRowIndex": h["ver_row"] - 1, "endRowIndex": r, "startColumnIndex": c, "endColumnIndex": c + 1}}})
        batch(sid, reqs)
        return
    # 새 버전: 원본 사본 백업(같은 폴더) → 파일명 버전·일자 변경 → 이력 행 추가
    g("POST", f"{DRIVE}/{sid}/copy?supportsAllDrives=true", {"name": doc["name"] + "의 사본"})
    ymd = datetime.date.today().strftime("%Y%m%d")
    new_name = re.sub(r"Ver\.?\s*\d+\.\d+_\d{8}", f"Ver.{h['ver']}_{ymd}", doc["name"])
    if new_name == doc["name"]:
        new_name = re.sub(r"Ver\.?\s*\d+\.\d+", f"Ver.{h['ver']}", doc["name"])
    g("PATCH", f"{DRIVE}/{sid}?supportsAllDrives=true", {"name": new_name})
    doc["name"] = new_name
    g("PUT", f"{SHEETS}{sid}/values/{a1(HIST_TAB, f'B{r}:F{r}')}?valueInputOption=RAW",
      {"values": [[str(h["num"]), h["ver"], h["today"], h["content"], h["author"]]]})


# ---------- 실행 ----------
def apply(q, log=print):
    p = preview(q, log)
    doc, items, h = p["doc"], p["items"], p["hist"]
    sid = doc["id"]
    sids = sheet_ids(sid)
    rows, _, _ = list_rows(sid)
    folder = CFG["qr_folder"]
    data, done = [], []
    for it in items:
        r = it["row"]
        prev = rows[r - 2] if len(rows) >= r - 1 else []
        cur = rows[r - 1] if len(rows) >= r else []
        # 순서(A)·URL(E) 수식이 비어 있으면 윗행 수식을 이 행 기준으로 복사
        for col, idx in (("A", 0), ("E", 4)):
            have = cur[idx] if len(cur) > idx else ""
            if not str(have).startswith("=") and len(prev) > idx and str(prev[idx]).startswith("="):
                f = re.sub(r"([A-Z])(\d+)", lambda m: m.group(1) + str(int(m.group(2)) + 1) if "$" not in m.group(0) else m.group(0), prev[idx])
                data.append({"range": f"'{LIST_TAB}'!{col}{r}", "values": [[f]]})
        data.append({"range": f"'{LIST_TAB}'!B{r}:D{r}", "values": [[it["cat"], it["proj"], it["model"]]]})
        data.append({"range": f"'{LIST_TAB}'!G{r}:I{r}", "values": [[it["manual"], it["inprod"], it["note"]]]})
        if it["model"] and q.get("make_qr", True):
            name = it["model"] + ".jpg"
            if existing_qr(name, folder):
                raise BotError(f"QR 폴더에 '{name}' 이(가) 이미 있습니다. 'QR 다시 만들기' 탭을 쓰세요.")
            img, ok = make_qr(it["url"])
            link = upload_jpg(name, img, folder)
            data.append({"range": f"'{LIST_TAB}'!F{r}", "values": [[link]]})
            data.append({"range": f"'{LIST_TAB}'!J{r}", "values": [[sheet_now()]]})
            it["qr"] = link
            log(f"QR 생성·업로드: {name}" + (" (스캔 검증 통과)" if ok else " (OpenCV 없음 — 스캔 검증 생략)"))
        done.append(it)
    put(sid, data)
    log(f"'{LIST_TAB}' {len(done)}행 추가")
    if q.get("write_history", True):
        write_history(doc, h, sids)
        log("문서이력: " + h["desc"])
    st = state()
    st["author"] = h["author"]
    save_state(st)
    return doc, done, h


def requr(q, log=print):
    """이미 목록에 있는 모델의 QR 이미지를 다시 만들어 새 파일로 올리고 링크 교체, 옛 파일은 휴지통."""
    model = (q.get("model") or "").strip()
    if not model:
        raise BotError("모델명을 적어 주세요.")
    doc = find_doc()
    sid = doc["id"]
    rows, _, models = list_rows(sid)
    r = models.get(model.upper())
    if not r:
        raise BotError(f"'{LIST_TAB}' 탭에서 모델명 '{model}' 을(를) 찾지 못했습니다.")
    url = values(sid, LIST_TAB, f"E{r}")[0][0]
    img, ok = make_qr(url)
    folder = CFG["qr_folder"]
    old = existing_qr(model + ".jpg", folder)
    link = upload_jpg(model + ".jpg", img, folder)
    put(sid, [{"range": f"'{LIST_TAB}'!F{r}", "values": [[link]]}, {"range": f"'{LIST_TAB}'!J{r}", "values": [[sheet_now()]]}])
    for f in old:  # 같은 ID 덮어쓰기는 Drive 미리보기가 옛 이미지로 남아 새 파일로 올리고 옛 파일은 휴지통
        g("PATCH", f"{DRIVE}/{f['id']}?supportsAllDrives=true", {"trashed": True})
    log(f"{r}행 {model}: QR 새로 만듦" + (" (스캔 검증 통과)" if ok else "") + (f", 옛 파일 {len(old)}개 휴지통" if old else ""))
    return doc, r, url, link


def find(q, log=print):
    doc = find_doc()
    rows = values(doc["id"], LIST_TAB, "A5:J400")
    kw = (q.get("q") or "").strip().lower()
    out = []
    for i, r in enumerate(rows, 5):
        r = r + [""] * (10 - len(r))
        if not str(r[2]).strip():
            continue
        if kw and kw not in " ".join(map(str, r[1:4])).lower():
            continue
        out.append([str(i), r[0], r[1], r[2], r[3], r[4], "있음" if r[5] else "-", r[6], r[7], r[8]])
    return doc, out
