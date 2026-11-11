#!/usr/bin/env python3
"""Figma UX 기획봇: 요청사항과 참고 IoCare+ 화면을 받아 UX 설계안을 만들고, 확인 후 Figma에 화면을 그린다.

  plan     참고 화면(Figma REST 글자) 수집 → Claude(도구 없음)가 설계안(JSON: 화면 목록·구성·정책) 작성 → runs/figma-ux/ 저장
  revise   설계안 + 수정 요청 → 새 버전
  draw     Claude(헤드리스, Figma MCP use_figma)가 대상 파일의 '작업안(UX봇)' 페이지에 섹션을 만들어 화면을 그림
           기존 페이지·노드는 수정하지 않는다(복제해서 가져오는 것만 허용).

자료 수집·Claude 실행은 기획 사양 정리 봇(spec-doc/spec_doc.py)의 함수를 그대로 쓴다.
"""
import datetime, json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "spec-doc"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # bots/ (hub_progress)
from hub_progress import progress, stage, waiting  # noqa: E402  화면 진행률 게이지
import spec_doc as sd  # noqa: E402

BotError = sd.BotError
DIR = os.path.join(sd.RUNS, "figma-ux")
PAGE = "작업안(UX봇)"
FIGMA_TOOLS = ["mcp__figma__create_session", "mcp__figma__get_metadata", "mcp__figma__get_design_context",
               "mcp__figma__get_screenshot", "mcp__figma__search_design_system", "mcp__figma__use_figma"]
PLAN_SHAPE = """{
  "title": "작업 제목",
  "summary": "무엇을 왜 바꾸는지 3~5줄",
  "assumptions": ["요청에 없어서 가정한 것"],
  "questions": ["기획자가 정해야 할 것 (없으면 빈 배열)"],
  "flow": "화면 흐름 한 줄 (예: 01 디바이스 탭 → 02 설정 → 03 완료 팝업)",
  "screens": [
    {
      "no": "01",
      "name": "화면 이름",
      "type": "화면 | 팝업 | 바텀시트 | 토스트",
      "purpose": "이 화면의 목적 한 줄",
      "base": {"node_id": "복제해서 고칠 참고 프레임 node-id (예: 17002:61, 자료 링크의 node-id 를 : 로) — 비슷한 화면이 없으면 빈 문자열",
               "file_key": "그 프레임이 있는 파일 키", "why": "이 프레임을 고른 이유"},
      "layout": ["위에서 아래 순서로 구성 요소와 실제 문구 (예: '헤더: 뒤로가기 · 제목 \\"수면 리포트\\"')"],
      "changes": ["참고 프레임에서 바꿀 점 (base 가 있을 때)"],
      "states": ["상태별 차이 (예: 미연결 시 버튼 비활성) — 없으면 빈 배열"],
      "policy": ["화면 옆 디스크립션에 적을 정책 문구 (번호 순서대로)"]
    }
  ]
}"""


def lines(s):
    return [x.strip() for x in s if x.strip()] if isinstance(s, list) else sd.lines(s)


def target_key(q):
    urls = lines(q.get("figma"))
    if not urls:
        raise BotError("참고할 IoCare+ Figma 화면 주소를 1개 이상 입력하세요 (첫 주소의 파일에 그립니다)")
    return sd.figma_ref(urls[0])[0]


def collect(q, log):
    srcs = []
    figs, wikis = lines(q.get("figma")), lines(q.get("wiki"))
    n = max(len(figs) + len(wikis), 1)
    for i, u in enumerate(figs):
        progress(3 + 32 * i / n, f"Figma 읽는 중 ({i + 1}/{n})")
        log(f"Figma 읽는 중: {u}")
        srcs.append(sd.figma_source(u))
    for i, u in enumerate(wikis, len(figs)):
        progress(3 + 32 * i / n, f"위키 읽는 중 ({i + 1}/{n})")
        log(f"위키 읽는 중: {u}")
        srcs.append(sd.wiki_source(sd.wiki_page(sd.page_id(u)), "참고"))
    return srcs


def src_text(srcs):
    return "".join(f"\n\n===== 자료 {i}: {s['head']}\n{s['text']}" for i, s in enumerate(srcs, 1))


