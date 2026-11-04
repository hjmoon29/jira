#!/usr/bin/env python3
"""catalog.py 의 조회 항목으로 bot.json(화면 정의)을 만든다. 항목을 고친 뒤 한 번 실행: python make_botjson.py"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import catalog as cat  # noqa: E402
import writes as wr  # noqa: E402

labels = list(cat.BY_LABEL)
ops = list(wr.BY_LABEL)
path_ops = [lb for lb, op in wr.BY_LABEL.items() if "{}" in op[2]]
uses = lambda key: [lb for lb, (_, need, _f) in cat.BY_LABEL.items() if key in need]
env = {"name": "env", "label": "환경", "type": "select", "options": ["개발", "검증", "운영"], "default": "개발",
       "help": "개발 = ims.iot.coway.dev / 검증 = ims.iot.coway.blue / 운영 = ims.iot.coway.com (환경마다 처음 한 번 로그인)"}
bot = {
    "name": "IoT IMS",
    "icon": "🛰️",
    "order": 15,
    "group": "기획 업무",
    "tagline": "IMS 조회 → 엑셀, 등록·수정·삭제",
    "description": "사내 IoT IMS(IoCare 통합관리시스템)의 메뉴 데이터를 조회해 표로 보여 주고 엑셀로 저장합니다. "
                   "제품별 앱 설정(기준정보·소모품·알림·자동화·살균·진단)을 한 번에 모아 보거나, 각 메뉴의 목록을 통째로 받을 수 있습니다. "
                   "'등록·수정·삭제' 탭은 미리보기로 보낼 요청을 확인한 뒤 'IMS에 반영' 을 눌러야 실제로 바뀝니다(기록: runs/ims/changes.jsonl). 처음과 IMS 세션이 끝났을 때 '로그인' 탭에서 직접 로그인해야 합니다(추가 인증).",
    "actions": [
        {
            "id": "query", "label": "조회", "button": "조회", "timeout": 600,
            "fields": [
                env,
                {"name": "item", "label": "조회 항목", "type": "select", "options": labels, "default": labels[0]},
                {"name": "model", "label": "제품(모델) — POP 코드나 제품명", "type": "text", "placeholder": "[02FAI] 아이콘 정수기 또는 02FAI",
                 "suggest_action": "models", "show_if": {"item": uses("model")},
                 "help": "목록은 한 번 조회한 뒤부터 자동완성됩니다"},
                {"name": "prod_type", "label": "제품 유형", "type": "select", "options": [""] + list(cat.PROD_TYPES),
                 "default": "", "show_if": {"item": uses("prod_type")}},
                {"name": "keyword", "label": "검색어", "type": "text", "show_if": {"item": uses("keyword")},
                 "placeholder": "운영 기기: 시리얼·MAC·주문번호 / POP 코드: 제품명 / 공통 코드: 그룹·코드명"},
                {"name": "start", "label": "시작일", "type": "date", "default": "month_start", "show_if": {"item": uses("start")}},
                {"name": "end", "label": "종료일", "type": "date", "default": "today", "show_if": {"item": uses("end")}},
            ],
        },
        {
            "id": "change_preview", "label": "등록·수정·삭제", "button": "미리보기", "timeout": 300,
            "extra": [{"action": "change_run", "button": "IMS에 반영",
                       "confirm": "선택한 환경의 IMS 데이터를 바로 바꿉니다(되돌리기는 IMS 화면에서 직접). 미리보기로 요청을 확인했나요?"}],
            "fields": [
                env,
                {"name": "op", "label": "작업", "type": "select", "options": ops, "default": ops[0]},
                {"name": "path_args", "label": "경로 값 (쉼표로 구분)", "type": "text", "show_if": {"op": path_ops},
                 "placeholder": "예: 12", "help": "삭제할 항목 번호 등 주소에 들어가는 값"},
                {"name": "query", "label": "쿼리 (이름=값, 한 줄에 하나)", "type": "textarea",
                 "placeholder": "faqSeq=12", "help": "삭제 대상 지정 등. 필요 없으면 비움"},
                {"name": "body", "label": "본문 (JSON)", "type": "textarea", "placeholder": "{ … }",
                 "help": "등록·수정 내용. 비우고 미리보기를 누르면 이 작업의 본문 틀(필드 이름)을 보여 줍니다"},
                {"name": "confirm_env", "label": "운영 확인 — 운영에 반영하려면 '운영' 입력", "type": "text",
                 "show_if": {"env": "운영"}},
            ],
        },
        {"id": "change_run", "label": "IMS에 반영", "hidden": True, "fields": []},
        {"id": "login", "label": "로그인", "button": "로그인 창 열기", "timeout": 330,
         "confirm": "IMS 로그인 창을 엽니다. 추가 인증까지 마치면 자동으로 닫힙니다.", "fields": [env]},
        {"id": "models", "label": "모델 목록", "hidden": True, "fields": []},
        {"id": "open_file", "label": "파일 열기", "hidden": True, "fields": []},
    ],
}
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.json"), "w", encoding="utf-8") as f:
    json.dump(bot, f, ensure_ascii=False, indent=2)
print(len(labels), "items")
