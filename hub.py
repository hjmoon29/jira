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
KEY = secrets.token_urlsafe(24)
PROGRESS = os.path.join(RUNS, "progress")
ACTIVE = {}  # (봇, 기능) → 시작 시각 — 화면을 새로 고쳐도 실행 중인 기능의 진행률을 다시 찾을 수 있게
ACTIVE_LOCK = threading.Lock()


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
    try:
        p = subprocess.run([sys.executable, "-I", "-X", "utf8", adapter, action_id], input=json.dumps(params, ensure_ascii=False),
                           capture_output=True, text=True, encoding="utf-8", cwd=os.path.dirname(adapter), env=env,
                           timeout=action.get("timeout", 300))
        try:
            res = json.loads(p.stdout)
        except ValueError:
            res = {"ok": p.returncode == 0, "text": (p.stdout + p.stderr).strip()}
        if p.returncode != 0 and p.stderr.strip() and not res.get("text"):
            res["text"] = p.stderr.strip()
        if p.returncode != 0 and not p.stdout.strip() and not p.stderr.strip():  # '실행 중지' 등으로 강제 종료된 경우
            res = {"ok": False, "headline": "⏹ 실행이 중지되었습니다", "text": "실행 도중 중지되었습니다.", "summary": "중지됨"}
    except subprocess.TimeoutExpired:
        res = {"ok": False, "text": f"시간 초과 ({action.get('timeout', 300)}초)"}
    finally:
        with ACTIVE_LOCK:
            ACTIVE.pop((bot_id, action_id), None)
        try:
            os.remove(pfile)
        except OSError:
            pass
    res.setdefault("ok", True)
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
        if self.path == "/api/history":
            return self.send(200, history())
        if self.path == "/api/limits":  # 상단 바의 한도 표시 — 실행 기록에 남기지 않음
            return self.send(200, run("daily-summary", "now", {}, log=False))
        self.send(404, {"ok": False, "text": "없는 경로"})

    def do_POST(self):
        if not self.guard(True):
            return
        if self.path == "/api/upload":
            return self.upload()
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
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        print(f"이미 실행 중인 것 같습니다 → http://127.0.0.1:{PORT}")
        webbrowser.open(f"http://127.0.0.1:{PORT}")
        return
    url = f"http://127.0.0.1:{PORT}"
    print(f"IoT업무봇(Beta) 실행 중 → {url}  (끝내려면 이 창에서 Ctrl+C)")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")


if __name__ == "__main__":
    main()