def plan_prompt(q, srcs, prev=None, feedback=None):
    p = [
        "당신은 코웨이 IoT기획팀의 IoCare+ 앱 UX 기획자입니다. 아래 요청사항에 맞는 화면 설계안을 만듭니다.",
        "참고 자료는 현재 IoCare+ Figma 화면설계서에서 뽑은 글자입니다(## 제목 옆 괄호가 그 프레임 링크, node-id 포함).",
        "규칙:",
        "- 기존 IoCare+ 화면의 구조·용어·문구 톤(존댓말, 짧은 버튼명)·디스크립션 작성 방식을 그대로 따릅니다.",
        "- 화면마다 가장 비슷한 기존 프레임을 base 로 고릅니다. 반드시 자료에 실제로 있는 node-id 만 씁니다(지어내지 말 것).",
        "- 화면 수는 요청에 필요한 만큼만(보통 2~8개). 팝업·토스트도 별도 화면으로 적습니다.",
        "- 자료에 없는 정책은 assumptions 에 가정으로 적고, 기획자가 정해야 할 것은 questions 에 적습니다.",
        "- 출력은 아래 형식의 JSON 하나만. 설명·코드블록 표시 없이 JSON 만 출력합니다.",
        PLAN_SHAPE,
        f"\n## 작업 제목\n{q.get('title') or ''}",
        f"\n## 요청사항\n{q.get('request') or ''}",
    ]
    if q.get("notes"):
        p.append(f"\n## 추가 요청\n{q['notes']}")
    if prev:
        p.append("\n## 이전 설계안 (이것을 고쳐서 새 버전을 만듭니다. 수정 요청에 없는 부분은 그대로 둡니다)\n"
                 + json.dumps(prev, ensure_ascii=False, indent=1))
        p.append(f"\n## 수정 요청\n{feedback}")
    p.append("\n## 참고 자료" + src_text(srcs))
    return "\n".join(p)


def check_bases(plan, srcs_txt):
    """자료에 없는 node-id(지어낸 값)는 비워서 그리기 단계가 엉뚱한 노드를 복제하지 않게 한다"""
    for s in plan["screens"]:
        b = s.get("base") or {}
        nid = (b.get("node_id") or "").strip()
        if nid and f"node-id={nid.replace(':', '-')})" not in srcs_txt:
            s["base"] = {"node_id": "", "file_key": "", "why": f"(자료에 없는 프레임 {nid} 라서 빼고 새로 조립)"}
    return plan


def parse_plan(text):
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    i, j = t.find("{"), t.rfind("}")
    try:
        plan = json.loads(t[i:j + 1])
    except ValueError as e:
        raise BotError(f"설계안 형식 오류(JSON): {e}\n{text[:600]}")
    if not plan.get("screens"):
        raise BotError("설계안에 화면이 없습니다:\n" + text[:600])
    return plan


def path(name):
    if not re.fullmatch(r"ux-[\w-]+", name or ""):
        raise BotError("잘못된 설계안 이름")
    return os.path.join(DIR, name + ".json")


def save(name, doc):
    os.makedirs(DIR, exist_ok=True)
    tmp = path(name) + ".tmp"
    json.dump(doc, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, path(name))


def load(name):
    try:
        return json.load(open(path(name), encoding="utf-8"))
    except OSError:
        raise BotError(f"설계안을 찾지 못했습니다: {name}")


def plan(q, log=print):
    if not (q.get("title") or "").strip() or not (q.get("request") or "").strip():
        raise BotError("작업 제목과 요청사항을 입력하세요")
    key = target_key(q)
    srcs = collect(q, log)
    name = "ux-" + time.strftime("%Y%m%d-%H%M%S")
    os.makedirs(DIR, exist_ok=True)
    open(os.path.join(DIR, name + ".sources.txt"), "w", encoding="utf-8").write(src_text(srcs))
    prompt = plan_prompt(q, srcs)
    log(f"Claude 설계 중… (자료 {len(srcs)}개, {len(prompt):,}자)")
    t0 = time.time()
    r = waiting(35, 97, f"Claude가 설계안 작성 중 (자료 {len(srcs)}개)",
                lambda: sd.run_claude(prompt, (q.get("model") or "").strip() or None, f"IoT업무봇: Figma UX 설계안 ({q['title'].strip()})"), past_sec("plan", 240))
    p = check_bases(parse_plan(r.get("result") or ""), src_text(srcs))
    doc = {"name": name, "title": q["title"].strip(), "request": q["request"].strip(), "notes": q.get("notes") or "",
           "figma": lines(q.get("figma")), "wiki": lines(q.get("wiki")), "target": key, "created": time.strftime("%Y-%m-%d %H:%M"),
           "versions": [{"v": 1, "at": time.strftime("%Y-%m-%d %H:%M"), "feedback": "", "sec": round(time.time() - t0),
                         "cost": r.get("total_cost_usd"), "plan": p}],
           "sources": [s["head"].split("\n")[0] for s in srcs], "draws": []}
    save(name, doc)
    return doc


