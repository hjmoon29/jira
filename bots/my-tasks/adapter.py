#!/usr/bin/env python3
# IoT업무봇 ↔ my_tasks.py 연결. 입력: argv[1]=기능, stdin=화면 입력(JSON) / 출력: JSON
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import my_tasks as mt  # noqa: E402


def out(**kw):
    print(json.dumps(kw, ensure_ascii=False))
    sys.exit(0)


def find(q):
    log = []
    try:
        v = mt.run(log=log.append, mail=q.get("mail") in (True, "true"), doc=q.get("doc", True) in (True, "true"))
    except mt.BotError as e:
        out(ok=False, text="\n".join(log + [str(e)]))
    lr = v.get("last_run") or {}
    out(ok=True, view="my_tasks", data=v, text="\n".join(log),
        summary=f"할 일 {len(v['open'])}건 (새로 {lr.get('new', 0)} · 갱신 {lr.get('updated', 0)} · 자동 완료 {lr.get('done', 0)})"
        + (f" · 워드 저장 {os.path.basename(v['docx'])}" if v.get("docx") else ""))


def board(q):
    out(ok=True, view="my_tasks", data=mt.board(), quiet=True)


def mark(q):
    try:
        mt.mark(q.get("id"), q.get("status"), (q.get("due") or "").strip(), (q.get("priority") or "").strip())
    except mt.BotError as e:
        out(ok=False, text=str(e))
    out(ok=True, view="my_tasks", data=mt.board(), quiet=True)


def settings(q):
    c = mt.cfg()
    c.update(drive_folder=(q.get("drive_folder") or "").strip(), mail_time=(q.get("mail_time") or "08:10").strip(),
             mail_to=(q.get("mail_to") or "").strip(), mail_on=q.get("mail_on") in (True, "true"))
    try:
        msg = mt.set_schedule(q.get("auto") in (True, "true"), c["mail_time"])
    except mt.BotError as e:
        out(ok=False, text=str(e))
    mt.save_cfg(c)
    out(ok=True, text=f"저장했습니다.\n{msg}", summary="내 할 일 설정 저장")


def settings_init(q):
    c = mt.cfg()
    out(ok=True, quiet=True, data={"values": {"drive_folder": c.get("drive_folder", ""), "mail_time": c.get("mail_time", "08:10"),
                                              "mail_to": c.get("mail_to", ""), "mail_on": bool(c.get("mail_on")),
                                              "auto": mt.schedule_status().get("on", False)}})


def save_now(q):
    try:
        path = mt.save_docx(mt.board())
    except Exception as e:
        out(ok=False, text=f"워드 저장 실패: {e}")
    if q.get("open") not in (False, "false"):
        try:
            os.startfile(path) if hasattr(os, "startfile") else __import__("subprocess").run(["open", path])
        except Exception:
            pass
    out(ok=True, text=f"워드 문서로 저장했습니다.\n{path}", summary="내 할 일 워드 저장")


def send_now(q):
    try:
        v = mt.board()
        to = mt.send_mail(v, mt.save_docx(v))
    except Exception as e:
        out(ok=False, text=f"메일 발송 실패: {e}")
    out(ok=True, text=f"{to} 로 지금 목록을 보냈습니다.", summary="내 할 일 메일 발송")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    q = json.loads(sys.stdin.read() or "{}")
    fn = {"find": find, "board": board, "mark": mark, "settings": settings, "settings_init": settings_init, "send_now": send_now, "save_now": save_now}.get(action)
    fn(q) if fn else out(ok=False, text="알 수 없는 기능")
