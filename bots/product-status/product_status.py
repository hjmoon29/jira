#!/usr/bin/env python3
"""제품별 진행상황: 내가 담당하는 제품(행) × 진행 단계(열) 표.

- 단계 목록·상태 종류: config.json (stages, statuses)
- 데이터(내가 입력한 값): runs/product-status/products.json — 화면에서 직접 입력·수정
- Jira 연동(선택): 내가 담당인 '큰틀' 에픽을 제품으로 불러오기, 에픽 하위 문서 상태로 빈칸 채우기
"""
import datetime, glob, json, os, re, shutil, sys, time, uuid
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # bots/ (hub_progress)
from hub_progress import progress  # noqa: E402  화면 진행률 게이지

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.environ.get("BOT_HUB_RUNS") or os.path.join(HERE, "..", "..", "runs")
LOCAL_DIR = os.path.join(RUNS, "product-status")
DRIVE_SUB = os.path.join("IoT업무봇", "product-status")


def _drive_root():
    """PC 의 Google Drive '내 드라이브' 폴더 (Windows G:, Mac CloudStorage). 없으면 None"""
    env = os.environ.get("WORKBOT_DRIVE")
    if env:
        return env if os.path.isdir(env) else None
    cands = []
    for letter in "GHIJ":
        cands += [f"{letter}:/내 드라이브", f"{letter}:/My Drive"]
    home = os.path.expanduser("~")
    for base in sorted(glob.glob(os.path.join(home, "Library", "CloudStorage", "GoogleDrive-*"))):
        cands += [os.path.join(base, "내 드라이브"), os.path.join(base, "My Drive")]
    cands += [os.path.join(home, "Google Drive", "내 드라이브"), os.path.join(home, "Google Drive", "My Drive")]
    global DRIVE_TRIED
    cs = os.path.join(home, "Library", "CloudStorage")
    if sys.platform == "darwin":
        try:
            DRIVE_TRIED = "CloudStorage 안: " + (", ".join(os.listdir(cs)) or "비어 있음")
        except OSError as e:
            DRIVE_TRIED = f"CloudStorage 를 읽지 못함 ({e.__class__.__name__}: 권한 확인 필요)"
    return next((c for c in cands if os.path.isdir(c)), None)


DRIVE_TRIED = ""


def _data_dir():
    """Drive 가 있으면 Drive 공유 폴더(두 PC 가 같은 데이터), 없으면 PC 로컬 runs/"""
    root = _drive_root()
    if not root:
        return LOCAL_DIR
    d = os.path.join(root, DRIVE_SUB)
    try:
        os.makedirs(d, exist_ok=True)
        dst, src = os.path.join(d, "products.json"), os.path.join(LOCAL_DIR, "products.json")
        if not os.path.exists(dst) and os.path.isfile(src):
            shutil.copy2(src, dst)  # 첫 실행: 이 PC 의 기존 데이터를 Drive 로 옮김
        return d
    except OSError:
        return LOCAL_DIR


DATA_DIR = _data_dir()
DATA = os.path.join(DATA_DIR, "products.json")
CONFIG = os.path.join(HERE, "config.json")
JIRA_DIR = os.path.join(HERE, "..", "jira-tasks")
PROJECT = "IOCARERENEW2023"
EPIC_TYPE = "10000"  # '큰틀' — JQL 이 번역명을 못 읽어 id 로 검색


class BotError(Exception):
    pass


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def cfg():
    return json.load(open(CONFIG, encoding="utf-8"))


# ── Drive 앱이 없는 PC(Mac): Drive API 로 같은 'IoT업무봇/product-status/products.json' 을 주고받음 ──
# 인증: WORKBOT_DRIVE_API 폴더(기본 ~/.config/claude-daily)의 gmail.json(client_id·client_secret) + drive.json(refresh_token, drive 범위)
API_DIR = os.path.expanduser(os.environ.get("WORKBOT_DRIVE_API") or "~/.config/claude-daily")
FILE_ID_CACHE = os.path.join(LOCAL_DIR, ".drive_file_id")
SYNC = {"note": ""}


def _api_on():
    return DATA_DIR == LOCAL_DIR and all(os.path.isfile(os.path.join(API_DIR, f)) for f in ("gmail.json", "drive.json"))


def _http(method, url, token=None, body=None, ctype="application/json", raw=False):
    import urllib.request
    h = {"Content-Type": ctype}
    if token:
        h["Authorization"] = "Bearer " + token
    with urllib.request.urlopen(urllib.request.Request(url, body, h, method=method), timeout=20) as r:
        b = r.read()
    return b if raw else json.loads(b or b"{}")