def revise(q, log=print):
    doc = load(q.get("name"))
    fb = (q.get("feedback") or "").strip()
    if not fb:
        raise BotError("수정 요청을 입력하세요")
    try:
        srcs_txt = open(os.path.join(DIR, doc["name"] + ".sources.txt"), encoding="utf-8").read()
    except OSError:
        srcs_txt = src_text(collect(doc, log))
    prev = doc["versions"][-1]["plan"]
    prompt = plan_prompt(doc, [], prev, fb) + srcs_txt
    log(f"Claude 수정 중… (v{len(doc['versions']) + 1})")
    t0 = time.time()
    r = waiting(5, 97, f"Claude가 설계안 v{len(doc['versions']) + 1} 수정 중",
                lambda: sd.run_claude(prompt, None, f"IoT업무봇: Figma UX 설계안 수정 ({doc['title']})"), past_sec("plan", 240))
    doc["versions"].append({"v": len(doc["versions"]) + 1, "at": time.strftime("%Y-%m-%d %H:%M"), "feedback": fb,
                            "sec": round(time.time() - t0), "cost": r.get("total_cost_usd"), "plan": check_bases(parse_plan(r.get("result") or ""), srcs_txt)})
    save(doc["name"], doc)
    return doc


def draw_prompt(doc, ver):
    p = ver["plan"]
    label = f"[UX봇] {doc['title']} v{ver['v']} · {datetime.date.today().isoformat()}"
    return "\n".join([
        "당신은 코웨이 IoCare+ 앱 UX 기획자입니다. 아래 설계안대로 Figma 파일에 기획 화면을 그립니다.",
        "시작 전에 ReadMcpResourceTool 로 server 'figma', uri 'skill://figma/figma-use/SKILL.md' 를 읽고 그 지침을 지킵니다.",
        f"대상 파일 키: {doc['target']} — 먼저 mcp__figma__create_session 을 이 키로 호출하고, 이후 모든 Figma 도구에 sessionId 를 넘깁니다.",
        "",
        "## 반드시 지킬 것 (팀 공용 원본 파일입니다)",
        f"- 그리는 곳은 '{PAGE}' 페이지 하나뿐입니다. 없으면 맨 뒤에 새로 만듭니다.",
        "- 다른 페이지의 노드는 절대 수정·이동·삭제하지 않습니다. 참고 프레임은 clone() 한 사본만 이 페이지로 옮겨 고칩니다.",
        "  (clone() 은 원본과 같은 부모에 생기므로, 만든 즉시 이 페이지의 섹션으로 appendChild 합니다.)",
        "- 컴포넌트 원본(main component)은 수정하지 않습니다. 인스턴스 문구만 바꾸거나 필요하면 detach 합니다.",
        f"- 이 페이지에 섹션 '{label}' 을 만들고 모든 결과를 그 안에 둡니다. 페이지에 이미 다른 섹션이 있으면 그 아래(겹치지 않게)에 둡니다.",
        "- 쓰기 권한이 없다는 오류가 나면 더 시도하지 말고 바로 결과 JSON 의 error 에 그 내용을 적고 끝냅니다.",
        "",
        "## 그리는 방법",
        "- 화면은 왼쪽→오른쪽으로 설계안 순서대로, 화면 사이 간격은 기존 파일의 배치 간격을 따릅니다. 프레임 이름은 '번호_화면이름'.",
        "- base 가 있으면: 그 프레임을 clone 해서 changes·layout 대로 문구와 요소를 고칩니다(글꼴은 loadFontAsync 후 수정).",
        "- base 가 없으면: 같은 파일의 비슷한 화면에서 헤더·버튼·카드 등 요소를 복제해 조립하고, 색·글꼴·여백은 기존 화면 값을 씁니다.",
        "- 각 화면 오른쪽에 기존 화면설계서와 같은 모양의 디스크립션(번호 + 정책 문구)을 policy 대로 붙이고, 화면 위 해당 위치에 번호 표시를 둡니다.",
        "  기존 파일에 디스크립션·번호 컴포넌트가 있으면 그것을 씁니다.",
        "- 섹션 맨 위에 작업 제목·요약·화면 흐름·가정·확인 필요 사항을 적은 텍스트 상자를 둡니다.",
        "- 한 번의 use_figma 호출에서 너무 많이 하지 말고 화면 1~2개씩 나눠서 그립니다. 끝나면 get_screenshot 으로 섹션을 한 번 확인하고 겹침·잘린 글자를 고칩니다.",
        "",
        "## 마지막 출력",
        '끝나면 다른 말 없이 JSON 하나만 출력합니다: {"page_id": "...", "section_id": "...", "screens": [{"no": "01", "node_id": "..."}], "notes": ["설계안과 다르게 그린 점·확인할 점"], "error": ""}',
        "",
        f"## 작업 제목\n{doc['title']}\n\n## 요청사항\n{doc['request']}",
        "\n## 설계안 (v%d)\n" % ver["v"] + json.dumps(p, ensure_ascii=False, indent=1),
    ])


