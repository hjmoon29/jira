#!/usr/bin/env python3
"""회의록 백그라운드 작업: 음성 인식 → 회의록 초안(v1). minutes.spawn() 이 창 없이 띄운다.
음성 인식은 CPU를 많이 쓰므로 한 번에 하나만 (다른 작업이 돌고 있으면 '대기 중'으로 기다림).
이미 녹취록이 있으면(다시 시작) 음성 인식은 건너뛴다.
"""
import os, sys, time, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("BOT_HUB_RUNS", os.path.abspath(os.path.join(HERE, "..", "..", "runs")))
sys.path.insert(0, HERE)
import minutes as mn  # noqa: E402


def lock():
    """프로세스가 끝나면 OS가 풀어 주는 파일 잠금"""
    os.makedirs(mn.DIR, exist_ok=True)
    f = open(mn.LOCK, "a+")
    while True:
        try:
            if sys.platform == "win32":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f
        except OSError:
            time.sleep(10)


def main(jid):
    job = mn.load(jid)

    def setp(p, msg):
        job.update(progress=round(p, 1), message=msg, beat=time.time())
        mn.save(job)
    try:
        tp = os.path.join(mn.DIR, jid, "transcript.txt")
        if not os.path.isfile(tp):
            job.update(status="queued", message="다른 회의록의 음성 인식이 끝나기를 기다리는 중…", beat=time.time())
            mn.save(job)
            lk = lock()
            job.update(status="transcribing", stt_started=time.strftime("%Y-%m-%d %H:%M"))
            t0 = time.time()
            text, dur = mn.transcribe(job, setp)
            lk.close()
            if not text.strip():
                raise mn.BotError("음성에서 말소리를 찾지 못했습니다 (파일이 비었거나 소리가 너무 작음)")
            open(tp, "w", encoding="utf-8").write(text)
            job.update(stt_sec=round(time.time() - t0), duration=dur, chars=len(text))
        job.update(status="drafting", progress=100, message="Claude가 회의록을 정리하는 중… (보통 1~5분)", beat=time.time())
        mn.save(job)
        mn.draft(job)
        last = job["versions"][-1]
        job.update(status="ready", message=last.get("error") or "", beat=time.time())
        mn.save(job)
    except Exception as e:  # noqa: BLE001
        job.update(status="error", message=str(e)[:500], beat=time.time())
        mn.save(job)
        traceback.print_exc()


if __name__ == "__main__":
    main(sys.argv[1])
