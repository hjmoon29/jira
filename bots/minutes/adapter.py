#!/usr/bin/env python3
# IoT업무봇 ↔ minutes.py 연결. 입력: argv[1]=기능, stdin=화면 입력(JSON) / 출력: JSON
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import minutes as mn  # noqa: E402


def out(**kw):
    print(json.dumps(kw, ensure_ascii=False))
    sys.exit(0)


def fail(e):
    out(ok=False, text=str(e))


def listing(quiet=True, **kw):
    js = mn.jobs()
    now = time.time()
    for j in js:  # 백그라운드 작업이 멈춘 것 같으면 표시 (PC 재시작 등)
        if j["status"] in ("queued", "transcribing", "drafting") and now - (j.get("beat") or now) > (3600 if j["status"] == "drafting" else 1800):
            j["stale"] = True
    out(ok=True, view="minutes_list", data={"jobs": js}, quiet=quiet, **kw)


def start(q):
    try:
        j = mn.start(q)
    except mn.BotError as e:
        fail(e)
    listing(quiet=False, summary=f"회의록 작업 시작: {j['file']}",
            headline="음성 인식을 시작했습니다. 이 화면을 닫아도 계속 진행되며, 끝나면 목록에서 '열기'로 확인합니다.")


def jobs(q):
    listing()


def open_(q):
    try:
        out(ok=True, view="minutes_doc", data=mn.view(q.get("id"), q.get("v")), quiet=True)
    except mn.BotError as e:
        fail(e)


def revise(q):
    try:
        j = mn.revise(q.get("id"), q.get("feedback"))
    except mn.BotError as e:
        fail(e)
    out(ok=True, view="minutes_doc", data=mn.view(j["id"]), summary=f"회의록 v{len(j['versions'])} 수정")


def publish(q):
    try:
        j = mn.publish(q.get("id"), q.get("parent"), q.get("title"))
    except mn.BotError as e:
        fail(e)
    out(ok=True, view="minutes_doc", data=mn.view(j["id"]), summary=f"회의록 위키 게시: {j['page_title']}")


def retry(q):
    try:
        j = mn.load(q.get("id"))
        j.update(status="queued", message="다시 시작", beat=time.time())
        mn.save(j)
        mn.spawn(j["id"])
    except mn.BotError as e:
        fail(e)
    listing(quiet=False, summary="회의록 작업 다시 시작")


def delete(q):
    try:
        mn.delete(q.get("id"))
    except mn.BotError as e:
        fail(e)
    listing(quiet=False, summary="회의록 작업 삭제")


def init(q):
    c = mn.cfg()
    out(ok=True, quiet=True, data={"values": {"parent": c.get("parent", "")}})


def settings(q):
    c = mn.cfg()
    c.update(parent=(q.get("parent") or "").strip(), glossary=(q.get("glossary") or "").strip())
    mn.save_cfg(c)
    out(ok=True, text="저장했습니다.", summary="회의록 설정 저장")


def settings_init(q):
    c = mn.cfg()
    out(ok=True, quiet=True, data={"values": {"parent": c.get("parent", ""), "glossary": c.get("glossary", "")}})


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    q = json.loads(sys.stdin.read() or "{}")
    fn = {"start": start, "jobs": jobs, "open": open_, "revise": revise, "publish": publish, "retry": retry, "delete": delete,
          "init": init, "settings": settings, "settings_init": settings_init}.get(action)
    fn(q) if fn else out(ok=False, text="알 수 없는 기능")