def _api_token():
    import urllib.parse
    client = json.load(open(os.path.join(API_DIR, "gmail.json")))
    conf = json.load(open(os.path.join(API_DIR, "drive.json")))
    body = urllib.parse.urlencode({"client_id": client["client_id"], "client_secret": client["client_secret"],
                                   "refresh_token": conf["refresh_token"], "grant_type": "refresh_token"}).encode()
    return _http("POST", "https://oauth2.googleapis.com/token", body=body, ctype="application/x-www-form-urlencoded")["access_token"]


def _api_find(token, q):
    import urllib.parse
    url = "https://www.googleapis.com/drive/v3/files?" + urllib.parse.urlencode({
        "q": q + " and trashed = false", "fields": "files(id,name)", "pageSize": 10,
        "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"})
    return _http("GET", url, token)["files"]


def _api_file_id(token):
    try:
        return open(FILE_ID_CACHE).read().strip()
    except OSError:
        pass
    parent = None
    for name in DRIVE_SUB.replace("\\", "/").split("/"):
        hits = _api_find(token, f"name = '{name}' and mimeType = 'application/vnd.google-apps.folder'"
                         + (f" and '{parent}' in parents" if parent else ""))
        if not hits:
            return None
        parent = hits[0]["id"]
    hits = _api_find(token, f"name = 'products.json' and '{parent}' in parents")
    if not hits:
        return None
    os.makedirs(LOCAL_DIR, exist_ok=True)
    open(FILE_ID_CACHE, "w").write(hits[0]["id"])
    return hits[0]["id"]


def _api_push(d, token=None, fid=None):
    token = token or _api_token()
    fid = fid or _api_file_id(token)
    if not fid:
        raise OSError("Drive 에서 IoT업무봇/product-status/products.json 을 찾지 못함")
    body = json.dumps(d, ensure_ascii=False, indent=2).encode("utf-8")
    _http("PATCH", f"https://www.googleapis.com/upload/drive/v3/files/{fid}?uploadType=media&supportsAllDrives=true",
          token, body)


def _api_sync(local):
    """Drive 본과 이 PC 사본 중 'updated' 가 최신인 쪽으로 맞춤 (같으면 Drive 우선). 실패해도 이 PC 사본으로 계속"""
    try:
        token = _api_token()
        fid = _api_file_id(token)
        if not fid:
            SYNC["note"] = "Drive 파일을 못 찾아 이 PC 사본 사용"
            return local
        remote = json.loads(_http("GET", f"https://www.googleapis.com/drive/v3/files/{fid}?alt=media&supportsAllDrives=true",
                                  token, raw=True).decode("utf-8"))
        remote.setdefault("products", [])
        if (local.get("updated") or "") > (remote.get("updated") or ""):
            _api_push(local, token, fid)  # 지난번 올리기에 실패한 이 PC 변경분
            SYNC["note"] = "Drive 동기화됨 (이 PC 변경분 올림)"
            return local
        if remote != local:
            _write_local(remote)
        SYNC["note"] = "Drive 동기화됨"
        return remote
    except Exception as e:  # noqa: BLE001  네트워크·토큰 문제
        SYNC["note"] = f"Drive 동기화 실패, 이 PC 사본 사용 ({e.__class__.__name__})"
        return local


def _write_local(d):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = DATA + ".tmp"
    json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.replace(tmp, DATA)


