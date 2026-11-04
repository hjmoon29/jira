#!/usr/bin/env python3
# IoT업무봇 ↔ meeting_room.py 연결. 입력: argv[1]=기능, stdin=화면 입력(JSON) / 출력: JSON
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import meeting_room as mr  # noqa: E402

STATUS = {"accepted": "예약 확정", "needsAction": "승인 대기 (회의실 관리자 확인 필요)", "tentative": "임시 예약",
          "declined": "회의실이 거절함 — 캘린더에서 일정을 지우고 다시 시도하세요", "unknown": "확인 중 — 잠시 뒤 gapps에서 확인하세요"}


def out(**kw):
    print(json.dumps(kw, ensure_ascii=False))
    sys.exit(0)


def book(q):
    log = []
    try:
        room, status, start, end, title = mr.book(q, log=log.append)
    except mr.BotError as e:
        out(ok=False, headline=f"❌ 예약하지 못했습니다 — {e}", text="\n".join(log + [str(e)]), summary=str(e)[:120])
    when = f"{start:%Y-%m-%d} ({'월화수목금토일'[start.weekday()]}) {start:%H:%M}~{end:%H:%M}"
    ok = status != "declined"
    head = ("✅ 예약 완료" if status == "accepted" else "⚠️ 예약 확인 필요" if ok else "❌ 예약 거절됨") + f" — {mr.label(room)} · {when}"
    text = "\n".join([f"회의실: {mr.label(room)}", f"일시: {when}", f"제목: {title}", f"상태: {STATUS.get(status, status)}",
                      "", "Google 캘린더에 일정으로 들어갔습니다. 취소는 캘린더에서 일정을 삭제하면 됩니다.",
                      f"https://calendar.google.com/calendar/r/day/{start:%Y/%m/%d}", "", "실행 로그"] + log)
    out(ok=ok, headline=head, text=text, summary=f"{mr.label(room)} {start:%m/%d %H:%M}~{end:%H:%M} {STATUS.get(status, status)[:6]}")


def free(q):
    log = []
    try:
        rooms, start, end = mr.free_rooms(q, log=log.append)
    except mr.BotError as e:
        out(ok=False, text="\n".join(log + [str(e)]))
    lines = log + [""] + [f"{i}. {mr.label(r)}" for i, r in enumerate(rooms, 1)]
    if rooms:
        lines += ["", "1번부터 순서대로 잡습니다 (내 층에서 가까운 층 → 인원에 맞는 크기 순)"]
    out(ok=True, text="\n".join(lines), summary=f"{start:%m/%d %H:%M}~{end:%H:%M} 빈 회의실 {len(rooms)}개")


def login(q):
    try:
        ok = mr.login()
    except mr.BotError as e:
        out(ok=False, text=str(e))
    out(ok=ok, text="로그인 완료. 이제 '회의실 잡기'를 쓸 수 있습니다." if ok else "5분 안에 로그인하지 않아 창을 닫았습니다. 다시 시도해 주세요.",
        summary="로그인 완료" if ok else "로그인 시간 초과")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    q = json.loads(sys.stdin.read() or "{}")
    {"book": book, "free": free, "login": login}.get(action, lambda q: out(ok=False, text="알 수 없는 기능"))(q)
