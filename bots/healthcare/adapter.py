#!/usr/bin/env python3
# 업무 봇 ↔ healthcare.py 연결. 입력: argv[1]=기능, stdin=화면 입력(JSON) / 출력: JSON
import datetime as dt
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import healthcare as hc  # noqa: E402

PORTAL = hc.WEEK + "{:%Y-%m-%d}"


def out(**kw):
    print(json.dumps(kw, ensure_ascii=False))
    sys.exit(0)


def _mine(mine):
    up = [m for m in mine if m["start"] >= dt.datetime.now()]
    return ["", "내 예약: " + ", ".join(hc.label(m) for m in up)] if up else []


def find(q):
    log = []
    try:
        with hc.Session() as s:
            free, mine, w = hc.scan(s, hc._sex(q), q.get("period", ""), log=log.append)
    except hc.BotError as e:
        out(ok=False, headline=f"❌ {e}", text="\n".join(log + [str(e)]), summary=str(e)[:120])
    ok = [c for c in free if not c["block"]]
    lines = log + _mine(mine) + [""]
    if ok:
        head = f"🟢 가장 빠른 예약 가능 시간 — {hc.label(ok[0])}"
        lines += [f"예약 가능한 시간 {len(ok)}개 (빠른 순)"] + [f"{i}. {hc.label(c)}" for i, c in enumerate(ok[:20], 1)]
        if len(ok) > 20:
            lines.append(f"… 외 {len(ok) - 20}개")
        lines += ["", "'예약하기' 탭에서 가장 빠른 시간 또는 원하는 일자·시간으로 바로 예약할 수 있습니다."]
    else:
        head = "⚪ 2주 안에 예약 가능한 시간이 없습니다"
    blocked = [c for c in free if c["block"]]
    if blocked:
        lines += ["", f"빈 시간이지만 신청 한도로 예약 불가 {len(blocked)}개 (예: {hc.label(blocked[0])} — {blocked[0]['block']})"]
    lines += ["", "포털 화면: " + PORTAL.format(dt.date.today())]
    out(ok=True, headline=head, text="\n".join(lines),
        summary=(f"가장 빠름 {hc.label(ok[0])}" if ok else "예약 가능 시간 없음"))


def book(q):
    log = []
    try:
        c, done = hc.book(q, log=log.append)
    except hc.BotError as e:
        out(ok=False, headline=f"❌ 예약하지 못했습니다 — {e}", text="\n".join(log + [str(e)]), summary=str(e)[:120])
    link = PORTAL.format(c["date"])
    if done:
        out(ok=True, headline=f"✅ 예약 완료 — {hc.label(c)}",
            text="\n".join([f"일시: {hc.label(c)}", "장소: 15층 헬스케어실", "",
                            "취소는 포털 화면에서 내 이름 아래 '취소'를 누르면 됩니다 (당일 취소 불가).", link, "", "실행 로그"] + log),
            summary=f"{hc.label(c)} 예약 완료")
    out(ok=False, headline=f"⚠️ 예약 확인 필요 — {hc.label(c)}",
        text="\n".join(["예약 신청은 보냈지만 화면에서 내 예약을 확인하지 못했습니다. 포털에서 확인해 주세요.", link, "", "실행 로그"] + log),
        summary=f"{hc.label(c)} 확인 필요")


def login(q):
    try:
        ok = hc.login()
    except hc.BotError as e:
        out(ok=False, text=str(e))
    out(ok=ok, text="로그인 완료. 이제 조회·예약을 쓸 수 있습니다. (포털 세션이 끝나면 다시 로그인해야 합니다)" if ok
        else "5분 안에 로그인하지 않아 창을 닫았습니다. 다시 시도해 주세요.",
        summary="로그인 완료" if ok else "로그인 시간 초과")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    q = json.loads(sys.stdin.read() or "{}")
    {"find": find, "book": book, "login": login}.get(action, lambda q: out(ok=False, text="알 수 없는 기능"))(q)
