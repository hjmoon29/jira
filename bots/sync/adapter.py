#!/usr/bin/env python3
# IoT업무봇 저장소를 Git으로 동기화. 입력: argv[1]=기능, stdin=JSON / 출력: JSON
import json, os, platform, re, shutil, subprocess, sys

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
PC = {"Darwin": "Mac", "Windows": "Windows"}.get(platform.system(), platform.system())
# zip으로 설치한 PC가 처음 연결할 저장소 (접근 권한이 있는 GitHub 계정 필요)
REMOTE = os.environ.get("BOT_HUB_REMOTE") or "https://github.com/hjmoon29/jira.git"
BRANCH = "main"


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
    if not os.path.isdir(os.path.join(REPO, ".git")):
        out(ok=False, text=f"아직 저장소에 연결되지 않았습니다 (zip으로 설치한 경우).\n'저장소 연결' 탭에서 한 번 연결하면 업데이트 받기·변경 올리기를 쓸 수 있습니다.\n저장소: {REMOTE}")
    ok, remote = git("remote", "get-url", "origin")
    return remote if ok else None


def changes():
    ok, s = git("status", "--porcelain")
    return [l for l in s.splitlines() if l.strip()]


# ── 올리기 전 확인: 올릴 파일 목록 · 비밀값 검사 · 메시지 초안 ─────────────────
KIND = {"?": "추가", "A": "추가", "M": "수정", "D": "삭제", "R": "이름 변경", "C": "복사", "T": "수정", "U": "충돌"}


