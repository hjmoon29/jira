#!/usr/bin/env python3
"""제품별 진행상황: 내가 담당하는 제품(행) × 진행 단계(열) 표.

- 단계 목록·상태 종류: config.json (stages, statuses)
- 데이터(내가 입력한 값): runs/product-status/products.json — 화면에서 직접 입력·수정
- Jira 연동(선택): 내가 담당인 '큰틀' 에픽을 제품으로 불러오기, 에픽 하위 문서 상태로 빈칸 채우기
"""
import datetime, glob, json, os, re, shutil, sys, time, uuid

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
    return next((c for c in cands if os.path.isdir(c)), None)


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


def load():
    try:
        d = json.load(open(DATA, encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    d.setdefault("products", [])
    return d


def save(d):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = DATA + ".tmp"
    json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.replace(tmp, DATA)


def board(d=None):
    d = d or load()
    c = cfg()
    return {"stages": c["stages"], "statuses": c["statuses"], "products": d["products"],
            "updated": d.get("updated"), "jira_at": d.get("jira_at")}


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
        raise BotError(f"Jira 토큰이 없습니다 ({jt.TOKEN_FILE}). 'JIRA 자동 생성기' 설정을 먼저 해 주세요.")
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
    for p in d["products"]:
        epic = (p.get("epic") or "").strip()
        if not epic:
            continue
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
        for s in stages:
            keys = [k.lower().replace(" ", "") for k in s.get("jira") or []]
            if not keys:
                continue
            hits = [k for k in kids if any(x in re.sub(r"\s", "", k["fields"]["summary"]).lower() for x in keys)]
            if not hits:
                continue
            sts = [status_of(h) for h in hits]
            st = "완료" if all(x == "완료" for x in sts) else "진행중" if any(x in ("진행중", "완료") for x in sts) else "예정"
            cur = cells.get(s["id"])
            if cur and cur.get("src") != "Jira":
                continue  # 직접 입력한 값 우선
            dates = [h["fields"].get("resolutiondate") or "" for h in hits if status_of(h) == "완료"]
            new = {"status": st, "date": (max(dates)[:10] if st == "완료" and dates else ""), "memo": ", ".join(f"{h['key']} {h['fields']['summary']}" for h in hits[:4]) + (" 외" if len(hits) > 4 else ""),
                   "link": f"https://jira.coway.com/browse/{hits[0]['key']}" if len(hits) == 1 else
                   f"https://jira.coway.com/issues/?jql=" + "key%20in%20(" + ",".join(h["key"] for h in hits) + ")",
                   "src": "Jira", "at": now()}
            if not cur or any(cur.get(k) != new[k] for k in ("status", "date", "memo")):
                cells[s["id"]] = new
                filled += 1
                log.append(f"{p['name']} · {s['label']} → {st} ({len(hits)}건)")
        p["updated"] = now()
    d["jira_at"] = now()
    touch(d)
    save(d)
    return d, filled, log
