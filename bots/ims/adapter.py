#!/usr/bin/env python3
# IoT업무봇 ↔ IMS 조회 연결. 입력: argv[1]=기능, stdin=화면 입력(JSON) / 출력: JSON
import datetime as dt
import json, os, platform, re, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import catalog as cat  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "qa"))
import qa  # noqa: E402  엑셀 쓰기(write_xlsx) 재사용
import ims  # noqa: E402
import writes as wr  # noqa: E402

RUNS = os.path.abspath(os.environ.get("BOT_HUB_RUNS") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "runs"))
OUT_DIR = os.path.join(RUNS, "ims")
SHOW_ROWS = 300
# 자주 나오는 열 이름 → 한글 (엑셀·화면 머리글)
KO = {"devModelCode": "POP 코드", "popCode": "POP 코드", "modelNm": "모델명", "displayModelNm": "표시 모델명", "productModel": "모델명",
      "productName": "제품명", "description": "설명", "prodTypeCode": "제품 유형", "prodTypeCodeNm": "제품 유형명",
      "prodCategoryCode": "앱 카테고리", "prodCategoryCodeNm": "앱 카테고리명", "prodFamilyNm": "제품군", "materialCode": "자재 코드",
      "flagshipModelCode": "대표 모델", "flagshipModelYn": "대표 모델 여부", "useYn": "사용", "sortNo": "순서", "serial": "시리얼",
      "macAddr": "MAC", "orderNo": "주문번호", "devModelNm": "모델명", "wifiFrmwrVer": "WiFi 펌웨어", "mcuFrmwrVer": "MCU 펌웨어",
      "creId": "등록자", "creDate": "등록일", "mdfId": "수정자", "mdfDate": "수정일", "title": "제목", "cntryNm": "국가",
      "salesCnt": "판매", "connCnt": "연결", "disConnCnt": "미연결", "svcCode": "서비스 코드", "protocolTypeCode": "프로토콜 유형",
      "groupId": "그룹 ID", "groupNm": "그룹명", "code": "코드", "codeNm": "코드명", "projectNm": "프로젝트명", "writer": "작성자",
      "replaceCycle": "교체 주기", "visitYn": "방문", "membershipYn": "멤버십", "salesYn": "판매", "imgUri": "이미지"}


def out(**kw):
    print(json.dumps(kw, ensure_ascii=False, default=str))
    sys.exit(0)


def _env(q):
    return q.get("env") or "개발"


def _cell(v):
    if isinstance(v, (dict, list)):
        if isinstance(v, list) and all(not isinstance(x, (dict, list)) for x in v):
            return ", ".join("" if x is None else str(x) for x in v)
        return json.dumps(v, ensure_ascii=False)[:2000]
    return v


def _table(title, rows):
    rows = [r if isinstance(r, dict) else {"값": r} for r in rows]
    keys = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    return {"title": title, "keys": keys, "columns": [KO.get(k, k) for k in keys],
            "rows": [[_cell(r.get(k)) for k in keys] for r in rows], "total": len(rows)}


def _save(tables, name):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{dt.datetime.now():%Y%m%d_%H%M%S}_{re.sub(r'[^0-9A-Za-z가-힣._-]+', '_', name)[:60]}.xlsx")
    sheets, used = [], set()
    for t in tables:
        sn = re.sub(r'[\\/:*?\[\]]', "_", t["title"])[:31] or "Sheet"
        while sn in used:
            sn = sn[:28] + f"_{len(used)}"
        used.add(sn)
        widths = [min(50, max(8, max([len(str(c))] + [len(str(r[i])) for r in t["rows"][:200]]) + 2)) for i, c in enumerate(t["columns"])]
        sheets.append((sn, t["columns"] or ["(데이터 없음)"], t["rows"], widths or [20]))
    qa.write_xlsx(path, sheets)
    return path


def _models_file(env):
    return os.path.join(RUNS, f"ims_models_{ {'개발': 'dev', '검증': 'stg'}.get(env, 'prd')}.json")


def _cache_models(s, env):
    try:
        rows = cat._get(s, "api", "/v1/device/popCodeNm/list")
        os.makedirs(RUNS, exist_ok=True)
        with open(_models_file(env), "w", encoding="utf-8") as f:
            json.dump([r.get("desc", "").strip() for r in rows if r.get("desc")], f, ensure_ascii=False)
    except Exception:
        pass


def _norm(q):
    q = {k: (v.strip() if isinstance(v, str) else v) for k, v in q.items()}
    m = re.match(r"\[([0-9A-Za-z]+)\]", q.get("model") or "")
    if m:
        q["model"] = m.group(1)
    q["prod_type"] = cat.PROD_TYPES.get(q.get("prod_type") or "", "")
    return q


def query(q):
    label = q.get("item") or ""
    if label not in cat.BY_LABEL:
        out(ok=False, text="조회 항목을 골라 주세요.")
    item_id, need, fn = cat.BY_LABEL[label]
    env = _env(q)
    q = _norm(q)
    try:
        with ims.Session(env) as s:
            res = fn(s, q)
            _cache_models(s, env)
    except (ims.BotError, cat.InputError) as e:
        out(ok=False, headline=f"❌ {e}", text=str(e), summary=str(e)[:120])
    tables = [_table(t, rows) for t, rows in res]
    name = label.split(" > ", 1)[-1].split(" (")[0] + (f"_{q['model']}" if q.get("model") else "")
    path = _save(tables, f"IMS{env}_{name}") if any(t["total"] for t in tables) else ""
    total = sum(t["total"] for t in tables)
    for t in tables:
        t["shown"] = min(t["total"], SHOW_ROWS)
        t["rows"] = t["rows"][:SHOW_ROWS]
        del t["keys"]
    head = f"✅ IMS({env}) {label} — {total:,}건" if total else f"⚪ IMS({env}) {label} — 조회 결과가 없습니다"
    out(ok=True, headline=head, view="table", data={"tables": tables, "file": path},
        text=f"엑셀: {path}" if path else "", summary=f"{label.split(' > ')[-1][:40]} {total:,}건 ({env})")


