---
name: jira-tasks
description: 사내 Jira(jira.coway.com) 제품 프로젝트 문서(에픽)에 하위 문서(작업)를 만드는 봇. "○○ 프로젝트 문서에 기본 하위 문서들을 생성해줘", "○○에 '제목' 하위 문서 만들어줘", "기본 하위 문서 목록 바꿔줘", "제품 등록" 같은 요청이나 /jira-tasks 호출 시 사용.
---

# Jira 하위 문서 생성 봇

- 하위 문서 제목 = `<머리말> <제품명> <문서 제목>` (예: `[기획] 대만향 아이콘3 알림 정의`), 에픽에 Epic Link로 연결.
- 코드·설정은 IoT업무봇 저장소 `~/Downloads/bot-hub/bots/jira-tasks/` (Windows: `%USERPROFILE%\Downloads\bot-hub\bots\jira-tasks\`). Mac·Windows가 Git으로 같이 씀.
- 설정 `config.json`: `default_docs`(기본 하위 문서 목록·설명 양식, `{제품}` 치환, `regions`=국내향·해외향), `reference_epic`(값 설정 기준 에픽, 대만향 IOCARERENEW2023-2597), `custom_prefix`(별도 문서 머리말),
  `products`(제품명 → 에픽 키·기본 담당자). 화면에서는 IoT업무봇 > JIRA 자동 생성기 > 설정.
- 스크립트: `python3 -I ~/Downloads/bot-hub/bots/jira-tasks/jira_tasks.py` (Windows는 `py -3 -I %USERPROFILE%\Downloads\bot-hub\bots\jira-tasks\jira_tasks.py`)
- 토큰: PC마다 `~/.config/jira-bot/token` (저장소에 넣지 않음)
- 설정을 바꿨으면 IoT업무봇의 '동기화' 봇이나 git으로 커밋·푸시해서 다른 PC와 공유한다.

## 절차
1. 요청을 그대로 명령으로 넘긴다: `jira_tasks.py command "<사용자 문장>" --out <스크래치패드>/jira-plan.json`
   - 또는 `plan --product "제품명" --defaults --region 해외향` (향지 없으면 국내향, 명령에 '해외'가 있으면 해외향) / `plan --product "제품명" --doc "제목" --doc "제목2"` (`--epic`, `--assignee` 선택)
   - 에픽은 등록된 제품 → 그 제품명이 들어간 하위 문서들이 속한 에픽 순으로 자동으로 찾는다. 못 찾으면 에픽 키를 묻고, 제품 등록을 권한다.
2. 미리보기(만들 목록, 이미 있어 건너뛰는 목록)를 보여주고 **확인을 받은 뒤** `apply <계획 파일>`. 확인 없이 생성 금지.
3. 생성된 키와 에픽 링크 `https://jira.coway.com/browse/<에픽>` 를 알려준다.
4. 기본 하위 문서 목록 변경·제품 등록 요청은 config.json 을 고친다 (JSON 형식 유지, 제목 빈 항목 금지).

## 참고
- 레이블·구성요소: 등록 제품의 labels → 그 에픽 기존 하위 문서에서 가장 많이 쓴 값 → config 기본값.
- 담당자: --assignee → 등록 제품의 assignee → 에픽 담당자.
- 중복: 같은 제품명이 들어간 기존 문서와 제목 비교(머리말·띄어쓰기 무시, 아주 비슷하면 '비슷한 문서 있음'으로 건너뜀).
- apply 가 중간에 끊기면 같은 명령을 다시 실행하면 이어서 만든다. 401 = 토큰 만료 → 새 PAT 발급.
- 화면 '새 제품 하위 문서' 탭: 제품명+향지 → 체크 목록 → (값 설정 창: 기준 에픽의 같은 문서 값으로 담당자·구성요소·레이블·우선순위·기한) → 생성하기.
