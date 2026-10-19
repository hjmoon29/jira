#!/usr/bin/env python3
"""제품 프로젝트(에픽)에 하위 문서(Jira 작업)를 만드는 봇 (jira.coway.com)

  plan     만들 목록 미리보기 + 계획 파일 저장 (Jira에는 아무것도 만들지 않음)
  apply    계획 파일대로 실제 생성 (에픽에 같은·비슷한 제목이 있으면 건너뜀)
  command  자연어 명령 해석 → plan  예) "대만향 아이콘3 프로젝트 문서에 기본 하위 문서들을 생성해줘"
  epic     제품명으로 에픽 찾기
  user     이름·이메일로 Jira 사용자 찾기

하위 문서 제목 = "<머리말> <제품명> <문서 제목>"  예) [기획] 대만향 아이콘3 알림 정의
기본 하위 문서 목록·제품 등록(제품→에픽·담당자)은 config.json.

예)
  jira_tasks.py plan --product "대만향 아이콘3" --defaults
  jira_tasks.py plan --product "대만향 아이콘3" --doc "현지 인증 일정 정리" --doc "포토모드 법무 검토"
  jira_tasks.py command "대만향 아이콘3 프로젝트 문서에 '현지 인증 일정 정리' 하위 문서 만들어줘"
  jira_tasks.py apply <계획 파일>
"""
import argparse, collections, difflib, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request

BASE = "https://jira.coway.com/rest/api/2"
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")
TOKEN_FILE = os.path.expanduser("~/.config/jira-bot/token")
EPIC_LINK = "customfield_10100"


class BotError(Exception):
    pass


