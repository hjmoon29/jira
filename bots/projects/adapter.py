#!/usr/bin/env python3
# IoT업무봇 ↔ projects.py 연결. 입력: argv[1]=기능, stdin=화면 입력(JSON) / 출력: JSON
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import projects as pj  # noqa: E402

LOG = []


def out(**kw):
    print(json.dumps(kw, ensure_ascii=False))
    sys.exit(0)


def view(sel=None, **kw):
    kw.setdefault("quiet", True)
    out(ok=True, view="projects", data=pj.board(sel), **kw)


def main(action, q):
    pid = q.get("pid") or ""
    try:
        if action == "board":
            view(pid)
        if action == "refresh":
            lines = pj.refresh(pid, LOG.append)
            view(pid, quiet=False, text="\n".join(lines + ([""] + LOG if LOG else [])),
                 summary=lines[0] if len(lines) == 1 else f"프로젝트 {len(lines)}개 새로 확인")
        if action == "save_project":
            view(pj.save_project(q))
        if action == "delete_project":
            pj.delete_project(pid)
            view()
        if action == "move_project":
            pj.move_project(pid, int(q.get("step") or 1))
            view(pid)
        if action == "schedule":
            msg = pj.set_schedule(bool(q.get("on")), (q.get("at") or "").strip())
            view(pid, quiet=False, text=msg, summary=msg)
        if action == "reorder":
            pj.reorder(q.get("ids") or [])
            view(pid)
        if action == "mark":
            pj.mark(pid, q.get("tid"), q.get("status") or "done")
            view(pid)
        if action == "add_todo":
            pj.add_todo(pid, q.get("title"), q.get("due"))
            view(pid)
        if action == "link_file":
            pj.link_file(pid, q.get("path"), bool(q.get("on", True)))
            view(pid)
        if action == "open_file":
            pj.open_file(q.get("path"))
            out(ok=True, quiet=True, text="파일을 열었습니다")
        if action == "preview":
            out(ok=True, quiet=True, preview=pj.preview(q.get("path")))
        if action == "attachment":
            path = pj.save_attachment(pid, q.get("mail_id"), q.get("att"), q.get("name"))
            out(ok=True, quiet=True, path=path, preview=pj.preview(path), data=pj.board(pid), view="projects",
                text=f"첨부를 저장했습니다: {path}")
        if action == "reply_draft":
            out(ok=True, quiet=True, draft=pj.reply_draft(pid, q.get("mail_id")))
        if action == "jira_transitions":
            out(ok=True, quiet=True, transitions=pj.jira_transitions(q.get("key")))
        if action == "jira_do":
            msg = pj.jira_do(q.get("key"), q.get("comment") or "", q.get("transition") or "")
            out(ok=True, quiet=False, text=msg, summary=msg)
        out(ok=False, text="알 수 없는 기능")
    except pj.BotError as e:
        out(ok=False, text=str(e))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "", json.loads(sys.stdin.read() or "{}"))