def pending():
    """올라갈 파일: [(종류, 경로, 이전 경로)] — .gitignore 로 빠지는 파일(runs/·백업·토큰)은 포함되지 않는다."""
    # git() 는 출력 앞뒤 공백을 지우므로(첫 줄 ' M' 의 공백이 사라져 경로가 잘림) 여기서는 그대로 받는다
    s = subprocess.run(["git", "-C", REPO, "-c", "core.quotepath=false", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=dict(os.environ, GIT_TERMINAL_PROMPT="0")).stdout
    parts, out_, i = s.split("\0"), [], 0
    while i < len(parts):
        e = parts[i]
        i += 1
        if len(e) < 4:
            continue
        xy, path = e[:2], e[3:]
        code = (xy[0] if xy[0] not in " ?" else xy[1]) if xy != "??" else "?"
        orig = ""
        if "R" in xy or "C" in xy:
            orig = parts[i] if i < len(parts) else ""
            i += 1
        out_.append((KIND.get(code, "수정"), path, orig))
    return out_


# 차단: 실제 비밀값 형태 / 경고: 비밀값일 수도 있는 긴 문자열 (줄 끝에 'secret-scan: ignore' 를 적으면 그 줄은 건너뜀)
BLOCK = [
    ("GitHub 토큰", r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}"),
    ("AWS 액세스 키", r"\bAKIA[0-9A-Z]{16}\b"),
    ("Figma 토큰", r"\bfigd_[A-Za-z0-9_-]{20,}"),
    ("Google API 키", r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ("Google OAuth 값", r"\bya29\.[0-9A-Za-z_-]{20,}|\b1//0[0-9A-Za-z_-]{30,}|GOCSPX-[0-9A-Za-z_-]{20,}"),
    ("Slack 토큰", r"\bxox[abprs]-[0-9A-Za-z-]{10,}"),
    ("Anthropic/OpenAI 키", r"\bsk-(ant-)?[A-Za-z0-9_-]{30,}"),
    ("개인 키", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("JWT", r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ("Authorization 헤더 값", r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9+/_.=-]{24,}"),
    ("비밀번호·토큰 값", r"""(?i)["']?(password|passwd|pwd|secret|client_secret|refresh_token|access_token|api[_-]?key|token|pat)["']?\s*[:=]\s*["'][^"'\s]{12,}["']"""),
]
WARN = [("긴 무작위 문자열", r"(?<![A-Za-z0-9+/_-])[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9+/_-])")]
BAD_NAMES = re.compile(r"(?i)(^|/)(\.env(\..*)?|.*\.pem|.*\.p12|.*\.key|id_rsa.*|token|.*token\.json|credentials?\.json|.*cookies?\.json|gmail\.json|.*secret.*)$")


def added_lines(kind, path):
    """수정 파일은 이번에 추가된 줄만, 새 파일은 전체를 검사한다. [(줄번호, 내용)]"""
    full = os.path.join(REPO, path)
    if kind == "삭제" or not os.path.isfile(full) or os.path.getsize(full) > 2_000_000:
        return []
    if kind in ("추가", "복사"):
        b = open(full, "rb").read()
        if b"\0" in b[:4096]:
            return []  # 이미지 등 바이너리
        return list(enumerate(b.decode("utf-8", "replace").splitlines(), 1))
    ok, d = git("-c", "core.quotepath=false", "diff", "HEAD", "-U0", "--no-color", "--", path)
    res, n = [], 0
    for line in d.splitlines():
        m = re.match(r"@@ -\S+ \+(\d+)", line)
        if m:
            n = int(m.group(1))
        elif line.startswith("+") and not line.startswith("+++"):
            res.append((n, line[1:]))
            n += 1
    return res


def mask(s):
    return s[:6] + "…" + f"({len(s)}자)" if len(s) > 10 else "…"


def scan(files):
    blocks, warns = [], []
    for kind, path, _ in files:
        if kind != "삭제" and BAD_NAMES.search(path):
            blocks.append(f"{path} — 토큰·인증 파일로 보이는 이름입니다 (.gitignore 에 추가 권장)")
        for no, line in added_lines(kind, path):
            if "secret-scan: ignore" in line:
                continue
            for name, rx in BLOCK:
                m = re.search(rx, line)
                if m:
                    blocks.append(f"{path}:{no} — {name}: {mask(m.group(0))}")
                    break
            else:
                for name, rx in WARN:
                    m = re.search(rx, line)
                    if m and not re.fullmatch(r"[0-9a-f]{40,64}", m.group(0)):  # git 해시·sha256 은 제외
                        warns.append(f"{path}:{no} — {name}: {mask(m.group(0))}")
    return blocks, warns


def bot_names():
    names = {}
    for d in os.listdir(os.path.join(REPO, "bots")):
        try:
            names[d] = json.load(open(os.path.join(REPO, "bots", d, "bot.json"), encoding="utf-8"))["name"]
        except (OSError, ValueError, KeyError):
            pass
    return names


ROOT_NAMES = {"index.html": "화면", "hub.py": "서버", "setup.py": "설치", "README.md": "안내", ".gitignore": "제외 설정"}


def suggest(files):
    """바뀐 파일로 커밋 메시지 초안: '제품별 진행상황 추가, IoT업무봇 동기화·화면 수정'"""
    names, new, mod = bot_names(), [], []
    have = set(git("ls-files", "bots")[1].split("\n"))
    for kind, path, _ in files:
        p = path.split("/")
        if p[0] == "bots" and len(p) > 2:
            label = names.get(p[1], p[1])
            is_new = not any(h.startswith(f"bots/{p[1]}/") for h in have)
            (new if is_new else mod).append(label)
        elif p[0] == "skills":
            mod.append("스킬")
        else:
            mod.append(ROOT_NAMES.get(path, os.path.splitext(p[-1])[0]))
    uniq = lambda xs: list(dict.fromkeys(xs))
    new, mod = uniq(new), [x for x in uniq(mod) if x not in new]
    parts = ([("·".join(new[:4]) + (" 등" if len(new) > 4 else "") + " 추가")] if new else []) + \
            ([("·".join(mod[:4]) + (" 등" if len(mod) > 4 else "") + " 수정")] if mod else [])
    return ", ".join(parts)


def report(files, blocks, warns):
    lines = [f"올라갈 파일 {len(files)}개 (runs/·백업·토큰 파일은 .gitignore 로 제외)"]
    for kind in ("추가", "수정", "이름 변경", "복사", "삭제", "충돌"):
        fs = [f for f in files if f[0] == kind]
        if fs:
            lines.append(f"\n[{kind} {len(fs)}]")
            lines += [f"  {p}" + (f"  ← {o}" if o else "") for _, p, o in fs]
    lines.append("")
    if blocks:
        lines.append(f"⛔ 비밀값 의심 {len(blocks)}건 — 이대로는 올리지 않습니다. 값을 지우거나 파일을 .gitignore 에 넣으세요.")
        lines += ["  " + b for b in blocks]
    else:
        lines.append("✓ 비밀값 검사 통과 (토큰·비밀번호·API 키·인증 파일 없음)")
    if warns:
        lines.append(f"\n⚠ 확인 권장 {len(warns)}건 (긴 무작위 문자열 — 비밀값이 아니면 그대로 올라갑니다)")
        lines += ["  " + w for w in warns[:15]] + (["  …"] if len(warns) > 15 else [])
    return "\n".join(lines)


def preview(q):
    check()
    files = pending()
    if not files:
        ok, head = git("status", "-sb")
        out(ok=True, text="올릴 변경이 없습니다." + ("\n아직 올리지 않은 커밋이 있으면 '올리기'로 보낼 수 있습니다." if "ahead" in head else ""), quiet=True)
    b, w = scan(files)
    out(ok=not b, text=f"메시지 초안: {suggest(files)}\n\n" + report(files, b, w), quiet=True)


def push_init(q):
    files = pending() if os.path.isdir(os.path.join(REPO, ".git")) else []
    out(ok=True, quiet=True, data={"values": {"message": suggest(files) if files else ""}})


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
                           "Claude Code에 'IoT업무봇 git 충돌 해결해줘' 라고 요청하세요.\n\n" + t)
    out(ok=True, text=t + "\n\n화면을 새로 고치면 새 봇·설정이 반영됩니다. (hub.py 자체가 바뀐 경우 IoT업무봇을 껐다 켜세요)",
        summary="업데이트 받음")


def push(q):
    msg = (q.get("message") or "").strip()
    if not msg:
        out(ok=False, text="무엇을 바꿨는지 한 줄 입력하세요")
    if not check():
        out(ok=False, text="원격 저장소가 연결돼 있지 않습니다.")
    log = []
    files = pending()
    if files:
        blocks, warns = scan(files)
        if blocks:  # 비밀값이 섞이면 커밋하지 않는다
            out(ok=False, text=report(files, blocks, warns), summary=f"올리기 중단: 비밀값 의심 {len(blocks)}건")
        log.append(report(files, blocks, warns))
    if changes():
        git("add", "-A")
        ok, t = git("commit", "-m", f"{msg} ({PC})")
        log.append(t)
        if not ok:
            out(ok=False, text="\n".join(log))
    ok, t = git("pull", "--rebase", "--autostash", "origin", "HEAD")
    log.append(t)
    if not ok:
        out(ok=False, text="올리기 전에 다른 PC 변경을 합치지 못했습니다(충돌). Claude Code에 'IoT업무봇 git 충돌 해결해줘' 라고 요청하세요.\n\n" + "\n".join(log))
    ok, t = git("push", "origin", "HEAD")
    log.append(t)
    out(ok=ok, text="\n\n".join(x for x in log if x), summary=f"올림: {msg}" if ok else "올리기 실패")


def connect(q):
    # zip 설치본을 저장소에 붙인다: 내 파일은 그대로 두고 저장소 기록만 가져온 뒤, 차이는 '바뀐 파일'로 보이게 한다
    if not shutil.which("git"):
        out(ok=False, text="git 이 설치돼 있지 않습니다. https://git-scm.com 에서 Git for Windows 를 설치한 뒤 다시 실행하세요.")
    if os.path.isdir(os.path.join(REPO, ".git")):
        out(ok=True, text="이미 저장소에 연결돼 있습니다. '상태' 탭에서 확인하세요.")
    remote = (q.get("remote") or REMOTE).strip()
    log = []
    for args in (("init", "-q"), ("remote", "add", "origin", remote)):
        ok, t = git(*args)
        log.append(t)
        if not ok:
            out(ok=False, text="\n".join(x for x in log if x))
    # 첫 fetch는 GitHub 로그인 창(Git Credential Manager)이 뜰 수 있어 터미널 입력 금지를 풀지 않고 그대로 둔다
    p = subprocess.run(["git", "-C", REPO, "fetch", "origin", BRANCH], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    if p.returncode != 0:
        shutil.rmtree(os.path.join(REPO, ".git"), ignore_errors=True)
        out(ok=False, text="저장소를 받지 못했습니다. GitHub 로그인 창이 떴다면 로그인 후 다시 실행하세요.\n"
                           "저장소 접근 권한이 없으면 문효준님께 GitHub 아이디를 알려 초대를 받아야 합니다.\n\n" + (p.stdout + p.stderr).strip())
    for args in (("reset", "-q", f"origin/{BRANCH}"), ("branch", "-q", "-M", BRANCH),
                 ("branch", "-q", f"--set-upstream-to=origin/{BRANCH}")):
        git(*args)
    # 설치용 zip에서 뺀 파일(.gitignore·README·스킬 등)은 저장소 것으로 채운다 — .gitignore가 없으면 runs/ 까지 올라갈 수 있다
    _, missing = git("ls-files", "--deleted", "-z")
    paths = [x for x in missing.split("\0") if x]
    if paths:
        git("checkout", "--", *paths)
    ch = changes()
    out(ok=True, text=f"저장소에 연결했습니다: {remote}\n"
                      + (f"내 PC 파일과 저장소가 다른 곳 {len(ch)}개 — '업데이트 받기' 전에 '상태'로 확인하세요." if ch else "저장소와 내 파일이 같습니다."),
        summary="저장소 연결")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    q = json.loads(sys.stdin.read() or "{}")
    {"status": status, "pull": pull, "push": push, "connect": connect, "preview": preview, "push_init": push_init}.get(action, lambda q: out(ok=False, text="알 수 없는 기능"))(q)