def load():
    try:
        d = json.load(open(DATA, encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    d.setdefault("products", [])
    if _api_on():
        d = _api_sync(d)
    d["_scope_changed"] = apply_scope(d)
    return d


def save(d):
    d.pop("_scope_changed", None)
    _write_local(d)
    if _api_on():
        try:
            _api_push(d)
            SYNC["note"] = "Drive 동기화됨"
        except Exception as e:  # noqa: BLE001  다음 load 때 다시 올림
            SYNC["note"] = f"Drive 올리기 실패, 다음에 다시 시도 ({e.__class__.__name__})"


def board(d=None):
    d = d or load()
    if d.pop("_scope_changed", False):
        save(d)
    c = cfg()
    if DATA_DIR != LOCAL_DIR:
        store = "Google Drive · " + DATA
    elif _api_on():
        store = f"Google Drive (API · {SYNC['note'] or '동기화'}) · IoT업무봇/product-status/products.json"
    else:
        store = "이 PC (Google Drive 폴더를 못 찾음" + (f" — {DRIVE_TRIED}" if DRIVE_TRIED else "") + ") · " + DATA
    return {"stages": c["stages"], "statuses": c["statuses"], "products": d["products"],
            "updated": d.get("updated"), "jira_at": d.get("jira_at"), "store": store}


def find(d, pid):
    p = next((x for x in d["products"] if x["id"] == pid), None)
    if not p:
        raise BotError("제품을 찾지 못했습니다. 화면을 새로 고쳐 주세요.")
    return p


def touch(d, p=None):
    d["updated"] = now()
    if p is not None:
        p["updated"] = d["updated"]


# ── 직접 입력 ─────────────────────────────────────────────
def save_product(q):
    d = load()
    name = (q.get("name") or "").strip()
    if not name:
        raise BotError("제품명을 입력해 주세요.")
    pid = q.get("id")
    if pid:
        p = find(d, pid)
    else:
        p = {"id": uuid.uuid4().hex[:10], "cells": {}, "created": now()}
        d["products"].append(p)
    for k in ("name", "models", "region", "epic", "owner", "note"):
        if k in q:
            p[k] = (q.get(k) or "").strip()
    p["epic"] = (p.get("epic") or "").upper()
    if "done" in q:
        p["done"] = q.get("done") in (True, "true")
    touch(d, p)
    save(d)
    return d


def delete_product(pid):
    d = load()
    p = find(d, pid)
    if p.get("epic"):  # 지운 제품은 [Jira에서 내 제품 불러오기]로 다시 들어오지 않게
        ex = d.setdefault("excluded_epics", [])
        if p["epic"] not in ex:
            ex.append(p["epic"])
    d["products"] = [x for x in d["products"] if x["id"] != pid]
    touch(d)
    save(d)
    return d


def move_product(pid, step):
    d = load()
    ps = d["products"]
    i = next((n for n, x in enumerate(ps) if x["id"] == pid), None)
    if i is None:
        raise BotError("제품을 찾지 못했습니다.")
    j = i + (1 if step > 0 else -1)
    if 0 <= j < len(ps):
        ps[i], ps[j] = ps[j], ps[i]
        touch(d)
        save(d)
    return d



# ── DPP 1·2차 통합 ─────────────────────────────────────────
MERGED_MEMO = "1·2차 통합 DPP — 차수 구분 없이 한 번 진행 (1차 DPP 칸 참고)"


def dpp_merged(c):
    """1차 DPP 칸이 '통합'(차수 없는 단일 DPP)인지: 메모에 '통합'이 있으면"""
    return bool(c) and "통합" in (c.get("memo") or "")


def sync_dpp(cells):
    """단일 DPP(1·2차 통합)면 2차 DPP 칸을 1차와 같은 상태로 채우고 '통합' 안내. 직접 입력한 2차 칸은 그대로.
    통합이 풀리면 자동으로 넣었던 2차 칸은 지운다"""
    c1, c2 = cells.get("dpp"), cells.get("dpp2")
    auto = bool(c2) and c2.get("src") == "통합"
    if dpp_merged(c1):
        if c2 and not auto:
            return False
        new = {"status": c1.get("status") or "", "date": c1.get("date") or "", "memo": MERGED_MEMO,
               "link": c1.get("link") or "", "src": "통합", "at": now()}
        if not c2 or any(c2.get(k) != new[k] for k in ("status", "date", "memo", "link")):
            cells["dpp2"] = new
            return True
        return False
    if auto:
        cells.pop("dpp2", None)
        return True
    return False



# ── 향지·제품 범위: 해외향 전용 단계는 국내향에서 '해당없음' (직접 입력한 칸은 그대로) ──
def stage_scope(p, s):
    """이 제품에 해당하는 단계인지. 아니면 이유 문자열"""
    regs = s.get("regions")
    if regs and (p.get("region") or "국내향") not in regs:
        return f"{p.get('region') or '국내향'} 해당 없음 ({'·'.join(regs)} 전용 업무)"
    if s.get("only") and not re.search(s["only"], f"{p.get('name', '')} {p.get('models', '')}"):
        return "해당 제품 아님 (" + s.get("help", "").split("(")[-1].split(")")[0] + ")" if "만 해당" in s.get("help", "") else "해당 제품 아님"
    return ""


def apply_scope(d):
    changed = False
    for p in d.get("products", []):
        cells = p.setdefault("cells", {})
        for s in cfg()["stages"]:
            why, cur = stage_scope(p, s), cells.get(s["id"])
            if why:
                if not cur or (cur.get("src") == "자동" and cur.get("memo") != why):
                    cells[s["id"]] = {"status": "해당없음", "date": "", "memo": why, "link": "", "src": "자동", "at": now()}
                    changed = True
            elif cur and cur.get("src") == "자동" and cur.get("status") == "해당없음":
                cells.pop(s["id"], None)  # 향지를 바꾸면 자동 '해당없음'은 풀어 줌
                changed = True
    return changed


def save_cell(q):
    d = load()
    p = find(d, q.get("id"))
    stage = q.get("stage")
    if stage not in {s["id"] for s in cfg()["stages"]}:
        raise BotError("알 수 없는 단계입니다.")
    status = (q.get("status") or "").strip()
    cell = {"status": status, "date": (q.get("date") or "").strip(), "memo": (q.get("memo") or "").strip(),
            "link": (q.get("link") or "").strip(), "src": "직접", "at": now()}
    cells = p.setdefault("cells", {})
    if not any(cell[k] for k in ("status", "date", "memo", "link")) or (status == "미착수" and not any(cell[k] for k in ("date", "memo", "link"))):
        cells.pop(stage, None)  # 비우면 '미착수'(빈칸)로
    else:
        cells[stage] = cell
    if stage == "dpp":
        sync_dpp(cells)
    touch(d, p)
    save(d)
    return d


# ── Jira 연동 ─────────────────────────────────────────────
def jira():
    sys.path.insert(0, os.path.abspath(JIRA_DIR))
    try:
        import jira_tasks as jt  # noqa
    except Exception as e:
        raise BotError(f"Jira 모듈을 불러오지 못했습니다: {e}")
    if not os.path.isfile(jt.TOKEN_FILE):
        raise BotError(f"Jira 토큰이 없습니다 ({jt.TOKEN_FILE}). 'Jira 자동 생성' 설정을 먼저 해 주세요.")
    return jt


MODEL_RE = re.compile(r"^[A-Z]{2,}[0-9]{2,}[A-Z0-9-]*$")


def split_summary(summary, labels):
    # "[프로젝트][제품][BAS50-A] 더매너 비데 (스탠다드)" → ("더매너 비데 (스탠다드)", "BAS50-A")
    tags = re.findall(r"\[([^\]]*)\]", summary)
    name = re.sub(r"^(\s*\[[^\]]*\]\s*)+", "", summary).strip()
    models = [t for t in tags if MODEL_RE.match(t.strip())] or [l for l in labels if MODEL_RE.match(l)]
    return name, ", ".join(dict.fromkeys(m.strip() for m in models))


def import_jira():
    jt = jira()
    issues = jt.search(f"project = {PROJECT} AND issuetype = {EPIC_TYPE} AND assignee = currentUser() "
                       f"AND statusCategory != Done ORDER BY updated DESC", "summary,labels,status", 100)
    d = load()
    have = {(p.get("epic") or "").upper() for p in d["products"]} | {e.upper() for e in d.get("excluded_epics", [])}
    added = []
    for it in issues:
        if it["key"].upper() in have:
            continue
        name, models = split_summary(it["fields"]["summary"], it["fields"].get("labels") or [])
        region = "해외향" if re.search(r"대만|말련|말레이|중국|미국|ODM|해외", name) else "국내향"
        d["products"].append({"id": uuid.uuid4().hex[:10], "name": name, "models": models, "region": region,
                              "epic": it["key"], "note": "", "cells": {}, "created": now(), "updated": now()})
        added.append(f"{it['key']} {name}" + (f" ({models})" if models else ""))
    touch(d)
    save(d)
    return d, added, len(issues)


def status_of(issue):
    cat = (issue["fields"].get("status") or {}).get("statusCategory", {}).get("key")
    return {"done": "완료", "indeterminate": "진행중"}.get(cat, "예정")


def token(p):
    # 제품 구분용 이름: "[말련향] 아이콘 프로" → "아이콘프로" (머리말·괄호·띄어쓰기 제거)
    n = re.sub(r"^(\s*\[[^\]]*\]\s*)+", "", p.get("name") or "")
    return re.sub(r"\s|\(.*?\)", "", n).lower()


def jira_fill():
    """에픽 하위 문서 제목을 단계 키워드와 맞춰, 비어 있는 칸만 Jira 상태로 채운다 (직접 입력한 칸은 건드리지 않음)."""
    jt = jira()
    stages = cfg()["stages"]
    d = load()
    log, filled = [], 0
    targets = [p for p in d["products"] if (p.get("epic") or "").strip()]
    progress(3, f"Jira 확인 준비 (에픽 있는 제품 {len(targets)}개)")
    done_n = 0
    for p in d["products"]:
        epic = (p.get("epic") or "").strip()
        if not epic:
            continue
        progress(5 + 88 * done_n / max(len(targets), 1), f"{p['name']} 하위 문서 확인 중 ({done_n + 1}/{len(targets)})")
        done_n += 1
        try:
            try:  # 하위 문서가 에픽 링크 또는 상위(parent)로 묶여 있음
                kids = jt.search(f'"Epic Link" = {epic} OR parent = {epic}', "summary,status,resolutiondate", 500)
            except Exception:
                kids = jt.search(f'"Epic Link" = {epic}', "summary,status,resolutiondate", 500)
        except Exception as e:
            log.append(f"{p['name']}: Jira 조회 실패 — {e}")
            continue
        # 한 에픽을 여러 제품이 같이 쓰면(예: 말련향 2종): 내 제품명이 들어간 문서 + 어느 제품명도 없는 공통 문서만
        sibs = [token(x) for x in d["products"] if x is not p and (x.get("epic") or "").strip().upper() == epic.upper()]
        if sibs:
            me_t = token(p)
            sq = lambda k: re.sub(r"\s", "", k["fields"]["summary"]).lower()
            kids = [k for k in kids if (me_t and me_t in sq(k)) or not any(t and t in sq(k) for t in sibs)]
        cells = p.setdefault("cells", {})
        # 'Jira 프로젝트 생성' 칸: 에픽(큰틀) 자체의 생성일 + 하위 문서 수 (직접 입력한 칸은 그대로)
        cur = cells.get("jira_epic")
        if any(s["id"] == "jira_epic" for s in stages) and (not cur or cur.get("src") == "Jira"):
            try:
                ep = jt.search(f"key = {epic}", "summary,created", 1)
            except Exception:
                ep = []
            if ep:
                new = {"status": "완료", "date": ep[0]["fields"]["created"][:10],
                       "memo": f"{ep[0]['key']} {ep[0]['fields']['summary']} · 하위 문서 {len(kids)}건",
                       "link": f"https://jira.coway.com/browse/{ep[0]['key']}", "src": "Jira", "at": now()}
                if not cur or any(cur.get(k) != new[k] for k in ("status", "date", "memo")):
                    cells["jira_epic"] = new
                    filled += 1
                    log.append(f"{p['name']} · Jira 프로젝트 생성 → 완료 ({new['date']})")
        for s in stages:
            keys = [k.lower().replace(" ", "") for k in s.get("jira") or []]
            if not keys:
                continue
            hits = [k for k in kids if any(x in re.sub(r"\s", "", k["fields"]["summary"]).lower() for x in keys)]
            single = False
            if s["id"] == "dpp" and not hits:  # 차수 없는 'DPP' 문서 = 1·2차 통합 (2차 DPP 문서가 따로 없을 때)
                sq = lambda k: re.sub(r"\s", "", k["fields"]["summary"]).lower()
                plain = [k for k in kids if "dpp" in sq(k) and not re.search(r"[0-9]차dpp", sq(k))]
                if plain and not any("2차dpp" in sq(k) for k in kids):
                    hits, single = plain, True
            if not hits:
                continue
            sts = [status_of(h) for h in hits]
            st = "완료" if all(x == "완료" for x in sts) else "진행중" if any(x in ("진행중", "완료") for x in sts) else "예정"
            cur = cells.get(s["id"])
            if cur and cur.get("src") != "Jira":
                continue  # 직접 입력한 값 우선
            dates = [h["fields"].get("resolutiondate") or "" for h in hits if status_of(h) == "완료"]
            new = {"status": st, "date": (max(dates)[:10] if st == "완료" and dates else ""), "memo": ("[1·2차 통합] " if single else "") + ", ".join(f"{h['key']} {h['fields']['summary']}" for h in hits[:4]) + (" 외" if len(hits) > 4 else ""),
                   "link": f"https://jira.coway.com/browse/{hits[0]['key']}" if len(hits) == 1 else
                   f"https://jira.coway.com/issues/?jql=" + "key%20in%20(" + ",".join(h["key"] for h in hits) + ")",
                   "src": "Jira", "at": now()}
            if not cur or any(cur.get(k) != new[k] for k in ("status", "date", "memo")):
                cells[s["id"]] = new
                filled += 1
                log.append(f"{p['name']} · {s['label']} → {st} ({len(hits)}건)")
        if sync_dpp(cells):
            filled += 1
            log.append(f"{p['name']} · 2차 DPP → 1·2차 통합으로 1차와 같게 표시")
        p["updated"] = now()
    progress(95, "저장 중")
    d["jira_at"] = now()
    touch(d)
    save(d)
    return d, filled, log
