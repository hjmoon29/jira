"""문서이력 → Jira·제품별 진행상황 자동 반영 (IoT업무봇)

모델별 기본정보 · IoT기능정의서(CW_IoCare_제품_기능정의서)의 '문서이력' 탭에서 새로 생긴 행을 읽어
  1) 내 담당 제품과 관련된 내용만 골라
  2) 그 제품 에픽의 Jira 하위 문서(제품 기본 정보 / 제품 기능 정의 / POP)에 댓글 + 상태 변경
     - 모델별 기본정보: 2.0 탭의 제품 행에 TBD·빈칸이 남아 있으면 '진행 중', 다 채워졌으면 '해결'
     - POP: 제품 행(색상 포함) POP 칸이 모두 채워지면 '해결'
     - IoT기능정의서: '할 일'이면 '진행 중' (완료는 사람이 판단)
     - 상태는 앞으로만 움직임(되돌리지 않음). 내가 담당이 아닌 문서는 건드리지 않고 알림에만 적음
  3) 제품별 진행상황 칸(모델별 기본정보·POP·IoT 기능정의서) 갱신 (직접 입력한 칸은 그대로)
  4) 한 일을 메일 + Windows 알림으로 보냄

사용: python doc_sync.py [--dry-run] [--since 2026-11-01] [--no-notify]
설정: config.json 의 "doc_sync"
"""
import argparse, base64, datetime, html, json, os, re, subprocess, sys, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..", "jira-tasks")]
import product_status as ps  # noqa: E402
import jira_tasks as jt  # noqa: E402

STATE = os.path.join(ps.DATA_DIR, "doc_sync_state.json")
LOG = os.path.join(ps.LOCAL_DIR, "doc_sync.log")
GMAIL_CONF = os.path.expanduser("~/.config/claude-daily/gmail.json")
SHEETS_TOKEN = os.path.expanduser("~/.gmail-mcp/gsheets-token.json")
OAUTH_KEYS = os.path.expanduser("~/.gmail-mcp/gcp-oauth.keys.json")
ORDER = {"할 일": 0, "진행 중": 1, "In Review": 2, "검토중": 2, "해결": 3, "완료": 3, "닫힘": 3}
TRANS = {"진행 중": "진행 중", "해결": "완료"}  # 목표 상태 → 전환 이름
CELL_ST = {"진행 중": "진행중", "해결": "완료", "할 일": "예정"}


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def conf():
    return ps.cfg().get("doc_sync") or {}


# ---------- Google Sheets ----------
_TOK = None