def draw(q, log=print):
    doc = load(q.get("name"))
    ver = doc["versions"][-1]
    log(f"Figma 그리는 중… ('{PAGE}' 페이지, 설계안 v{ver['v']}, 화면 {len(ver['plan']['screens'])}개)")
    cmd = [sd.claude_bin(), "-p", "--output-format", "json", "--model", (q.get("model") or "").strip() or "opus",
           "--tools", "ReadMcpResourceTool", "--allowedTools", " ".join(["ReadMcpResourceTool"] + FIGMA_TOOLS)]
    env = dict(os.environ, CLAUDE_CODE_MAX_OUTPUT_TOKENS="32000")
    t0 = time.time()
    n_scr = len(ver["plan"]["screens"])
    p = waiting(3, 97, f"Claude가 Figma에 그리는 중 (화면 {n_scr}개)",
                lambda: subprocess.run(cmd, input=draw_prompt(doc, ver), capture_output=True, text=True, encoding="utf-8", timeout=3300, env=env),
                past_sec("draw", 120 * n_scr, per=n_scr))
    try:
        r = json.loads(p.stdout)
    except ValueError:
        raise BotError("Claude 실행 오류: " + (p.stderr or p.stdout)[-800:])
    sd.label_session(r.get("session_id"), f"IoT업무봇: Figma UX 그리기 ({doc['title']})")
    text = r.get("result") or ""
    try:
        res = json.loads(text[text.find("{"): text.rfind("}") + 1])
    except ValueError:
        res = {"error": "" if not r.get("is_error") else text[:800], "notes": [text[-1500:]]}
    if r.get("is_error") and not res.get("error"):
        res["error"] = text[:800]
    nid = res.get("section_id") or res.get("page_id") or ""
    res.update(v=ver["v"], at=time.strftime("%Y-%m-%d %H:%M"), sec=round(time.time() - t0), cost=r.get("total_cost_usd"),
               url=f"https://www.figma.com/design/{doc['target']}/" + (f"?node-id={nid.replace(':', '-')}" if nid else ""))
    doc["draws"].append(res)
    save(doc["name"], doc)
    return doc, res


def past_sec(kind, default, per=None):
    """지난 설계안(plan)·그리기(draw)에 걸린 시간의 평균 — 진행률 게이지 예상 시간. per: 그리기는 화면 수에 비례"""
    xs = []
    for f in sorted(os.listdir(DIR) if os.path.isdir(DIR) else [], reverse=True)[:20]:
        if not f.endswith(".json"):
            continue
        try:
            d = json.load(open(os.path.join(DIR, f), encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if kind == "plan":
            xs += [v["sec"] for v in d.get("versions", []) if v.get("sec")]
        else:
            for x in d.get("draws", []):
                n = len((next((v for v in d.get("versions", []) if v["v"] == x.get("v")), {}).get("plan") or {}).get("screens", [])) or 1
                if x.get("sec") and not x.get("error"):
                    xs.append(x["sec"] / n)
    if not xs:
        return default
    avg = sum(xs[:8]) / len(xs[:8])
    return avg * per if per else avg


def list_plans():
    out = []
    for f in sorted(os.listdir(DIR) if os.path.isdir(DIR) else [], reverse=True):
        if f.endswith(".json") and f.startswith("ux-"):
            try:
                d = json.load(open(os.path.join(DIR, f), encoding="utf-8"))
            except (OSError, ValueError):
                continue
            dr = [x for x in d.get("draws", []) if not x.get("error")]
            out.append({"name": d["name"], "title": d["title"], "created": d["created"], "v": len(d["versions"]),
                        "screens": len(d["versions"][-1]["plan"]["screens"]), "drawn": dr[-1]["url"] if dr else ""})
    return out