def _change_req(q):
    op = wr.BY_LABEL.get(q.get("op") or "")
    if not op:
        out(ok=False, text="작업을 골라 주세요.")
    try:
        return op, wr.build(op, q.get("path_args"), q.get("query"), q.get("body"))
    except wr.InputError as e:
        tpl = wr.template(op)
        hint = ["", "본문 틀 (조회 결과의 필드 이름 — 값을 채우고, 수정이면 대상 번호(…Seq 등)도 넣으세요):",
                json.dumps(tpl, ensure_ascii=False, indent=2)] if tpl and not (q.get("body") or "").strip() else []
        out(ok=False, headline=f"⚠️ {e}", text="\n".join([str(e)] + hint), summary=str(e)[:120])


def _describe(env, req):
    m, b, path, params, data = req
    url = ims.ENVS[env] + ims.PREFIX[b] + path + ("?" + "&".join(f"{k}={v}" for k, v in params.items()) if params else "")
    lines = [f"환경: {env}", f"요청: {m} {url}"]
    if data is not None:
        lines += ["본문:", json.dumps(data, ensure_ascii=False, indent=2)]
    return lines


def change_preview(q):
    """보낼 요청을 보여 주기만 한다(IMS 에 보내지 않음)."""
    env = _env(q)
    op, req = _change_req(q)
    warn = ["", "⚠️ 앱 운영 화면에 바로 반영되거나 여러 데이터를 한꺼번에 바꾸는 작업입니다."] if wr.HIGH.search(op[2]) else []
    out(ok=True, headline=f"👀 미리보기 — {q['op']} (아직 보내지 않았습니다)",
        text="\n".join(_describe(env, req) + warn + ["", "확인했으면 'IMS에 반영' 버튼을 누르세요. 본문 형식은 IMS 화면이 보내는 것과 같아야 합니다."]),
        summary=f"미리보기 {q['op'][:50]}", quiet=True)


def change_run(q):
    env = _env(q)
    op, req = _change_req(q)
    if env == "운영" and (q.get("confirm_env") or "").strip() != "운영":
        out(ok=False, headline="⛔ 운영 반영 확인이 필요합니다", text="운영에 반영하려면 '운영 확인' 칸에 운영 이라고 입력해 주세요.")
    m, b, path, params, data = req
    entry = {"env": env, "op": q["op"], "method": m, "base": b, "path": path, "params": params, "body": data}
    try:
        with ims.Session(env) as s:
            res = s.call(m, b, path, body=data, params=params)
    except ims.BotError as e:
        wr.log({**entry, "ok": False, "error": str(e)})
        out(ok=False, headline=f"❌ 반영하지 못했습니다 — {e}", text="\n".join(_describe(env, req) + ["", str(e)]), summary=str(e)[:120])
    wr.log({**entry, "ok": True, "response": res})
    out(ok=True, headline=f"✅ IMS({env})에 반영했습니다 — {q['op']}",
        text="\n".join(_describe(env, req) + ["", "응답:", json.dumps(res, ensure_ascii=False, indent=2)[:5000],
                                               "", f"기록: {os.path.join(OUT_DIR, 'changes.jsonl')}",
                                               "IMS 화면이나 '조회' 탭에서 결과를 확인하세요."]),
        summary=f"{env} 반영 {q['op'][:50]}")


def models(q):
    data = []
    for env in ims.ENVS:  # 자동완성은 어느 환경인지 모르고 불리므로 두 환경 목록을 합친다
        try:
            with open(_models_file(env), encoding="utf-8") as f:
                data += [m for m in json.load(f) if m not in data]
        except (OSError, ValueError):
            pass
    out(ok=True, data=data, quiet=True)


def open_file(q):
    p = os.path.abspath(q.get("file") or "")
    if not p.startswith(os.path.abspath(OUT_DIR)) or not os.path.exists(p):
        out(ok=False, text="파일을 찾을 수 없습니다.", quiet=True)
    target = p if q.get("what") != "folder" else os.path.dirname(p)
    if platform.system() == "Windows":
        os.startfile(target)  # noqa
    else:
        subprocess.Popen(["open", target])
    out(ok=True, text="열었습니다.", quiet=True)


def login(q):
    env = _env(q)
    try:
        ok = ims.login(env)
    except ims.BotError as e:
        out(ok=False, text=str(e))
    out(ok=ok, text=f"IMS({env}) 로그인 완료. 이제 조회를 쓸 수 있습니다. (IMS 세션이 끝나면 다시 로그인해야 합니다)" if ok
        else "5분 안에 로그인하지 않아 창을 닫았습니다. 다시 시도해 주세요.",
        summary=f"{env} 로그인 완료" if ok else "로그인 시간 초과")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    q = json.loads(sys.stdin.read() or "{}")
    {"query": query, "models": models, "open_file": open_file, "login": login,
     "change_preview": change_preview, "change_run": change_run}.get(
        action, lambda q: out(ok=False, text="알 수 없는 기능"))(q)