def sheets(path):
    global _TOK
    if not _TOK:
        t = json.load(open(SHEETS_TOKEN, encoding="utf-8"))
        k = json.load(open(OAUTH_KEYS, encoding="utf-8"))
        k = k.get("installed") or k.get("web")
        body = urllib.parse.urlencode({"client_id": k["client_id"], "client_secret": k["client_secret"],
                                       "refresh_token": t["refresh_token"], "grant_type": "refresh_token"}).encode()
        _TOK = json.load(urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", body), timeout=30))["access_token"]
    req = urllib.request.Request("https://sheets.googleapis.com/v4/spreadsheets/" + path, headers={"Authorization": "Bearer " + _TOK})
    return json.load(urllib.request.urlopen(req, timeout=60))


def values(sid, tab):
    return sheets(f"{sid}/values/{urllib.parse.quote(tab)}").get("values", [])


def title_of(sid):
    return sheets(f"{sid}?fields=properties.title")["properties"]["title"]


# ---------- 문서이력 ----------
def parse_date(s):
    n = re.findall(r"\d+", s or "")
    if len(n) >= 3 and len(n[0]) == 4:
        try:
            return datetime.date(int(n[0]), int(n[1]), int(n[2]))
        except ValueError:
            return None
    return None


def history(doc):
    """[{key, ver, date, text, author}] — 열: 번호 | 버전 | 변경 일자 | 변경 내용 | 작성자"""
    out = []
    for r in values(doc["sheet"], doc.get("history_tab") or "문서이력"):
        r = (r + [""] * 5)[:5]
        d = parse_date(r[2])
        if not d or not r[3].strip():
            continue
        ver = r[1].strip()
        out.append({"key": f"{doc['id']}|{ver}|{d}", "ver": ver, "date": d, "text": r[3], "author": r[4].strip()})
    return out


def norm(s):
    return re.sub(r"[\s_\-·()]+", "", (s or "")).lower()


def blocks(text):
    """변경 내용을 항목 묶음으로: '- ' 줄 + 뒤따르는 '>'·들여쓰기 줄. <탭> 머리말은 붙여 둠"""
    out, cur, head = [], None, ""
    for raw in text.splitlines():
        line = raw.rstrip()
        s = line.strip()
        if not s:
            continue
        if s.startswith("<") and ">" in s and not s.startswith(">"):
            head = s[: s.index(">") + 1]
            rest = s[s.index(">") + 1:].strip()
            if rest:
                cur = [head + " " + rest]
                out.append(cur)
            continue
        if s.startswith("-") or s.startswith("•"):
            cur = [(head + " " if head else "") + s]
            out.append(cur)
        elif cur is not None:
            cur.append(s)
        else:
            cur = [s]
            out.append(cur)
    return ["\n".join(b) for b in out]


def related(entry, words):
    w = [norm(x) for x in words if x]
    hit = [b for b in blocks(entry["text"]) if any(x in norm(b) for x in w)]
    if hit:
        return hit
    return [entry["text"].strip()] if any(x in norm(entry["text"]) for x in w) else []


# ---------- 모델별 기본정보 2.0 탭 완성도 ----------
MAIN_COLS = {3: "제품명", 4: "노출 모델명", 5: "모델명", 6: "자재코드", 8: "POP"}
COLOR_COLS = {5: "모델명", 6: "자재코드", 8: "POP"}


def bad(v):
    v = (v or "").strip()
    return not v or v.upper() in ("TBD", "-") or "미정" in v or "TBD" in v.upper()


def model_rows(table, code):
    """프로젝트코드(B열) 행 + 바로 아래 B열이 빈 색상 행들"""
    for i, r in enumerate(table):
        if len(r) > 1 and r[1].strip() == code:
            rows = [r]
            for x in table[i + 1:]:
                if (len(x) > 1 and x[1].strip()) or not (len(x) > 5 and x[5].strip()):
                    break
                rows.append(x)
            return i + 1, rows
    return None, []


def completeness(table, code):
    row_no, rows = model_rows(table, code)
    if not rows:
        return None
    miss, pop_miss = [], []
    for n, r in enumerate(rows):
        r = r + [""] * 12
        cols = MAIN_COLS if n == 0 else COLOR_COLS
        for c, name in cols.items():
            if bad(r[c]):
                (pop_miss if c == 8 else miss).append(f"{r[5] or r[2] or code} {name}")
    first = rows[0] + [""] * 12
    return {"row": row_no, "n": len(rows), "missing": miss + pop_miss, "pop_missing": pop_miss,
            "open": first[9].strip(), "release": first[10].strip()}


# ---------- Jira ----------
def kids(epic):
    try:
        return jt.search(f'"Epic Link" = {epic} OR parent = {epic}', "summary,status,assignee", 500)
    except Exception:
        return jt.search(f'"Epic Link" = {epic}', "summary,status,assignee", 500)


def pick(issues, p, d, kws):
    """에픽 하위 문서 중 키워드가 든 것. 여러 제품이 같은 에픽이면 내 제품명이 든 문서 + 제품명 없는 공통 문서"""
    epic = (p.get("epic") or "").upper()
    sibs = [ps.token(x) for x in d["products"] if x is not p and (x.get("epic") or "").upper() == epic]
    me_t = ps.token(p)
    sq = lambda i: norm(i["fields"]["summary"])
    out = [i for i in issues if any(norm(k) in sq(i) for k in kws)]
    if sibs:
        mine = [i for i in out if me_t and me_t in sq(i)]
        common = [i for i in out if not any(t and t in sq(i) for t in sibs) and i not in mine]
        out = mine or common
    return out


def status_name(i):
    return i["fields"]["status"]["name"]


def assignee(i):
    return ((i["fields"].get("assignee") or {}).get("name") or "").lower()


# ---------- 알림 ----------
def send_mail(subject, body_html):
    from email.mime.text import MIMEText
    c = json.load(open(GMAIL_CONF, encoding="utf-8"))
    body = urllib.parse.urlencode({"client_id": c["client_id"], "client_secret": c["client_secret"],
                                   "refresh_token": c["refresh_token"], "grant_type": "refresh_token"}).encode()
    tok = json.load(urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", body), timeout=30))["access_token"]
    msg = MIMEText(body_html, "html", "utf-8")
    msg["To"] = conf().get("mail_to") or c.get("to")
    msg["Subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    req = urllib.request.Request("https://gmail.googleapis.com/gmail/v1/users/me/messages/send", json.dumps({"raw": raw}).encode(),
                                 {"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=30)
    return msg["To"]


def toast(title, text):
    if os.name != "nt":
        return
    esc = lambda s: html.escape(s).replace("'", "''")
    ps1 = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
           "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null;"
           f"$x = New-Object Windows.Data.Xml.Dom.XmlDocument; $x.LoadXml('<toast><visual><binding template=\"ToastGeneric\"><text>{esc(title)}</text><text>{esc(text)}</text></binding></visual></toast>');"
           "$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
           "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($x))")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", ps1],
                   creationflags=0x08000000, timeout=60)


# ---------- 본체 ----------
def run(dry=False, since=None, notify=True):
    c = conf()
    state = json.load(open(STATE, encoding="utf-8")) if os.path.isfile(STATE) else {"seen": []}
    seen = set(state.get("seen") or [])
    since = since or state.get("since") or c.get("since") or "2026-11-01"
    since_d = datetime.date.fromisoformat(since)
    d = ps.load()
    me = jt.me()["name"].lower()
    prods = [(p, (c.get("products") or {}).get(p["id"]) or {}) for p in d["products"]]
    prods = [(p, pc) for p, pc in prods if pc.get("match")]
    table = None
    report, cell_changes, new_keys = [], 0, []

    for doc in c.get("docs") or []:
        try:
            ents = [e for e in history(doc) if e["date"] >= since_d and e["key"] not in seen]
            doc_title = title_of(doc["sheet"])
        except Exception as ex:
            report.append({"err": f"{doc['name']} 읽기 실패: {ex}"})
            continue
        ents.sort(key=lambda e: (e["date"], e["ver"]))
        for e in ents:
            new_keys.append(e["key"])
        for p, pc in prods:
            hits = [(e, related(e, pc["match"])) for e in ents]
            hits = [(e, b) for e, b in hits if b]
            if not hits:
                continue
            item = {"product": p["name"], "doc": doc["name"], "file": doc_title, "entries": [], "jira": [], "cell": ""}
            for e, b in hits:
                item["entries"].append({"ver": e["ver"], "date": str(e["date"]), "author": e["author"], "text": "\n".join(b)})
            # 목표 상태 판단
            target, why, pop_target = "진행 중", "문서 업데이트됨", None
            comp = None
            if doc["id"] == "model_info" and pc.get("proj_code"):
                if table is None:
                    table = values(doc["sheet"], c.get("model_tab") or "2.0_모델 기본 정보 및 정책")
                comp = completeness(table, pc["proj_code"])
                rel = ((p.get("cells") or {}).get("release") or {}).get("date") or ""
                if comp and re.match(r"\d{4}-\d{2}", rel) and re.match(r"\d{2}-\d{2}", comp["release"]) and comp["release"][:5] != rel[2:7]:
                    comp["missing"].append(f"출시일 시트 {comp['release']} ≠ 제품별 진행상황 {rel[2:7]} (최신 일정 반영 필요)")
                if comp:
                    if comp["missing"]:
                        why = f"{comp['row']}행 기준 남은 칸 {len(comp['missing'])}개: " + ", ".join(comp["missing"][:6]) + (" 외" if len(comp["missing"]) > 6 else "")
                    else:
                        target, why = "해결", f"{comp['row']}행부터 {comp['n']}개 행의 제품명·모델명·자재코드·POP가 모두 채워짐"
                    pop_target = "해결" if not comp["pop_missing"] else None
            item["target"], item["why"] = target, why
            # Jira
            epic = (p.get("epic") or "").strip()
            iss_all = kids(epic) if epic else []
            groups = [(doc.get("jira_kw") or [], target, why)]
            if pop_target:
                groups.append((c.get("pop_kw") or ["POP"], pop_target, "제품 행 POP 칸이 모두 채워짐"))
            for kws, tgt, reason in groups:
                for i in pick(iss_all, p, d, kws):
                    cur = status_name(i)
                    act = {"key": i["key"], "summary": i["fields"]["summary"], "from": cur, "to": cur, "mine": assignee(i) == me, "done": []}
                    move = ORDER.get(tgt, 0) > ORDER.get(cur, 0)
                    if not act["mine"]:
                        act["note"] = f"담당 {(i['fields'].get('assignee') or {}).get('displayName', '없음')} — 변경 안 함" + (f" (제안: {cur} → {tgt})" if move else "")
                    else:
                        body = (f"[IoT업무봇 자동] {doc['name']} 문서이력 반영\n파일: {doc_title}\n"
                                + "\n".join(f"* {x['ver']} ({x['date']}, {x['author']})\n{{noformat}}{x['text']}{{noformat}}" for x in item["entries"])
                                + f"\n판단: {reason}" + (f"\n상태: {cur} → {tgt}" if move else ""))
                        if not dry:
                            jt.call("POST", f"/issue/{i['key']}/comment", {"body": body})
                            act["done"].append("댓글")
                            if move:
                                tr = [t for t in jt.call("GET", f"/issue/{i['key']}/transitions")["transitions"] if t["name"] == TRANS.get(tgt, tgt) or t["to"]["name"] == tgt]
                                if tr:
                                    jt.call("POST", f"/issue/{i['key']}/transitions", {"transition": {"id": tr[0]["id"]}})
                                    act["to"] = tgt
                                    act["done"].append("상태 변경")
                                else:
                                    act["note"] = f"'{tgt}' 전환을 찾지 못함"
                        else:
                            act["done"].append("댓글(시험)")
                            if move:
                                act["to"] = tgt
                                act["done"].append("상태 변경(시험)")
                    item["jira"].append(act)
            # 제품별 진행상황 칸
            stage = doc.get("stage")
            last = item["entries"][-1]
            main = next((a for a in item["jira"] if any(norm(k) in norm(a["summary"]) for k in doc.get("jira_kw") or [])), None)
            memo = (f"{doc['name']} v{last['ver']}({last['date'][5:].replace('-', '/')}) " + re.sub(r"\s+", " ", last["text"])[:180]
                    + f" · 판단: {why}" + (f" · Jira {main['key']} {main['to']}" if main else ""))
            cells = p.setdefault("cells", {})
            for st_id, tgt in [(stage, target)] + ([("pop", pop_target)] if pop_target else []):
                if not st_id:
                    continue
                cur = cells.get(st_id) or {}
                if cur.get("src") == "직접":
                    continue
                fresh = (cur.get("at") or "")[:10] >= last["date"]  # 문서이력보다 나중에 정리한 칸 → 메모는 그대로, 상태만 맞춤
                new = {"status": CELL_ST.get(tgt, "진행중"), "date": last["date"],
                       "memo": memo if st_id == stage else f"POP 칸 모두 채워짐 ({doc['name']} v{last['ver']})",
                       "link": f"https://docs.google.com/spreadsheets/d/{doc['sheet']}/edit", "src": "문서이력(자동)", "at": ps.now()}
                if fresh:
                    if cur.get("status") == new["status"]:
                        continue
                    new = dict(cur, status=new["status"], at=new["at"])
                if any(cur.get(k) != new[k] for k in ("status", "date", "memo")):
                    if not dry:
                        cells[st_id] = new
                    cell_changes += 1
                    item["cell"] += f"{st_id}→{new['status']} "
            report.append(item)

    if not dry:
        if cell_changes:
            ps.touch(d)
            ps.save(d)
        state["seen"] = sorted(seen | set(new_keys))
        state["since"] = since
        state["last_run"] = ps.now()
        json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    items = [r for r in report if "err" not in r]
    log(f"{'DRY ' if dry else ''}new_rows={len(new_keys)} items={len(items)} cells={cell_changes} errs={len(report) - len(items)}")
    if notify and not dry and report:
        subject, body, short = render(report)
        try:
            send_mail(subject, body)
        except Exception as ex:
            log(f"메일 실패: {ex}")
        try:
            toast("IoT업무봇 · 문서이력 반영", short)
        except Exception as ex:
            log(f"알림 실패: {ex}")
    return report


def render(report):
    items = [r for r in report if "err" not in r]
    moved = sum(1 for r in items for a in r["jira"] if a["from"] != a["to"])
    subject = f"[IoT업무봇] 문서이력 반영 {len(items)}건 · Jira 상태 변경 {moved}건 ({datetime.date.today():%m/%d})"
    css = "font-family:Pretendard,Malgun Gothic,sans-serif;font-size:13px;color:#1F2933"
    h = [f'<div style="{css}"><p>모델별 기본정보·IoT기능정의서 문서이력에서 내 담당 제품 관련 업데이트를 찾아 반영했습니다.</p>']
    for r in items:
        h.append(f'<h3 style="margin:18px 0 6px;color:#0B3B8C">{html.escape(r["product"])} · {html.escape(r["doc"])}</h3>')
        for e in r["entries"]:
            h.append(f'<div style="margin:4px 0;padding:6px 10px;background:#F4F6F8;border-radius:6px"><b>v{html.escape(e["ver"])}</b> ({e["date"]}, {html.escape(e["author"])})<br>'
                     + html.escape(e["text"]).replace("\n", "<br>") + "</div>")
        h.append(f'<div>판단: {html.escape(r["why"])}</div><ul>')
        for a in r["jira"]:
            st = f'{a["from"]} → <b>{a["to"]}</b>' if a["from"] != a["to"] else a["from"]
            h.append(f'<li><a href="https://jira.coway.com/browse/{a["key"]}">{a["key"]}</a> {html.escape(a["summary"])} — {st}'
                     + (f' · {", ".join(a["done"])}' if a["done"] else "") + (f' <span style="color:#B42318">({html.escape(a["note"])})</span>' if a.get("note") else "") + "</li>")
        if not r["jira"]:
            h.append("<li>연결된 Jira 문서 없음</li>")
        if r["cell"]:
            h.append(f"<li>제품별 진행상황 칸 갱신: {html.escape(r['cell'])}</li>")
        h.append("</ul>")
    for r in report:
        if "err" in r:
            h.append(f'<p style="color:#B42318">⚠ {html.escape(r["err"])}</p>')
    h.append('<p style="color:#616A75;font-size:12px">IoT업무봇 자동 실행 (doc_sync.py) · 되돌리려면 Jira에서 상태를 직접 바꿔 주세요.</p></div>')
    short = f"{len(items)}건 반영, Jira 상태 변경 {moved}건 — " + ", ".join(sorted({r['product'] for r in items}))[:120]
    return subject, "".join(h), short


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="Jira·진행상황을 바꾸지 않고 할 일만 출력")
    ap.add_argument("--since", help="이 날짜(YYYY-MM-DD) 이후 문서이력만 (처음 실행 기준)")
    ap.add_argument("--no-notify", action="store_true")
    a = ap.parse_args()
    try:
        rep = run(a.dry_run, a.since, not a.no_notify)
    except Exception as ex:
        log(f"실패: {ex!r}")
        raise
    for r in rep:
        if "err" in r:
            print("⚠", r["err"])
            continue
        print(f"## {r['product']} · {r['doc']} — 판단: {r['target']} ({r['why']})")
        for e in r["entries"]:
            print(f"   v{e['ver']} {e['date']} {e['author']}: " + re.sub(r"\s+", " ", e["text"])[:160])
        for x in r["jira"]:
            print(f"   Jira {x['key']} {x['summary']} : {x['from']} → {x['to']} {x['done']} {x.get('note', '')}")
        if r["cell"]:
            print("   진행상황:", r["cell"])
    if not rep:
        print("새 문서이력 없음")


if __name__ == "__main__":
    main()
