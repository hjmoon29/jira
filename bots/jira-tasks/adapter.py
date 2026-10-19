#!/usr/bin/env python3
# 봇 허브 ↔ jira_tasks.py 연결 (/jira-tasks 스킬도 같은 스크립트 사용). 입력: argv[1]=기능, stdin=화면 입력(JSON) / 출력: JSON
import json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "jira_tasks.py")
RUNS = os.environ.get("BOT_HUB_RUNS") or os.path.join(HERE, "..", "..", "runs")
sys.path.insert(0, HERE)
import jira_tasks as jt  # noqa: E402  (설정 읽기·쓰기·검증은 같은 코드 사용)


def out(**kw):
    print(json.dumps(kw, ensure_ascii=False))
    sys.exit(0)


def call(*args):
    p = subprocess.run([sys.executable, "-I", "-X", "utf8", SCRIPT, "--json", *args], capture_output=True, text=True, encoding="utf-8",
                       env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"))
    try:
        return json.loads(p.stdout)
    except ValueError:
        return {"error": (p.stdout + p.stderr).strip() or "알 수 없는 오류"}


def new_plan_file():
    return os.path.join(RUNS, f"jira-plan-{time.strftime('%Y%m%d-%H%M%S')}.json")


def show_plan(r, auto=False):
    if "error" in r:
        out(ok=False, text=r["error"])
    p = r["plan"]
    n = sum(1 for x in p["tasks"] if not x.get("skip"))
    out(ok=True, view="jira_plan", data={"plan": p, "plan_file": os.path.basename(r["plan_file"]), "auto": auto},
        summary=f"{p['product']}: 새 문서 {n}개 / 전체 {len(p['tasks'])}개")


def opt(q, k):
    v = (q.get(k) or "").strip()
    return v or None


def command(q):
    show_plan(call("command", (q.get("text") or "").strip(), "--out", new_plan_file()), auto=True)


def plan_args(q):
    a = ["plan", "--product", (q.get("product") or "").strip(), "--out", new_plan_file()]
    for k in ("epic", "assignee"):
        if opt(q, k):
            a += ["--" + k, opt(q, k)]
    return a


def defaults(q):
    show_plan(call(*plan_args(q), "--defaults"))


def custom(q):
    titles = [t.strip() for t in (q.get("titles") or "").splitlines() if t.strip()]
    if not titles:
        out(ok=False, text="문서 제목을 한 줄에 하나씩 입력하세요")
    a = plan_args(q)
    for t in titles:
        a += ["--doc", t]
    show_plan(call(*a))


def apply(q):
    name = os.path.basename(q.get("plan_file") or "")
    path = os.path.join(RUNS, name)
    if not name.startswith("jira-plan-") or not os.path.isfile(path):
        out(ok=False, text="계획 파일이 없습니다. 미리보기부터 다시 하세요.")
    if q.get("plan"):  # 화면에서 체크 해제한 항목(skip) 반영
        json.dump(q["plan"], open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    r = call("apply", path)
    if "error" in r:
        out(ok=False, text=r["error"])
    p = r["plan"]
    made = [x for x in p["tasks"] if x.get("created")]
    out(ok=True, text=r["text"], view="jira_plan", data={"plan": p, "plan_file": name, "done": True},
        summary=f"{p['product']} → {p['epic']['key']} 에 {len(made)}개 생성")


def settings(q):
    out(ok=True, view="jira_settings", data=jt.load_config(), summary="설정 열기")


def save_settings(q):
    c = q.get("config")
    if not isinstance(c, dict) or not isinstance(c.get("default_docs"), list):
        out(ok=False, text="설정 형식 오류")
    try:
        jt.save_config(c)
    except jt.BotError as e:
        out(ok=False, text=str(e))
    out(ok=True, view="jira_settings", data=jt.load_config(), saved=True,
        summary=f"설정 저장: 기본 문서 {len(c['default_docs'])}개 · 제품 {len(c.get('products', []))}개")


def products(q):
    out(ok=True, data=[p["name"] for p in jt.load_config().get("products", [])], quiet=True)


def user(q):
    r = call("user", (q.get("query") or "").strip())
    if isinstance(r, dict) and "error" in r:
        out(ok=False, text=r["error"])
    out(ok=True, text="\n".join(f"{u['displayName']}  {u['name']}" for u in r) or "찾은 사용자가 없습니다")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    q = json.loads(sys.stdin.read() or "{}")
    fn = {"command": command, "defaults": defaults, "custom": custom, "apply": apply, "settings": settings,
          "save_settings": save_settings, "products": products, "user": user}.get(action)
    fn(q) if fn else out(ok=False, text="알 수 없는 기능")
