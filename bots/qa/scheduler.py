#!/usr/bin/env python3
"""QA 대응 예약 실행기 — OS 작업(Windows 작업 스케줄러 'WorkBotQA' / Mac launchd 'com.workbot.qa')이 10분마다 실행한다.
때가 된 예약(next_run <= 지금)을 실행하고 다음 시각을 계산한다. IoT업무봇 화면이 꺼져 있어도 동작한다.
기록: runs/qa-scheduler.log
"""
import datetime, os, sys, time, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("BOT_HUB_RUNS", os.path.abspath(os.path.join(HERE, "..", "..", "runs")))
sys.path.insert(0, HERE)
import qa  # noqa: E402

LOG = os.path.join(qa.RUNS, "qa-scheduler.log")
LOCK = os.path.join(qa.RUNS, "qa-scheduler.lock")


def log(msg):
    os.makedirs(qa.RUNS, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


def main():
    # 이전 실행이 아직 도는 중이면(답변 작성은 10분 넘게 걸릴 수 있음) 건너뛴다. 3시간 넘은 잠금은 비정상 종료로 보고 무시
    if os.path.isfile(LOCK) and time.time() - os.path.getmtime(LOCK) < 3 * 3600:
        return
    open(LOCK, "w").write(str(os.getpid()))
    try:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        for job in qa.load_schedules():
            if not job.get("next_run") or job["next_run"] > now:
                continue
            log(f"실행 {job['id']} {job['label']}")
            try:
                result = qa.run_job(job, log=lambda m: log(f"  {m}"))
            except Exception as e:  # 한 작업이 실패해도 다음 작업은 계속
                result = f"실패: {e}"
                log(traceback.format_exc())
            # 실행 중에 화면에서 예약을 지웠거나 고쳤을 수 있으니 다시 읽어 이 작업만 갱신
            cur = qa.load_schedules()
            for j in cur:
                if j["id"] == job["id"]:
                    j.update(last_run=time.strftime("%Y-%m-%d %H:%M"), last_result=result, state=job.get("state", {}),
                             next_run=qa.next_run(j["when"]))
            qa.save_schedules(cur)
            log(f"  → {result}")
    finally:
        os.remove(LOCK)


if __name__ == "__main__":
    main()
