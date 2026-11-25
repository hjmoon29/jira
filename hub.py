#!/usr/bin/env python3
"""IoT업무봇: 여러 IoT업무봇을 한 화면에서 실행하는 로컬 웹 프로그램.

실행: python3 hub.py (Windows: py -3 hub.py, 또는 IoT업무봇 실행.bat) → 브라우저에서 http://127.0.0.1:8765 이 열린다.
봇 추가: bots/<봇id>/bot.json (화면 정의) + adapter.py (실행) — README.md 참고.
이 Mac 안에서만 열리며(127.0.0.1), 화면마다 발급한 키가 있어야 실행 요청을 받는다.
"""
import json, os, secrets, subprocess, sys, threading, time, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
BOTS = os.path.join(HERE, "bots")
RUNS = os.path.join(HERE, "runs")
HISTORY = os.path.join(RUNS, "history.jsonl")
PORT = int(os.environ.get("BOT_HUB_PORT", "8765"))
KEY = os.environ.pop("BOT_HUB_KEY_RESTART", "") or secrets.token_urlsafe(24)  # 스스로 다시 켤 때는 같은 키 → 열려 있는 화면이 그대로 동작
PROGRESS = os.path.join(RUNS, "progress")
ACTIVE = {}  # (봇, 기능) → 시작 시각 — 화면을 새로 고쳐도 실행 중인 기능의 진행률을 다시 찾을 수 있게
ACTIVE_LOCK = threading.Lock()
RUNNING = {}   # (봇, 기능) → 실행 중인 프로세스 — '중지' 버튼으로 끌 수 있게
STOPPED = set()  # 사용자가 중지한 (봇, 기능)


