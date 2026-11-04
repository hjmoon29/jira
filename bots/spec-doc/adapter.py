#!/usr/bin/env python3
# IoT업무봇 ↔ spec_doc.py 연결. 입력: argv[1]=기능, stdin=화면 입력(JSON) / 출력: JSON
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spec_doc as sd  # noqa: E402


def out(**kw):
    print(json.dumps(kw, ensure_ascii=False))
    sys.exit(0)


def view(name, meta, body, log=()):
    try:
        prev = sd.preview_html(body)
    except Exception as e:  # XML 형식 오류여도 원문은 보여 준다
        prev = "<pre>" + sd.html.escape(body) + "</pre>"
        meta = dict(meta, error=meta.get("error") or f"XML 형식 오류: {e}")
    return dict(ok=True, view="spec_draft", data={"name": name, "meta": meta, "html": prev, "log": list(log)},
                summary=f"{meta['title']} 초안" + (" (형식 오류)" if meta.get("error") else ""))


def draft(q):
    q = dict(q, children=q.get("children", True) not in (False, "false"))
    log = []
    try:
        name, meta, body = sd.draft(q, log=log.append)
    except sd.BotError as e:
        out(ok=False, text="\n".join(log + [str(e)]))
    out(**view(name, meta, body, log))


def drafts(q):
    out(ok=True, view="spec_list", data=sd.list_drafts(), summary="초안 목록", quiet=True)


def open_draft(q):
    try:
        meta, body = sd.load_draft(q.get("name"))
    except sd.BotError as e:
        out(ok=False, text=str(e))
    out(**dict(view(q["name"], meta, body), quiet=True))


def publish(q):
    try:
        meta = sd.publish(q.get("name"))
    except sd.BotError as e:
        out(ok=False, text=str(e))
    p = meta["published"]
    out(ok=True, text=f"{p['how']}: {meta['title']}\n{p['url']}", summary=f"{meta['title']} 위키 게시 ({p['how']})",
        view="spec_draft", data={"name": q["name"], "meta": meta, "html": sd.preview_html(sd.load_draft(q["name"])[1])})


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    q = json.loads(sys.stdin.read() or "{}")
    fn = {"draft": draft, "drafts": drafts, "open": open_draft, "publish": publish}.get(action)
    fn(q) if fn else out(ok=False, text="알 수 없는 기능")
