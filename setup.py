#!/usr/bin/env python3
"""IoT업무봇 처음 설정 (Mac·Windows 공용). 저장소를 받은 뒤 한 번 실행:  python3 setup.py  (Windows: py -3 setup.py)

1. Python 버전 확인 (3.8 이상)
2. Jira 개인 액세스 토큰 저장 (~/.config/jira-bot/token — 저장소 밖, 이 PC 전용)
3. Claude Code 스킬 연결: 저장소 skills/<이름> → ~/.claude/skills/<이름>
   (Mac은 심볼릭 링크, Windows는 디렉터리 정션. 저장소를 pull 하면 스킬도 같이 최신이 된다)
"""
import getpass, os, platform, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
WIN = platform.system() == "Windows"


def step(msg):
    print("\n▶ " + msg)


def check_python():
    step(f"Python {platform.python_version()}")
    if sys.version_info < (3, 8):
        sys.exit("  Python 3.8 이상이 필요합니다. https://www.python.org 에서 설치하세요.")
    print("  OK")


def token():
    path = os.path.join(HOME, ".config", "jira-bot", "token")
    step(f"Jira 토큰: {path}")
    if os.path.isfile(path) and open(path).read().strip():
        print("  이미 있음 (바꾸려면 파일을 지우고 다시 실행)")
        return
    print("  jira.coway.com > 프로필 > 개인 액세스 토큰 > 토큰 만들기 에서 발급한 값을 붙여 넣으세요 (화면에 안 보임).")
    t = getpass.getpass("  토큰: ").strip()
    if not t:
        print("  건너뜀 — Jira 봇을 쓰기 전에 다시 실행하세요.")
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(t)
    if not WIN:
        os.chmod(path, 0o600)
    print("  저장함")


def is_link(p):
    if os.path.islink(p):
        return True
    if WIN and os.path.isdir(p):  # 정션은 islink 로 안 잡힘
        try:
            return os.path.realpath(p) != os.path.abspath(p)
        except OSError:
            return False
    return False


def link_skills():
    src_root = os.path.join(HERE, "skills")
    if not os.path.isdir(src_root):  # 설치용 zip에는 스킬이 없다 (저장소 연결 후 생김)
        return
    dst_root = os.path.join(HOME, ".claude", "skills")
    os.makedirs(dst_root, exist_ok=True)
    for name in sorted(os.listdir(src_root)):
        src, dst = os.path.join(src_root, name), os.path.join(dst_root, name)
        if not os.path.isdir(src):
            continue
        step(f"스킬 연결: {dst} → {src}")
        if is_link(dst):
            if os.path.realpath(dst) == os.path.realpath(src):
                print("  이미 연결됨")
                continue
            os.unlink(dst) if not WIN else os.rmdir(dst)
        elif os.path.exists(dst):
            bak = os.path.join(HOME, ".claude", "backups", f"skill-{name}-{time.strftime('%Y%m%d%H%M%S')}")
            os.makedirs(os.path.dirname(bak), exist_ok=True)
            shutil.move(dst, bak)
            print(f"  기존 폴더는 백업으로 옮김: {bak}")
        if WIN:
            r = subprocess.run(["cmd", "/c", "mklink", "/J", dst, src], capture_output=True, text=True)
            if r.returncode != 0:
                shutil.copytree(src, dst)
                print("  정션을 만들지 못해 복사했습니다 (저장소를 pull 한 뒤 setup.py 를 다시 실행하면 갱신)")
                continue
        else:
            os.symlink(src, dst)
        print("  연결함")


if __name__ == "__main__":
    check_python()
    token()
    link_skills()
    print("\n완료. 실행: " + ("'IoT업무봇 실행.bat' 더블클릭" if WIN else "'IoT업무봇 실행.command' 더블클릭") + " 또는 python3 hub.py")