def kill_tree(proc):
    """봇 프로세스와 그 아래에서 띄운 것(Edge 창·Claude 등)까지 함께 끈다."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        else:
            import signal
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


def stop(bot_id, action_id=""):
    """실행 중인 기능을 중지. action_id 가 비면 그 봇에서 도는 기능 전부. 남은 잠금 파일도 지운다."""
    with ACTIVE_LOCK:
        keys = [k for k in RUNNING if k[0] == bot_id and (not action_id or k[1] == action_id)]
        procs = [(k, RUNNING[k]) for k in keys]
        STOPPED.update(keys)
    for _, p in procs:
        kill_tree(p)
    # 봇이 '실행 중' 표시로 남겨 둔 잠금 파일(run.lock 등) 정리 — 다음 실행이 '이미 실행 중'으로 막히지 않게
    d = os.path.join(RUNS, bot_id)
    if procs and os.path.isdir(d):
        for f in os.listdir(d):
            if f.endswith(".lock"):
                try:
                    os.remove(os.path.join(d, f))
                except OSError:
                    pass
    return {"ok": True, "stopped": [k[1] for k, _ in procs],
            "text": f"{len(procs)}개 기능을 중지했습니다." if procs else "실행 중인 기능이 없습니다."}


def progress_of(bot_id):
    """그 봇에서 지금 실행 중인 기능들의 진행 상태 {기능: {running, pct, msg, t0}}"""
    out = {}
    with ACTIVE_LOCK:
        items = [(a, t0) for (b, a), t0 in ACTIVE.items() if b == bot_id]
    for a, t0 in items:
        r = {"running": True, "t0": t0, "pct": None, "msg": ""}
        try:
            r.update(json.load(open(os.path.join(PROGRESS, f"{bot_id}__{a}.json"), encoding="utf-8")))
        except (OSError, ValueError):
            pass
        out[a] = r
    return out
HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}


def load_bots():
    bots = []
    for name in sorted(os.listdir(BOTS)):
        path = os.path.join(BOTS, name, "bot.json")
        if not os.path.isfile(path):
            continue
        try:
            b = json.load(open(path, encoding="utf-8"))
        except ValueError as e:
            print(f"[경고] {path} 형식 오류: {e}", file=sys.stderr)
            continue
        b["id"] = name
        bots.append(b)
    return sorted(bots, key=lambda b: (b.get("order", 100), b["name"]))


def badges():
    """왼쪽 메뉴 배지: 내 할 일의 오늘·지난 기한 건수, 동기화(올릴 변경 있음) 여부. 실패하면 조용히 빈 값."""
    out = {}
    try:
        import datetime
        tasks = json.load(open(os.path.join(RUNS, "my-tasks", "tasks.json"), encoding="utf-8"))
        tasks = tasks if isinstance(tasks, list) else tasks.get("tasks", [])
        today = datetime.date.today().isoformat()
        n = sum(1 for t in tasks if t.get("status") == "open" and t.get("due") and t["due"] <= today)
        if n:
            out["my-tasks"] = {"n": n, "title": f"오늘·지난 기한 {n}건"}
    except Exception:  # noqa: BLE001
        pass
    try:
        g = lambda *a: subprocess.run(["git", *a], cwd=HERE, capture_output=True, text=True, encoding="utf-8", timeout=8)
        dirty = [l for l in g("status", "--porcelain").stdout.splitlines() if l.strip()]
        ahead = g("rev-list", "--count", "@{u}..HEAD").stdout.strip()
        ahead = int(ahead) if ahead.isdigit() else 0
        if dirty or ahead:
            out["sync"] = {"dot": True, "title": f"올릴 변경 있음 (파일 {len(dirty)}개" + (f", 커밋 {ahead}개" if ahead else "") + ") — 변경 올리기"}
    except Exception:  # noqa: BLE001
        pass
    if AUTO_SYNC["restart"]:
        out["sync"] = {"dot": True, "title": "IoT업무봇 본체가 업데이트됨 — 껐다 켜야 반영됩니다"}
    elif AUTO_SYNC["at"] and not AUTO_SYNC["ok"]:
        out["sync"] = {"dot": True, "title": f"{AUTO_SYNC['at']} {AUTO_SYNC['text']}"}
    elif DATA_SYNC["at"] and not DATA_SYNC["ok"]:
        out["sync"] = {"dot": True, "title": f"{DATA_SYNC['at']} 데이터 동기화 문제 — '데이터 맞추기'에서 확인"}
    return out


# ── 자동 동기화: 켜 있는 동안 주기적으로 Git 저장소와 맞춰 Mac·Windows 가 같은 봇 코드를 쓰게 함 ──
# 이 PC 변경이 있으면 '변경 올리기'(비밀값 검사 → 커밋 → 다른 PC 변경 합치기 → 올리기), 없으면 '업데이트 받기'.
# 간격(분)은 환경변수 BOT_HUB_AUTO_SYNC_MIN (기본 10, 0 이면 끔). 봇이 실행 중이면 그 회차는 건너뜀.
AUTO_SYNC_MIN = int(os.environ.get("BOT_HUB_AUTO_SYNC_MIN", "10") or 0)
AUTO_SYNC = {"at": "", "text": "", "ok": True, "restart": False}


def auto_sync_once():
    if not os.path.isdir(os.path.join(HERE, ".git")):
        return
    with ACTIVE_LOCK:
        if ACTIVE:
            return
    g = lambda *a: subprocess.run(["git", *a], cwd=HERE, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=60, env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    before = g("rev-parse", "HEAD").stdout.strip()
    dirty = [l for l in g("status", "--porcelain").stdout.splitlines() if l.strip()]
    ahead = g("rev-list", "--count", "@{u}..HEAD").stdout.strip()
    if dirty or (ahead.isdigit() and int(ahead)):
        msg = ((run("sync", "push_init", {}, log=False).get("data") or {}).get("values") or {}).get("message") or "봇 수정"
        res = run("sync", "push", {"message": "[자동 동기화] " + msg})
        what = "올림"
    else:
        if g("fetch", "--quiet", "origin").returncode != 0:
            AUTO_SYNC.update(at=time.strftime("%H:%M"), ok=False, text="저장소에 연결하지 못함 (네트워크 확인)")
            return
        behind = g("rev-list", "--count", "HEAD..@{u}").stdout.strip()
        if not (behind.isdigit() and int(behind)):
            AUTO_SYNC.update(at=time.strftime("%H:%M"), ok=True, text="최신 상태")
            return
        res = run("sync", "pull", {})
        what = "받음"
    after = g("rev-parse", "HEAD").stdout.strip()
    if res.get("ok") and before != after and _hub_hash() != HUB_HASH:  # 지금 돌고 있는 것과 디스크의 hub.py 가 다를 때만
        AUTO_SYNC["restart"] = True  # 화면·봇은 바로 반영되지만 hub.py 자체는 다시 켜야 반영 → 아래에서 스스로 다시 켬
    AUTO_SYNC.update(at=time.strftime("%H:%M"), ok=bool(res.get("ok")),
                     text=f"변경 {what}" if res.get("ok") else f"자동 동기화 실패 — '{('변경 올리기' if what == '올림' else '업데이트 받기')}'에서 내용 확인")
    if res.get("ok") and before != after:
        bad = py39_check_all()
        if bad:  # 다른 PC 가 이 PC(Python 3.9)에서 안 되는 문법을 올림 → 봇이 안 열릴 수 있음을 알림
            AUTO_SYNC.update(ok=False, text="받은 코드에 이 PC에서 안 되는 문법: " + " / ".join(bad[:3]) + " — Claude Code에 고쳐 달라고 하세요")
            print("[자동 동기화] " + AUTO_SYNC["text"], flush=True)
    if AUTO_SYNC["restart"]:
        restart_self()


def py39_check_all():
    try:
        sys.path.insert(0, os.path.join(HERE, "bots"))
        import py39check
        files = subprocess.run(["git", "ls-files", "*.py"], cwd=HERE, capture_output=True, text=True, encoding="utf-8").stdout.split()
        return py39check.check([os.path.join(HERE, f) for f in files])
    except Exception:  # noqa: BLE001
        return []


SRV = {}


def _hub_hash():
    try:
        import hashlib
        return hashlib.md5(open(os.path.join(HERE, "hub.py"), "rb").read()).hexdigest()
    except OSError:
        return ""


HUB_HASH = _hub_hash()  # 이 프로세스가 시작할 때의 hub.py


def restart_self():
    """hub.py 가 바뀌면 스스로 다시 켠다: 같은 포트·같은 화면 키로 새 프로세스를 띄우고 이 프로세스는 끝낸다.
    봇이 실행 중이면 다음 회차로 미룬다. (Windows 창 있는 실행은 창이 닫히고 업무봇은 창 없이 계속 돈다 — 끄기는 '종료' 파일)"""
    with ACTIVE_LOCK:
        if ACTIVE:
            return
    try:
        bad = py39_check_all()
        if bad:  # 새 hub.py 가 이 PC 에서 문법 오류면 다시 켜지 않고 지금 것으로 계속
            return
        args = [sys.executable] + (["-I"] if sys.flags.isolated else []) + [os.path.join(HERE, "hub.py"), "--no-browser", "--restarted"]
        log = open(os.path.join(RUNS, "hub.log"), "a", encoding="utf-8")
        kw = {"creationflags": 0x00000008 | 0x00000200} if os.name == "nt" else {"start_new_session": True}  # DETACHED_PROCESS | NEW_PROCESS_GROUP
        subprocess.Popen(args, cwd=HERE, stdout=log, stderr=log, stdin=subprocess.DEVNULL, close_fds=True,
                         env=dict(os.environ, BOT_HUB_KEY_RESTART=KEY), **kw)
        print("[자동 동기화] IoT업무봇 본체가 바뀌어 다시 켭니다", flush=True)
        if SRV.get("srv"):
            SRV["srv"].server_close()
    except Exception as e:  # noqa: BLE001
        AUTO_SYNC.update(ok=False, text=f"다시 켜기 실패({e.__class__.__name__}) — 업무봇을 직접 껐다 켜 주세요")
        return
    os._exit(0)


# 데이터(runs/) 동기화: Google Drive 'IoT업무봇/runs' 와 3분마다 + 봇 실행 직후 (BOT_HUB_DATA_SYNC_MIN, 0 이면 끔)
DATA_SYNC_MIN = int(os.environ.get("BOT_HUB_DATA_SYNC_MIN", "3") or 0)
DATA_SYNC = {"at": "", "ok": True, "text": ""}
DATA_LOCK = threading.Lock()


def runs_fingerprint():
    """runs/ 파일 (경로, 수정 시각, 크기) 요약 — 봇 실행 뒤 데이터가 실제로 바뀌었을 때만 맞추려고"""
    h = []
    for d, dirs, files in os.walk(RUNS):
        dirs[:] = [x for x in dirs if x not in (".sync", "progress")]
        for f in files:
            try:
                st = os.stat(os.path.join(d, f))
                h.append((d, f, st.st_mtime, st.st_size))
            except OSError:
                pass
    return hash(tuple(sorted(h)))


DATA_SYNC["fp"] = None
DATA_SYNC["again"] = False


def data_sync_now(after_run=False):
    if not DATA_SYNC_MIN:
        return
    if after_run and runs_fingerprint() == DATA_SYNC["fp"]:
        return
    if not DATA_LOCK.acquire(blocking=False):
        DATA_SYNC["again"] = True  # 지금 도는 회차가 끝나면 한 번 더
        return
    try:
        with ACTIVE_LOCK:
            busy = [k for k in ACTIVE if k[0] != "sync"]
        if busy:
            return
        DATA_SYNC["again"] = False
        res = run("sync", "data", {"quiet": True}, log=False)
        DATA_SYNC.update(at=time.strftime("%H:%M"), ok=bool(res.get("ok")), text=(res.get("text") or "")[:300], fp=runs_fingerprint())
    except Exception as e:  # noqa: BLE001
        DATA_SYNC.update(at=time.strftime("%H:%M"), ok=False, text=f"데이터 동기화 오류: {e.__class__.__name__}")
    finally:
        DATA_LOCK.release()
    if DATA_SYNC["again"]:
        data_sync_now(after_run=True)


def data_sync_loop():
    time.sleep(8)
    while True:
        data_sync_now()
        time.sleep(DATA_SYNC_MIN * 60)


def auto_sync_loop():
    time.sleep(5)
    while True:
        try:
            auto_sync_once()
        except Exception as e:  # noqa: BLE001
            AUTO_SYNC.update(at=time.strftime("%H:%M"), ok=False, text=f"자동 동기화 오류: {e.__class__.__name__}")
        time.sleep(AUTO_SYNC_MIN * 60)


def history(limit=30):
    try:
        lines = open(HISTORY, encoding="utf-8").read().splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def run(bot_id, action_id, params, log=True):
    bot = next((b for b in load_bots() if b["id"] == bot_id), None)
    action = bot and next((a for a in bot.get("actions", []) if a["id"] == action_id), None)
    if not action:
        return {"ok": False, "text": f"알 수 없는 봇/기능: {bot_id}/{action_id}"}
    adapter = os.path.join(BOTS, bot_id, "adapter.py")
    # Windows 기본 인코딩(cp949)에서도 한글이 깨지지 않게 봇은 항상 UTF-8로 주고받는다 (-I 는 PYTHONUTF8 등 환경변수를 무시하므로 -X utf8 로 지정)
    os.makedirs(PROGRESS, exist_ok=True)
    pfile = os.path.join(PROGRESS, f"{bot_id}__{action_id}.json")
    try:
        os.remove(pfile)
    except OSError:
        pass
    env = dict(os.environ, BOT_HUB_RUNS=RUNS, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", BOT_HUB_PROGRESS=pfile)
    t0 = time.time()
    if action.get("progress"):
        with ACTIVE_LOCK:
            ACTIVE[(bot_id, action_id)] = t0
    key = (bot_id, action_id)
    with ACTIVE_LOCK:
        STOPPED.discard(key)
    try:
        # 별도 프로세스 그룹으로 띄워 '중지' 때 하위 프로세스까지 함께 끌 수 있게
        kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        p = subprocess.Popen([sys.executable, "-I", "-X", "utf8", adapter, action_id], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, encoding="utf-8", cwd=os.path.dirname(adapter), env=env, **kw)
        with ACTIVE_LOCK:
            RUNNING[key] = p
        try:
            out, err = p.communicate(json.dumps(params, ensure_ascii=False), timeout=action.get("timeout", 300))
        except subprocess.TimeoutExpired:
            kill_tree(p)
            p.communicate()
            raise
        try:
            res = json.loads(out)
        except ValueError:
            res = {"ok": p.returncode == 0, "text": (out + err).strip()}
        if p.returncode != 0 and err.strip() and not res.get("text"):
            res["text"] = err.strip()
        if key in STOPPED or (p.returncode != 0 and not out.strip() and not err.strip()):  # '중지' 등으로 강제 종료된 경우
            res = {"ok": False, "stopped": True, "headline": "⏹ 실행이 중지되었습니다", "text": "실행 도중 중지되었습니다.", "summary": "중지됨"}
    except subprocess.TimeoutExpired:
        res = {"ok": False, "text": f"시간 초과 ({action.get('timeout', 300)}초)"}
    finally:
        with ACTIVE_LOCK:
            ACTIVE.pop((bot_id, action_id), None)
            RUNNING.pop(key, None)
            STOPPED.discard(key)
        try:
            os.remove(pfile)
        except OSError:
            pass
    res.setdefault("ok", True)
    if bot_id != "sync":  # 봇이 데이터를 바꿨으면 바로 다른 PC 와 맞춤 (안 바뀌었으면 건너뜀)
        threading.Thread(target=data_sync_now, kwargs={"after_run": True}, daemon=True).start()
    if res.get("quiet") or not log:  # 화면 내부용 호출(자동완성 목록 등)은 실행 기록에 남기지 않음
        return res
    entry = {"t": int(t0), "bot": bot["name"], "action": action["label"], "ok": res["ok"],
             "sec": round(time.time() - t0, 1), "summary": res.get("summary") or (res.get("text") or "").strip().split("\n")[-1][:120]}
    with open(HISTORY, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return res


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def guard(self, need_key):
        # 다른 웹사이트가 이 서버를 몰래 호출하지 못하게: 호스트 이름과 화면 키 확인
        if self.headers.get("Host") not in HOSTS:
            self.send(403, {"ok": False, "text": "허용되지 않은 호스트"})
            return False
        if need_key and self.headers.get("X-Hub-Key") != KEY:
            self.send(403, {"ok": False, "text": "화면 키가 맞지 않습니다. 페이지를 새로 고치세요."})
            return False
        return True

    def do_GET(self):
        if not self.guard(False):
            return
        if self.path in ("/", "/index.html"):
            html = open(os.path.join(HERE, "index.html"), encoding="utf-8").read().replace("__HUB_KEY__", KEY)
            return self.send(200, html.encode(), "text/html; charset=utf-8")
        if not self.guard(True):
            return
        if self.path == "/api/bots":
            return self.send(200, load_bots())
        if self.path.startswith("/api/progress?"):
            import urllib.parse
            q = urllib.parse.parse_qs(self.path.split("?", 1)[1])
            return self.send(200, progress_of((q.get("bot") or [""])[0]))
        if self.path == "/api/badges":
            return self.send(200, badges())
        if self.path == "/api/history":
            return self.send(200, history())
        if self.path == "/api/running":  # 지금 실행 중인 기능 목록 (화면 새로 고침 뒤 '중지' 버튼용)
            with ACTIVE_LOCK:
                return self.send(200, [{"bot": b, "action": a} for b, a in RUNNING])
        if self.path == "/api/limits":  # 상단 바의 한도 표시 — 실행 기록에 남기지 않음
            return self.send(200, run("daily-summary", "now", {}, log=False))
        self.send(404, {"ok": False, "text": "없는 경로"})

    def do_POST(self):
        if not self.guard(True):
            return
        if self.path == "/api/upload":
            return self.upload()
        if self.path == "/api/stop":
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            except ValueError:
                return self.send(400, {"ok": False, "text": "요청 형식 오류"})
            return self.send(200, stop(req.get("bot", ""), req.get("action", "")))
        if self.path != "/api/run":
            return self.send(404, {"ok": False, "text": "없는 경로"})
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except ValueError:
            return self.send(400, {"ok": False, "text": "요청 형식 오류"})
        self.send(200, run(req.get("bot", ""), req.get("action", ""), req.get("params") or {}))


UPLOAD_MAX = 4 * 1024 ** 3  # 4GB (긴 회의 녹화 파일 고려)


def _upload(self):
    """파일 첨부: 본문을 그대로 runs/uploads/ 에 저장하고 경로를 돌려준다 (봇은 이 경로를 입력값으로 받는다)"""
    import re, urllib.parse
    n = int(self.headers.get("Content-Length") or 0)
    if not n or n > UPLOAD_MAX:
        return self.send(400, {"ok": False, "text": "파일이 비었거나 너무 큽니다 (최대 4GB)"})
    name = urllib.parse.unquote(self.headers.get("X-File-Name") or "file")
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", os.path.basename(name.replace("\\", "/")))[-120:] or "file"
    up = os.path.join(RUNS, "uploads")
    os.makedirs(up, exist_ok=True)
    for f in os.listdir(up):  # 하루 지난 첨부는 정리 (봇이 가져간 파일은 이미 옮겨져 없음)
        fp = os.path.join(up, f)
        if time.time() - os.path.getmtime(fp) > 86400:
            try:
                os.remove(fp)
            except OSError:
                pass
    path = os.path.join(up, time.strftime("%Y%m%d-%H%M%S_") + name)
    left = n
    with open(path, "wb") as f:
        while left:
            chunk = self.rfile.read(min(left, 1 << 20))
            if not chunk:
                break
            f.write(chunk)
            left -= len(chunk)
    if left:
        os.remove(path)
        return self.send(400, {"ok": False, "text": "파일 전송이 중간에 끊겼습니다"})
    self.send(200, {"ok": True, "path": path, "name": name, "size": n})


Handler.upload = _upload


def main():
    for st in (sys.stdout, sys.stderr):  # Windows 콘솔에서도 한글 출력
        try:
            st.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    os.makedirs(RUNS, exist_ok=True)
    srv = None
    for i in range(60 if "--restarted" in sys.argv else 1):  # 스스로 다시 켠 경우: 이전 프로세스가 포트를 놓을 때까지 기다림
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
            break
        except OSError:
            time.sleep(0.5)
    if srv is None:
        print(f"이미 실행 중인 것 같습니다 → http://127.0.0.1:{PORT}")
        if "--restarted" not in sys.argv:
            webbrowser.open(f"http://127.0.0.1:{PORT}")
        return
    SRV["srv"] = srv
    url = f"http://127.0.0.1:{PORT}"
    print(f"IoT업무봇(Beta) 실행 중 → {url}  (끝내려면 이 창에서 Ctrl+C)")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    if DATA_SYNC_MIN > 0:
        threading.Thread(target=data_sync_loop, daemon=True).start()
        print(f"데이터 동기화: {DATA_SYNC_MIN}분마다·봇 실행 직후 runs/ 를 Google Drive 'IoT업무봇/runs' 와 맞춤 (끄려면 BOT_HUB_DATA_SYNC_MIN=0)")
    if AUTO_SYNC_MIN > 0:
        threading.Thread(target=auto_sync_loop, daemon=True).start()
        print(f"자동 동기화: {AUTO_SYNC_MIN}분마다 Git 저장소와 맞춤 (끄려면 BOT_HUB_AUTO_SYNC_MIN=0)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")


if __name__ == "__main__":
    main()
