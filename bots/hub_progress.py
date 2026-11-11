"""IoT업무봇 진행률 보고 — 봇이 '몇 % · 지금 하는 일'을 허브에 알린다.

허브(hub.py)가 실행할 때 환경변수 BOT_HUB_PROGRESS 에 파일 경로를 넣어 주면, 봇은 그 파일에 진행 상태를 쓰고
화면은 /api/progress 로 1초마다 읽어 게이지로 보여 준다. 허브 밖(예약 실행 등)에서 돌 때는 아무것도 하지 않는다.

사용법:
    from hub_progress import progress, stage
    progress(10, "메일 읽는 중")            # 0~100
    with stage(10, 40, "Drive 읽는 중") as sub:   # 10~40% 구간을 하위 작업으로 나눔
        for i, f in enumerate(files):
            sub(i, len(files), f["name"])
"""
import json, math, os, threading, time
from contextlib import contextmanager

_PATH = os.environ.get("BOT_HUB_PROGRESS")
_last = [0.0]


def progress(pct, msg=""):
    if not _PATH:
        return
    pct = max(_last[0], min(100.0, float(pct)))  # 게이지가 뒤로 가지 않게
    _last[0] = pct
    try:
        tmp = _PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"pct": round(pct, 1), "msg": msg, "t": time.time()}, f, ensure_ascii=False)
        os.replace(tmp, _PATH)
    except OSError:  # 화면이 마침 읽는 중이면 이번 한 번은 건너뜀
        pass


@contextmanager
def stage(a, b, msg):
    """a~b% 구간. sub(i, n, 세부) 로 구간 안에서 나눠 보고한다."""
    progress(a, msg)

    def sub(i, n, detail=""):
        progress(a + (b - a) * (i / max(n, 1)), f"{msg} {detail}".strip() if detail else msg)
    yield sub
    progress(b, msg)


def waiting(a, b, msg, fn, est_sec=120):
    """끝나는 시점을 알 수 없는 긴 작업(Claude 판단 등): 지난번 걸린 시간(est_sec)을 기준으로 a→b 구간을
    천천히 채우며(끝나기 전엔 b의 95%까지만) fn() 을 실행하고 결과를 돌려준다."""
    stop = threading.Event()
    t0 = time.time()

    def tick():
        while not stop.wait(1):
            sec = time.time() - t0
            frac = 1 - math.exp(-sec / max(est_sec, 10) * 1.6)  # 예상 시간쯤 약 80%
            progress(a + (b - a) * 0.95 * frac, f"{msg} {int(sec)}초 (보통 약 " + (f"{round(est_sec / 60)}분" if est_sec >= 90 else f"{int(est_sec)}초") + ")")
    th = threading.Thread(target=tick, daemon=True)
    th.start()
    try:
        return fn()
    finally:
        stop.set()
        progress(b, msg)