def call(method, path, body=None, params=None):
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    tok = open(TOKEN_FILE).read().strip()
    req = urllib.request.Request(url, json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        msg = e.read().decode(errors="replace")[:500]
        if e.code == 401:
            msg = f"토큰 인증 실패 — Jira 프로필 > 개인 액세스 토큰에서 새로 발급해 {TOKEN_FILE} 에 넣으세요"
        raise BotError(f"Jira 오류 {e.code} ({method} {path}): {msg}")


def search(jql, fields, limit=200):
    return call("GET", "/search", params={"jql": jql, "fields": fields, "maxResults": limit})["issues"]


def load_config():
    return json.load(open(CONFIG, encoding="utf-8"))


def save_config(c):
    for d in c.get("default_docs", []):
        if not (d.get("title") or "").strip():
            raise BotError("기본 하위 문서 제목이 비어 있는 항목이 있습니다")
    for p in c.get("products", []):
        if not (p.get("name") or "").strip():
            raise BotError("제품명이 비어 있는 항목이 있습니다")
    tmp = CONFIG + ".tmp"
    json.dump(c, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG)


def squash(s):
    return re.sub(r"\s+", "", s or "").lower()


def norm(s):
    # 비교용: 앞의 [기획]·[제품] 같은 머리말과 띄어쓰기·괄호를 무시
    return re.sub(r"[\s()（）]", "", re.sub(r"^(\[[^\]]*\]\s*)+", "", s))


def same_task(summary, have):
    # 이미 있는 작업: 같은 제목(머리말·띄어쓰기 무시) → 그 키, 매우 비슷한 제목 → "~키"
    n = norm(summary)
    for s, k in have.items():
        if norm(s) == n:
            return k, s
    best = max(have.items(), key=lambda it: difflib.SequenceMatcher(None, n, norm(it[0])).ratio(), default=None)
    if best and difflib.SequenceMatcher(None, n, norm(best[0])).ratio() >= 0.85:
        return "~" + best[1], best[0]
    return None, None


def find_user(q):
    hits = call("GET", "/user/search", params={"username": q, "maxResults": 10})
    return [{"name": u["name"], "displayName": u["displayName"]} for u in hits if u.get("active", True)]


def resolve_user(q):
    if not q:
        return None
    hits = find_user(q)
    exact = [u for u in hits if u["name"].lower() == q.lower()]
    if exact or len(hits) == 1:
        return (exact or hits)[0]
    if not hits:
        raise BotError(f"담당자 '{q}' 를 Jira에서 찾지 못했습니다")
    raise BotError(f"담당자 '{q}' 후보가 여러 명입니다: " + ", ".join(f"{u['displayName']}({u['name']})" for u in hits))


def registered(c, product):
    return next((p for p in c.get("products", []) if squash(p["name"]) == squash(product)), None)


def find_epic(c, product):
    """제품명 → 에픽 후보. 1) 설정에 등록된 제품 2) 그 제품명이 들어간 하위 문서들이 속한 에픽"""
    p = registered(c, product)
    if p and p.get("epic"):
        return [{"key": p["epic"].upper(), "summary": "", "why": "설정에 등록된 제품"}]
    q = product.replace('"', "")
    hits = search(f'project = {c["project"]} AND "Epic Link" is not EMPTY AND summary ~ "\\"{q}\\""', f"summary,{EPIC_LINK}")
    cnt = collections.Counter(i["fields"][EPIC_LINK] for i in hits if squash(product) in squash(i["fields"]["summary"]))
    out = []
    for key, n in cnt.most_common(5):
        e = call("GET", f"/issue/{key}", params={"fields": "summary"})
        out.append({"key": key, "summary": e["fields"]["summary"], "why": f"'{product}' 하위 문서 {n}개가 있는 에픽"})
    return out


def parse_command(text):
    """자연어 명령 → {product, defaults, titles}
    예) 대만향 아이콘3 프로젝트 문서에 기본 하위 문서들을 생성해줘
        대만향 아이콘3 문서에 '현지 인증 일정 정리', '포토모드 법무 검토' 하위 문서 만들어줘
        대만향 아이콘3에 현지 인증 일정 정리 하위 문서 추가"""
    t = text.strip()
    m = re.match(r"^\s*(.+?)\s*(?:프로젝트\s*)?(?:문서\s*|에픽\s*)?(?:에다가|에다|에)\s+(.+)$", t)
    if not m:
        raise BotError("명령을 이해하지 못했습니다. 예) 대만향 아이콘3 프로젝트 문서에 기본 하위 문서들을 생성해줘")
    product, rest = m.group(1).strip(), m.group(2).strip()
    titles = re.findall(r"[\"'“”‘’「『]([^\"'“”‘’」』]+)[\"'“”‘’」』]", rest)
    if not titles and not re.search(r"기본", rest):
        mm = re.match(r"^(?:별도\s*)?(?:하위\s*문서\s*)?[:：]?\s*(.+?)\s*(?:이라는|라는)?\s*(?:별도\s*)?(?:하위\s*)?(?:문서|작업|태스크)", rest)
        if mm:
            titles = [x.strip() for x in re.split(r"\s*[,，]\s*", mm.group(1)) if x.strip()]
    titles = [x.strip() for x in titles if x.strip()]
    defaults = bool(re.search(r"기본", rest)) and not titles
    if not defaults and not titles:
        raise BotError("기본 하위 문서인지, 만들 문서 제목이 무엇인지 알 수 없습니다. 제목은 따옴표로 감싸 주세요.")
    return {"product": product, "defaults": defaults, "titles": titles}


def fill(s, product):
    return (s or "").replace("{제품}", product)


def plan(product, epic=None, assignee=None, defaults=False, titles=(), out=None):
    c = load_config()
    product = product.strip()
    if not product:
        raise BotError("제품명을 입력하세요")
    reg = registered(c, product)
    if not epic:
        cands = find_epic(c, product)
        if len(cands) != 1:
            raise BotError(
                (f"'{product}' 의 프로젝트 에픽을 찾지 못했습니다." if not cands else f"'{product}' 에픽 후보가 여러 개입니다: "
                 + ", ".join(f"{x['key']}({x['summary'][:30]})" for x in cands))
                + " 에픽 키를 직접 입력하거나 설정에서 제품을 등록하세요.")
        epic = cands[0]["key"]
    epic = epic.strip().upper()
    e = call("GET", f"/issue/{epic}", params={"fields": "summary,issuetype,project,assignee"})
    if e["fields"]["issuetype"]["name"] != "큰틀":
        raise BotError(f"{epic} 은 에픽(큰틀)이 아니라 '{e['fields']['issuetype']['name']}' 입니다")

    docs = []
    if defaults:
        docs += [dict(d) for d in c["default_docs"]]
    docs += [{"prefix": c.get("custom_prefix", "[기획]"), "title": t.strip(), "description": "", "custom": True} for t in titles if t.strip()]
    if not docs:
        raise BotError("만들 하위 문서가 없습니다 (기본 하위 문서 또는 제목 입력)")

    owner = resolve_user(assignee or (reg or {}).get("assignee"))
    if not owner and e["fields"].get("assignee"):
        a = e["fields"]["assignee"]
        owner = {"name": a["name"], "displayName": a["displayName"]}

    children = search(f'"Epic Link" = {epic}', "summary,labels,components", 500)
    # 같은 제품명이 들어간 문서끼리만 비교 (다른 모델의 같은 문서를 중복으로 보지 않게)
    have = {i["fields"]["summary"].strip(): i["key"] for i in children if squash(product) in squash(i["fields"]["summary"])}
    # 레이블·구성요소: 등록 제품 값 → 이 에픽의 기존 하위 문서에서 가장 많이 쓴 값 → 설정 기본값
    common = lambda k, f: collections.Counter(tuple(f(x) for x in i["fields"][k]) for i in children if i["fields"][k]).most_common(1)
    lab = common("labels", lambda x: x)
    comp = common("components", lambda x: x["name"])
    labels = (reg or {}).get("labels") or (list(lab[0][0]) if lab else c["labels"])
    components = list(comp[0][0]) if comp else c["components"]

    tasks = []
    for d in docs:
        x = {"summary": re.sub(r"\s+", " ", f"{d.get('prefix', '')} {product} {d['title']}").strip(),
             "description": fill(d.get("description"), product), "assignee": owner}
        k, title = same_task(x["summary"], have)
        if k:
            x["skip"], x["skip_title"] = k, title
        tasks.append(x)
    p = {"project": e["fields"]["project"]["key"], "issuetype": c["issuetype"], "product": product,
         "epic": {"key": epic, "summary": e["fields"]["summary"]}, "labels": labels, "components": components, "tasks": tasks}
    out = out or os.path.join(os.getcwd(), f"jira-plan-{time.strftime('%Y%m%d-%H%M%S')}.json")
    json.dump(p, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return p, out


def apply(path):
    p = json.load(open(path, encoding="utf-8"))
    base = {"project": {"key": p["project"]}, "issuetype": {"name": p["issuetype"]}, "labels": p["labels"],
            "components": [{"name": c} for c in p["components"]], EPIC_LINK: p["epic"]["key"]}
    log = []
    for x in p["tasks"]:
        if x.get("skip") or x.get("created"):
            continue
        f = dict(base, summary=x["summary"])
        if x.get("description"):
            f["description"] = x["description"]
        if x.get("assignee"):
            f["assignee"] = {"name": x["assignee"]["name"]}
        x["created"] = call("POST", "/issue", {"fields": f})["key"]
        log.append(f"  생성 {x['created']}  {x['summary']}")
        json.dump(p, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)  # 끊겨도 다시 실행하면 이어서
    return p, log


def plan_text(p, out):
    who = lambda u: u["displayName"] if u else "(미지정)"
    new = [x for x in p["tasks"] if not x.get("skip")]
    L = [f"제품 {p['product']} · 에픽 {p['epic']['key']} {p['epic']['summary']}",
         f"레이블 {', '.join(p['labels'])} · 구성요소 {', '.join(p['components'])}",
         f"\n만들 하위 문서 {len(new)}개" + (f" (이미 있어 건너뜀 {len(p['tasks']) - len(new)}개)" if len(new) < len(p["tasks"]) else "")]
    for i, x in enumerate(p["tasks"], 1):
        s = x.get("skip") or ""
        mark = f"  [비슷한 문서 있음 {s[1:]}: {x['skip_title']}]" if s.startswith("~") else f"  [있음 {s}]" if s else ""
        L.append(f"  {i:2}. {x['summary']}  — {who(x['assignee'])}{mark}")
    L.append(f"\n계획 파일: {out}\n실제 생성: jira_tasks.py apply {out}")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="결과를 JSON으로 출력 (봇 허브용)")
    sp = ap.add_subparsers(dest="cmd", required=True)
    pp = sp.add_parser("plan")
    pp.add_argument("--product", required=True)
    pp.add_argument("--epic")
    pp.add_argument("--assignee")
    pp.add_argument("--defaults", action="store_true", help="기본 하위 문서 전체")
    pp.add_argument("--doc", action="append", default=[], help="별도 하위 문서 제목 (여러 번)")
    pp.add_argument("--out")
    cp = sp.add_parser("command")
    cp.add_argument("text")
    cp.add_argument("--out")
    sp.add_parser("apply").add_argument("plan_file")
    sp.add_parser("epic").add_argument("product")
    sp.add_parser("user").add_argument("query")
    a = ap.parse_args()
    try:
        if a.cmd in ("plan", "command"):
            if a.cmd == "command":
                q = parse_command(a.text)
                p, out = plan(q["product"], defaults=q["defaults"], titles=q["titles"], out=a.out)
            else:
                p, out = plan(a.product, a.epic, a.assignee, a.defaults, a.doc, a.out)
            print(json.dumps({"plan": p, "plan_file": out}, ensure_ascii=False) if a.json else plan_text(p, out))
        elif a.cmd == "apply":
            p, log = apply(a.plan_file)
            msg = "\n".join(log) + f"\n\n완료: {len(log)}개 생성 → https://jira.coway.com/browse/{p['epic']['key']}"
            print(json.dumps({"plan": p, "text": msg}, ensure_ascii=False) if a.json else msg)
        elif a.cmd == "epic":
            r = find_epic(load_config(), a.product)
            print(json.dumps(r, ensure_ascii=False) if a.json else "\n".join(f"{x['key']}  {x['summary']}  ({x['why']})" for x in r) or "찾지 못함")
        else:
            r = find_user(a.query)
            print(json.dumps(r, ensure_ascii=False) if a.json else "\n".join(f"{u['displayName']}  {u['name']}" for u in r) or "찾지 못함")
    except BotError as e:
        if a.json:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
        else:
            print(e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
