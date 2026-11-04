#!/usr/bin/env python3
# IoT업무봇 ↔ product_status.py 연결. 입력: argv[1]=기능, stdin=화면 입력(JSON) / 출력: JSON
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import product_status as ps  # noqa: E402


def out(**kw):
    print(json.dumps(kw, ensure_ascii=False))
    sys.exit(0)


def view(d, **kw):
    out(ok=True, view="product_status", data=ps.board(d), **kw)


def main(action, q):
    try:
        if action == "board":
            view(None, quiet=True)
        if action == "save_product":
            view(ps.save_product(q), quiet=True)
        if action == "delete_product":
            view(ps.delete_product(q.get("id")), quiet=True)
        if action == "move_product":
            view(ps.move_product(q.get("id"), int(q.get("step") or 1)), quiet=True)
        if action == "save_cell":
            view(ps.save_cell(q), quiet=True)
        if action == "import_jira":
            d, added, n = ps.import_jira()
            view(d, text=("새로 추가: \n" + "\n".join(added)) if added else f"내 담당 진행 중 에픽 {n}개 — 모두 이미 표에 있거나 삭제한 제품입니다.",
                 summary=f"Jira에서 제품 {len(added)}개 추가")
        if action == "jira_fill":
            d, n, log = ps.jira_fill()
            view(d, text="\n".join(log) or "바뀐 칸이 없습니다.", summary=f"Jira 하위 문서로 {n}칸 갱신")
        out(ok=False, text="알 수 없는 기능")
    except ps.BotError as e:
        out(ok=False, text=str(e))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "", json.loads(sys.stdin.read() or "{}"))
