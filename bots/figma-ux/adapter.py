#!/usr/bin/env python3
# IoT업무봇 ↔ figma_ux.py 연결. 입력: argv[1]=기능, stdin=화면 입력(JSON) / 출력: JSON
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import figma_ux as fx  # noqa: E402


def out(**kw):
    print(json.dumps(kw, ensure_ascii=False))
    sys.exit(0)


def view(doc, log=(), **kw):
    return dict(ok=True, view="ux_plan", data={"doc": doc, "page": fx.PAGE}, text="\n".join(log), **kw)


def guarded(fn, q):
    log = []
    try:
        return fn(q, log=log.append), log
    except fx.BotError as e:
        out(ok=False, text="\n".join(log + [str(e)]))


def plan(q):
    doc, log = guarded(fx.plan, q)
    out(**view(doc, log, summary=f"{doc['title']} 설계안 (화면 {len(doc['versions'][-1]['plan']['screens'])}개)"))


def revise(q):
    doc, log = guarded(fx.revise, q)
    out(**view(doc, log, summary=f"{doc['title']} 설계안 v{len(doc['versions'])}"))


def draw(q):
    (doc, res), log = guarded(fx.draw, q)
    if res.get("error"):
        out(**dict(view(doc, log, summary=f"{doc['title']} Figma 그리기 실패"), ok=False))
    out(**view(doc, log, summary=f"{doc['title']} Figma에 그림 (v{res['v']})"))


def plans(q):
    out(ok=True, view="ux_list", data=fx.list_plans(), summary="설계안 목록", quiet=True)


def open_plan(q):
    try:
        out(**view(fx.load(q.get("name")), quiet=True))
    except fx.BotError as e:
        out(ok=False, text=str(e))


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    q = json.loads(sys.stdin.read() or "{}")
    fn = {"plan": plan, "revise": revise, "draw": draw, "plans": plans, "open": open_plan}.get(action)
    fn(q) if fn else out(ok=False, text="알 수 없는 기능")
