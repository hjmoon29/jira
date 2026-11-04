#!/usr/bin/env python3
# IoT업무봇 ↔ qa.py 연결. 입력: argv[1]=기능, stdin=화면 입력(JSON) / 출력: JSON
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qa  # noqa: E402


def out(**kw):
    print(json.dumps(kw, ensure_ascii=False))
    sys.exit(0)


def guard(fn):
    def run(q):
        try:
            fn(q)
        except (qa.BotError, qa.jt.BotError) as e:
            out(ok=False, text=str(e))
    return run


@guard
def jira_options(q):
    out(ok=True, data=qa.jira_options(q.get("project")), quiet=True)


@guard
def defaults(q):
    me = qa.re.sub(r"\[.*$", "", qa.jira("GET", "/myself")["displayName"]).strip()  # '문효준[IoT기획팀/팀원]' → '문효준'
    out(ok=True, data={"values": {"to": qa.mail_to_default(), "owner": me}}, quiet=True)


@guard
def jira_find(q):
    jql, rows = qa.jira_find(q)
    out(ok=True, view="qa_list", data={"kind": "jira", "query": q, "jql": jql, "rows": rows, "to": q.get("to") or qa.mail_to_default()},
        summary=f"이슈 {len(rows)}건 찾음")


@guard
def inquiry_find(q):
    r = qa.inquiry_find(q)
    rows = [{"key": x["id"], "summary": x["question"], "status": x["date"], "versions": x["doc"], "assignee": x["writer"], "labels": "", "updated": f"{x['row']}행"} for x in r["rows"]]
    out(ok=True, view="qa_list", data={"kind": "inquiry", "query": q, "rows": rows, "to": q.get("to") or qa.mail_to_default(),
                                        "note": f"{r['file']} › {r['sheet']} (머리글 {r['header_row']}행, 열: {', '.join(f'{k}={v}' for k, v in r['columns'].items())})"},
        summary=f"문의 {len(rows)}건 찾음")


@guard
def answer(q):
    log = []
    kind, params, keys, to = q["kind"], q["query"], q.get("keys") or [], q.get("to")
    if not keys:
        out(ok=False, text="답변할 건을 하나 이상 고르세요")
    res = (qa.run_jira if kind == "jira" else qa.run_inquiry)(params, keys, log=log.append)
    if not res:
        out(ok=False, text="고른 건을 다시 찾지 못했습니다")
    sent = ""
    if q.get("mail", True):
        mto, _ = qa.mail_result(res, to, kind)
        sent = f"\n메일 발송 → {mto}"
    out(ok=True, text="\n".join(log) + f"\n\n{res['title']}\n엑셀: {res['file']}{sent}", summary=res["title"] + (" 메일 발송" if sent else ""))


@guard
def schedule(q):
    job, os_task = qa.add_schedule(q["kind"], q["query"], q["when"], q.get("to"), q.get("label") or ("Jira 이슈 답변" if q["kind"] == "jira" else "문의사항 답변"))
    out(ok=True, text=f"예약했습니다: {job['label']} · {qa.when_text(job['when'])} → {job['to']}\n다음 실행: {job['next_run']}\n자동 실행: {os_task}",
        summary=f"예약 {qa.when_text(job['when'])}")


@guard
def schedules(q):
    s = qa.load_schedules()
    out(ok=True, view="qa_schedules", data=[dict(j, when_text=qa.when_text(j["when"]), state=None) for j in s], quiet=True)


@guard
def unschedule(q):
    s = [j for j in qa.load_schedules() if j["id"] != q.get("id")]
    qa.save_schedules(s)
    out(ok=True, view="qa_schedules", data=[dict(j, when_text=qa.when_text(j["when"]), state=None) for j in s], summary="예약 삭제")


@guard
def run_now(q):
    s = qa.load_schedules()
    job = next((j for j in s if j["id"] == q.get("id")), None)
    if not job:
        out(ok=False, text="예약을 찾을 수 없습니다")
    log = []
    result = qa.run_job(job, log=log.append)
    for j in s:
        if j["id"] == job["id"]:
            j.update(last_run=qa.time.strftime("%Y-%m-%d %H:%M"), last_result=result, state=job["state"])
    qa.save_schedules(s)
    out(ok=True, text="\n".join(log + [result]), summary=f"{job['label']} 지금 실행")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    q = json.loads(sys.stdin.read() or "{}")
    fn = {"jira_options": jira_options, "defaults": defaults, "jira_find": jira_find, "inquiry_find": inquiry_find, "answer": answer,
          "schedule": schedule, "schedules": schedules, "unschedule": unschedule, "run_now": run_now}.get(action)
    fn(q) if fn else out(ok=False, text="알 수 없는 기능")
