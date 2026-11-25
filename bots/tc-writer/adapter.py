#!/usr/bin/env python3
# IoT업무봇 ↔ tc_writer.py 연결. 입력: argv[1]=기능, stdin=화면 입력(JSON) / 출력: JSON
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tc_writer as tw  # noqa: E402


def out(**kw):
    print(json.dumps(kw, ensure_ascii=False))
    sys.exit(0)


def call(fn, *a):
    log = []
    try:
        return fn(*a, log=log.append), log
    except (tw.BotError, tw.sd.BotError, tw.qu.BotError) as e:
        out(ok=False, text="\n".join(log + [str(e)]))


def view(meta, data, log=(), quiet=False):
    return dict(ok=True, view="tc_draft", quiet=quiet, data={"meta": meta, "data": data, "log": list(log)},
                summary=f"{meta['product']} TC {len(data.get('tc') or [])}행" + (" · 문서 생성됨" if meta.get("doc") else " 초안"))


def draft(q):
    (meta, data), log = call(tw.draft, q)
    out(**view(meta, data, log))


def drafts(q):
    out(ok=True, view="tc_list", data=tw.list_drafts(), summary="초안 목록", quiet=True)


def open_draft(q):
    try:
        meta, data = tw.load(q.get("name"))
    except tw.BotError as e:
        out(ok=False, text=str(e))
    out(**view(meta, data, quiet=True))


def create(q):
    meta, log = call(tw.create, q.get("name"))
    _, data = tw.load(meta["name"])
    r = view(meta, data, log)
    r.update(headline=f"Google Drive에 TC 문서를 만들었습니다 — {meta['doc']['name']}", summary=f"{meta['product']} TC 문서 생성")
    out(**r)


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    q = json.loads(sys.stdin.read() or "{}")
    fn = {"draft": draft, "drafts": drafts, "open": open_draft, "create": create}.get(action)
    fn(q) if fn else out(ok=False, text="알 수 없는 기능")
