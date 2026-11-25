"""IoT업무봇 — PC를 켜고 로그인하면 전부 새로 받아오기 (작업 스케줄러 'IoTWorkBot_Startup', 로그인 시)

순서 (각 단계는 실패해도 다음 단계로 넘어감, 기록: runs/startup.log)
  1) 업무봇 서버 시작 + 브라우저 열기 (이미 켜져 있으면 브라우저만)
  2) 아침 갱신 (scripts/calendar_wallpaper/morning_routine.py --force)
     메신저 · 팀 휴가 · 내 할 일 · 제품별 진행상황(Jira) · 월페이퍼
  3) 문서이력 → Jira·진행상황 반영 (product-status/doc_sync.py, 반영할 게 있으면 메일·알림)
  4) 프로젝트 대응 전체 새로 확인 (projects/morning.py: 메일·Jira·첨부·요약·할 일)
  5) 직원 커뮤니케이션 실시간 반영
  6) 끝나면 Windows 알림
"""
import datetime, os, subprocess, sys, time, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RUNS = os.path.join(ROOT, "runs")
LOG = os.path.join(RUNS, "startup.log")
PY = sys.executable.replace("pythonw.exe", "python.exe")
NOWIN = 0x08000000 if os.name == "nt" else 0
HOME = os.path.expanduser("~")


def log(msg):
    os.makedirs(RUNS, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def run(name, args, cwd, timeout):
    t0 = time.time()
    try:
        r = subprocess.run(args, cwd=cwd, creationflags=NOWIN, timeout=timeout, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=dict(os.environ, PYTHONIOENCODING="utf-8", BOT_HUB_RUNS=RUNS))
        ok = r.returncode == 0
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-3:]
        log(f"[{name}] {'완료' if ok else '실패 코드 ' + str(r.returncode)} ({time.time() - t0:.0f}초)" + ("" if ok else " — " + " / ".join(tail)))
        return ok
    except subprocess.TimeoutExpired:
        log(f"[{name}] 시간 초과 ({timeout}초) — 건너뜀")
    except Exception as e:  # noqa: BLE001
        log(f"[{name}] 오류 — {e}")
    return False


def wait_network(sec=120):
    import socket
    end = time.time() + sec
    while time.time() < end:
        try:
            socket.create_connection(("www.googleapis.com", 443), timeout=5).close()
            return True
        except OSError:
            time.sleep(5)
    return False


def main():
    log("===== PC 시작: 전부 새로 받아오기 =====")
    run("업무봇 시작", ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File",
                     os.path.join(HOME, "scripts", "start_workbot.ps1")], HOME, 120)
    if not wait_network():
        log("네트워크 연결이 2분 넘게 안 돼서 새로 받아오기를 건너뜀")
        return
    results = {}
    wall = os.path.join(HOME, "scripts", "calendar_wallpaper")
    results["아침 갱신(메신저·팀 휴가·내 할 일·진행상황·월페이퍼)"] = run("아침 갱신", [PY, "-I", "morning_routine.py", "--force"], wall, 40 * 60)
    ps_dir = os.path.join(HERE, "product-status")
    results["문서이력 → Jira"] = run("문서이력 반영", [PY, "-I", "-X", "utf8", "doc_sync.py"], ps_dir, 15 * 60)
    pj_dir = os.path.join(HERE, "projects")
    results["프로젝트 대응"] = run("프로젝트 대응 새로 확인", [PY, "-X", "utf8", "morning.py"], pj_dir, 40 * 60)
    im_dir = os.path.join(HERE, "intimacy")
    results["직원 커뮤니케이션"] = run("직원 커뮤니케이션", [PY, "-X", "utf8", "-c", "import intimacy as im; im.refresh()"], im_dir, 20 * 60)
    ok = [k for k, v in results.items() if v]
    bad = [k for k, v in results.items() if not v]
    log(f"끝 — 성공 {len(ok)} · 실패 {len(bad)}" + (f" ({', '.join(bad)})" if bad else ""))
    try:
        sys.path.insert(0, ps_dir)
        import doc_sync
        doc_sync.toast("IoT업무봇 · 새로 받아오기 완료", f"성공 {len(ok)}개" + (f" · 실패: {', '.join(bad)}" if bad else " — 화면을 새로 고침하세요"))
    except Exception as e:  # noqa: BLE001
        log(f"알림 실패 — {e}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        log(f"실패: {e}\n{traceback.format_exc()}")
