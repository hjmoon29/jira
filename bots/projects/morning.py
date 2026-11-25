#!/usr/bin/env python3
"""프로젝트 대응 — 아침 자동 실행기. 작업 스케줄러 'WorkBotProjects'(Mac: launchd com.workbot.projects)가 매일 정해진 시각에 실행.
모든 프로젝트의 새 메일·Jira 변경을 읽어 요약·할 일을 갱신하고, 메일 첨부를 다운로드 폴더(IoT업무봇\\<프로젝트>)에 받는다.
기록: runs/projects/morning.log
"""
import os, sys, time, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("BOT_HUB_RUNS", os.path.abspath(os.path.join(HERE, "..", "..", "runs")))
sys.path.insert(0, HERE)
import projects as pj  # noqa: E402

LOG = os.path.join(pj.DIR, "morning.log")


def log(msg):
    os.makedirs(pj.DIR, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


if __name__ == "__main__":
    log("시작")
    try:
        for line in pj.refresh("", lambda m: log("  " + m)):
            log("  → " + line)
        log("끝")
    except Exception as e:  # noqa: BLE001
        log(f"실패: {e}\n{traceback.format_exc()}")
