#!/usr/bin/env python3
# IoT업무봇 ↔ team_leave.py 연결. 입력: argv[1]=기능, stdin=화면 입력(JSON) / 출력: JSON
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import team_leave as tl  # noqa: E402


def out(**kw):
    print(json.dumps(kw, ensure_ascii=False))
    sys.exit(0)


def board(q):
    out(ok=True, view="table", data=tl.board(), quiet=True)


def _run(register):
    log = []
    try:
        r = tl.check(log=log.append, register=register)
    except tl.BotError as e:
        out(ok=False, headline="⚠️ " + str(e).split("\n")[0], text="\n".join(log + [str(e)]), summary=str(e)[:120])
    except Exception as e:  # noqa: BLE001 — 예상 못한 오류도 무엇이 문제인지 보여 준다
        import traceback
        msg = f"예상 못한 오류: {type(e).__name__}: {e}"
        out(ok=False, headline="⚠️ " + msg[:200], text="\n".join(log + [msg, "", traceback.format_exc()[-1500:]]), summary=msg[:120])
    tgt = [x for x in r["people"] if x["status"]]
    head = (f"새로 등록 {len(r['added'])}건" if register else f"휴가 상태 {len(tgt)}명 (미리보기, 등록 안 함)")
    if r["errors"]:
        head += f" · 실패 {len(r['errors'])}건"
    out(ok=not r["errors"], view="table", data=tl.board(r), text="\n".join(log + r["errors"]),
        headline=("✅ " if not r["errors"] else "⚠️ ") + head, summary=head)


def run(q):
    _run(True)


def preview(q):
    _run(False)


def login(q):
    log = []
    try:
        ok = tl.login(log.append)
    except tl.BotError as e:
        out(ok=False, text="\n".join(log + [str(e)]))
    out(ok=ok, text="로그인 완료. 이제 '지금 확인'과 자동 확인이 동작합니다." if ok else "10분 안에 두 곳 모두 로그인되지 않아 창을 닫았습니다. 다시 시도해 주세요.",
        summary="로그인 완료" if ok else "로그인 시간 초과")


def stop(q):
    try:
        done = tl.stop()
    except tl.BotError as e:
        out(ok=False, text=str(e), summary="중지 실패")
    if not done:
        out(ok=True, headline="ℹ️ 실행 중인 기능이 없습니다", text="지금 확인·미리보기·로그인·자동 확인 중 실행 중인 것이 없습니다.", summary="실행 중인 기능 없음")
    out(ok=True, headline="⏹ 실행을 중지했습니다", text="\n".join(done) + "\n\n이미 등록된 일정은 그대로 남아 있습니다. 필요하면 '지금 확인'으로 다시 실행하세요.",
        summary="중지: " + ", ".join(done)[:110])


def settings_init(q):
    c = tl.config()
    s = tl.schedule_status()
    out(ok=True, values={"calendar": c["calendar"], "orgunit_url": c["orgunit_url"], "statuses": ", ".join(c["statuses"]),
                         "title_format": c.get("title_format", "{name} {status}"), "auto": s["on"], "times": c.get("times", "")})


def settings(q):
    c = tl.config()
    for k in ("calendar", "orgunit_url", "title_format", "times"):
        if str(q.get(k, "")).strip():
            c[k] = str(q[k]).strip()
    if str(q.get("statuses", "")).strip():
        c["statuses"] = [x.strip() for x in str(q["statuses"]).split(",") if x.strip()]
    tl.save_config(c)
    try:
        msg = tl.set_schedule(q.get("auto") in (True, "true"), c.get("times", ""))
    except tl.BotError as e:
        out(ok=False, text=f"설정은 저장했지만 자동 실행 등록 실패 — {e}")
    s = tl.schedule_status()
    out(ok=True, text="설정 저장\n" + msg + (f"\n다음 실행: {s['next']}" if s.get("next") else ""), summary=msg[:120])


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    q = json.loads(sys.stdin.read() or "{}")
    {"board": board, "run": run, "preview": preview, "login": login, "settings_init": settings_init,
     "settings": settings, "stop": stop}.get(action, lambda q: out(ok=False, text="알 수 없는 기능"))(q)
