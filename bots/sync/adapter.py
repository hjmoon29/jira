#!/usr/bin/env python3
# 업무 봇 저장소를 Git으로 동기화. 입력: argv[1]=기능, stdin=JSON / 출력: JSON
import json, os, platform, shutil, subprocess, sys

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
PC = {"Darwin": "Mac", "Windows": "Windows"}.get(platform.system(), platform.system())


def out(**kw):
    print(json.dumps(kw, ensure_ascii=False))
    sys.exit(0)


def git(*args):
    p = subprocess.run(["git", "-C", REPO, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    return p.returncode == 0, (p.stdout + p.stderr).strip()


def check():
    if not shutil.which("git"):
        out(ok=False, text="git 이 설치돼 있지 않습니다. Windows는 https://git-scm.com 에서 Git for Windows 를 설치하세요.")
    ok, _ = git("rev-parse", "--is-inside-work-tree")
    if not ok:
        out(ok=False, text=f"{REPO} 는 Git 저장소가 아닙니다. README의 '처음 설정' 을 따라 회사 Git 저장소에서 받아 주세요.")
    ok, remote = git("remote", "get-url", "origin")
    return remote if ok else None


def changes():
    ok, s = git("status", "--porcelain")
    return [l for l in s.splitlines() if l.strip()]


def status(q):
    remote = check()
    if remote:
        git("fetch", "--quiet", "origin")
    _, branch = git("status", "-sb")
    _, logs = git("log", "-8", "--date=format:%m-%d %H:%M", "--pretty=format:%ad  %an  %s")
    ch = changes()
    head = branch.splitlines()[0] if branch else ""
    behind = "behind" in head
    ahead = "ahead" in head
    lines = [f"저장소: {remote or '(원격 저장소 미연결 — README 처음 설정 참고)'}", f"이 PC: {PC} · {head.replace('## ', '')}", ""]
    if behind:
        lines.append("⬇ 다른 PC에서 올린 업데이트가 있습니다 → '업데이트 받기'")
    if ch:
        lines.append(f"⬆ 이 PC에서 바뀐 파일 {len(ch)}개 → '변경 올리기'")
        lines += ["   " + l for l in ch[:20]]
    elif ahead:
        lines.append("⬆ 아직 올리지 않은 기록이 있습니다 → '변경 올리기'")
    if not behind and not ch and not ahead:
        lines.append("✓ 최신 상태입니다")
    lines += ["", "최근 변경 기록", logs or "(없음)"]
    out(ok=True, text="\n".join(lines), summary=("업데이트 있음 " if behind else "") + (f"바뀐 파일 {len(ch)}개" if ch else "최신" if not behind else ""))


def pull(q):
    if not check():
        out(ok=False, text="원격 저장소가 연결돼 있지 않습니다.")
    ok, t = git("pull", "--rebase", "--autostash", "origin", "HEAD")
    if not ok:
        out(ok=False, text="업데이트를 받지 못했습니다. 같은 파일을 두 PC에서 동시에 고쳤을 수 있습니다.\n"
                           "Claude Code에 '업무 봇 git 충돌 해결해줘' 라고 요청하세요.\n\n" + t)
    out(ok=True, text=t + "\n\n화면을 새로 고치면 새 봇·설정이 반영됩니다. (hub.py 자체가 바뀐 경우 업무 봇를 껐다 켜세요)",
        summary="업데이트 받음")


def push(q):
    msg = (q.get("message") or "").strip()
    if not msg:
        out(ok=False, text="무엇을 바꿨는지 한 줄 입력하세요")
    if not check():
        out(ok=False, text="원격 저장소가 연결돼 있지 않습니다.")
    log = []
    if changes():
        git("add", "-A")
        ok, t = git("commit", "-m", f"{msg} ({PC})")
        log.append(t)
        if not ok:
            out(ok=False, text="\n".join(log))
    ok, t = git("pull", "--rebase", "--autostash", "origin", "HEAD")
    log.append(t)
    if not ok:
        out(ok=False, text="올리기 전에 다른 PC 변경을 합치지 못했습니다(충돌). Claude Code에 '업무 봇 git 충돌 해결해줘' 라고 요청하세요.\n\n" + "\n".join(log))
    ok, t = git("push", "origin", "HEAD")
    log.append(t)
    out(ok=ok, text="\n\n".join(x for x in log if x), summary=f"올림: {msg}" if ok else "올리기 실패")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    q = json.loads(sys.stdin.read() or "{}")
    {"status": status, "pull": pull, "push": push}.get(action, lambda q: out(ok=False, text="알 수 없는 기능"))(q)
