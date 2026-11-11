#!/usr/bin/env python3
# IoT업무봇 ↔ qr_url.py 연결. 입력: argv[1]=기능, stdin=화면 입력(JSON) / 출력: JSON
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qr_url as qu  # noqa: E402


def out(**kw):
    print(json.dumps(kw, ensure_ascii=False))
    sys.exit(0)


def run(fn, q):
    log = []
    try:
        return fn(q, log=log.append), log
    except qu.BotError as e:
        out(ok=False, text="\n".join(log + [str(e)]))


def norm(q):
    q = dict(q)
    for k in ("make_qr", "write_history"):
        if k in q:
            q[k] = q[k] in (True, "true", "on", "1", 1, "예")
    return q


def init(q):
    out(ok=True, quiet=True, data={"values": {"author": qu.state().get("author") or "IoT기획팀 문효준"}})


def preview(q):
    p, log = run(qu.preview, norm(q))
    out(ok=True, headline="미리보기 — 아직 시트에 쓰지 않았습니다", text=p["text"], summary=f"미리보기 {len(p['items'])}개", quiet=True)


def apply(q):
    (doc, items, h), log = run(qu.apply, norm(q))
    lines = [f"문서: {doc['name']}", ""]
    for it in items:
        lines.append(f"{it['row']}행 {it['proj']} {it['model'] or '(모델명 없음)'}")
        if it.get("url"):
            lines.append(f"   URL {it['url']}")
        if it.get("qr"):
            lines.append(f"   QR  {it['qr']}")
    lines += ["", *log, "", f"https://docs.google.com/spreadsheets/d/{doc['id']}/edit"]
    out(ok=True, headline=f"URL정의서에 {len(items)}개 추가 완료", text="\n".join(lines),
        summary=f"{', '.join(it['proj'] for it in items)} 추가")


def requr(q):
    (doc, r, url, link), log = run(qu.requr, q)
    out(ok=True, headline=f"{q.get('model')} QR 다시 만듦", text="\n".join([f"{r}행 · {url}", f"새 QR {link}", *log]),
        summary=f"{q.get('model')} QR 재생성")


def find(q):
    (doc, rows), log = run(qu.find, q)
    out(ok=True, view="table", quiet=True, summary=f"목록 {len(rows)}건",
        data={"tables": [{"title": doc["name"], "columns": ["행", "순서", "제품군", "프로젝트명", "모델명", "URL", "QR", "매뉴얼", "제품 내 사용", "기타사항"],
                          "rows": rows, "total": len(rows), "shown": len(rows)}]})


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    q = json.loads(sys.stdin.read() or "{}")
    fn = {"init": init, "preview": preview, "apply": apply, "requr": requr, "find": find}.get(action)
    fn(q) if fn else out(ok=False, text="알 수 없는 기능")
