#!/usr/bin/env python3
"""팀 휴가 캘린더 — 자동 실행기. Windows 작업 스케줄러 'WorkBotTeamLeave' 가 평일 설정 시각에 실행한다."""
import datetime as dt
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import team_leave as tl  # noqa: E402


def log(msg):
    os.makedirs(tl.DIR, exist_ok=True)
    with open(tl.LOG, "a", encoding="utf-8") as f:
        f.write(f"{dt.datetime.now(tl.KST):%Y-%m-%d %H:%M:%S} {msg}\n")


if __name__ == "__main__":
    try:
        r = tl.check(log=log)
        log(f"완료: 새로 등록 {len(r['added'])}건, 실패 {len(r['errors'])}건")
    except tl.BotError as e:
        log(f"오류: {e}")
    except Exception as e:  # noqa: BLE001
        log(f"예상 못한 오류: {e!r}")
