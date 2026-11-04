#!/usr/bin/env python3
"""내 할 일 — 아침 자동 실행기. OS 작업(Windows 'WorkBotMyTasks' / Mac launchd 'com.workbot.mytasks')이 매일 정해진 시각에 실행한다.
할 일을 찾아 다운로드 폴더에 워드 문서로 저장한다(설정에서 메일 켜면 워드 첨부해 발송). 기록: runs/my-tasks/morning.log
"""
import os, sys, time, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("BOT_HUB_RUNS", os.path.abspath(os.path.join(HERE, "..", "..", "runs")))
sys.path.insert(0, HERE)
import my_tasks as mt  # noqa: E402

LOG = os.path.join(mt.DIR, "morning.log")


def log(msg):
    os.makedirs(mt.DIR, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


if __name__ == "__main__":
    log("시작")
    try:
        mt.run(log=lambda m: log("  " + m), mail=bool(mt.cfg().get("mail_on")), doc=True)
        log("끝")
    except Exception as e:
        log(f"실패: {e}\n{traceback.format_exc()}")
