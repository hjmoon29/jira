# 업무 봇

여러 업무 봇을 한 화면에서 실행하는 로컬 프로그램. Mac·Windows가 회사 Git 저장소로 같은 코드·설정을 같이 쓰고 같이 고친다.
각 PC 안에서만 열린다(http://127.0.0.1:8765).

## 실행
- Mac: `업무봇 실행.command` 더블클릭 / Windows: `업무봇 실행.bat` 더블클릭
- 또는 `python3 hub.py` (Windows: `py -3 hub.py`). 터미널(명령 창)을 닫으면 종료.

## 처음 설정 (PC마다 한 번)
1. 준비물: Python 3.8+ (Windows: python.org 설치 시 "Add to PATH" 체크), Git (Windows: Git for Windows)
2. 저장소 받기 — 위치는 두 PC 모두 `Downloads/bot-hub` 로 맞춘다 (Claude Code 스킬 안내가 이 경로를 씀)
   - Mac: `cd ~/Downloads && git clone <회사 Git 저장소 주소> bot-hub`
   - Windows: `cd %USERPROFILE%\Downloads && git clone <회사 Git 저장소 주소> bot-hub`
3. `python3 setup.py` (Windows: `py -3 setup.py`)
   - Jira 개인 액세스 토큰 입력 → `~/.config/jira-bot/token` (저장소에 안 들어감, PC마다 따로)
   - `skills/` 의 Claude Code 스킬을 `~/.claude/skills/` 에 연결 → 그 PC의 Claude Code에서 `/jira-tasks` 사용 가능
4. 토큰 사용량 리포트 봇은 각 PC의 데일리 써머리 폴더를 쓴다
   (Mac `~/claude-daily-summary`, Windows `%USERPROFILE%\Downloads\claude-daily-summary`, 다르면 환경변수 `CLAUDE_DAILY_DIR`).
   다른 PC 기록은 Google Drive `claude-daily/<PC이름>` 에서 받는다(그 폴더의 drive_upload.py 인증 필요).

5. 기획 사양 정리·QA 대응 봇은 위키·Figma·Google Drive를 직접 읽는다. 인증 정보는 `~/.config/work-bot/credentials.json`
   (`{"wiki_token": "위키 개인 액세스 토큰", "figma_token": "figd_…", "gdrive_credentials": "구글 인증 파일 경로", "gdrive_oauth": "OAuth 클라이언트 파일 경로"}`),
   없으면 Claude Code MCP 설정(`~/.claude/mcp.json`, `~/.claude.json`)의 confluence·figma·gdrive 값을 찾아 쓴다.
   글쓰기는 그 PC의 Claude Code(`claude -p`, 1M 컨텍스트 모델)를 쓰고, 메일은 데일리 써머리의 Gmail 설정(`~/.config/claude-daily/gmail.json`)을 쓴다.
6. QA 대응 예약: 처음 예약을 저장할 때 10분마다 `bots/qa/scheduler.py` 를 실행하는 OS 작업을 자동 등록한다
   (Windows 작업 스케줄러 `WorkBotQA`, Mac launchd `com.workbot.qa`). 예약 목록·실행 기록은 PC마다 `runs/` 에 따로 있다.

## 같이 쓰기 (동기화)
- 화면의 **🔄 업무 봇 동기화**: 상태 확인 → **업데이트 받기**(다른 PC 변경 받기) / **변경 올리기**(이 PC 변경 올리기)
- Jira 기본 하위 문서 목록·제품 등록(`bots/jira-tasks/config.json`)도 저장소에 들어 있어 두 PC가 같은 설정을 쓴다.
  설정을 바꾼 뒤 '변경 올리기' 를 해야 다른 PC에 반영된다.
- 작업 시작 전 '업데이트 받기', 끝나면 '변경 올리기' 습관. 충돌이 나면 Claude Code에 "업무 봇 git 충돌 해결해줘".

## 들어 있는 봇
| 폴더 | 봇 | 기능 |
|---|---|---|
| bots/jira-tasks | Jira 하위 문서 생성 | 직접 입력(요약·이슈 유형·보고자·담당자·우선순위·구성 요소·레이블·기한·설명) / 명령으로 생성 / 기본 하위 문서 / 별도 하위 문서 / 설정(기본 문서 목록·제품 등록) / 담당자 찾기 |
| bots/spec-doc | 기획 사양 정리 (wiki) | 초안 만들기(위키·Drive·Figma → Claude가 [기획 사양 정리] 형식으로 작성) / 초안 목록·미리보기·위키 게시 |
| bots/qa | QA 대응 | Jira 이슈 답변 / 문의사항 답변 (답변 초안 → 엑셀 → 메일, Jira·문서에는 안 씀) / 예약 발송 |
| bots/meeting-room | 회의실 예약 | 회의실 잡기(일자·시간대·인원 → gapps 빈 회의실을 Google 캘린더 일정으로 예약) / 빈 회의실 보기 / 로그인. Playwright 필요(`pip install playwright`), 브라우저 로그인 상태는 `~/.playwright-edge-profile` |
| bots/healthcare | 헬스케어 예약 | 가장 빠른 시간 찾기 / 예약하기(키퍼·시간대·희망 일자·시간) / 로그인. 포털 로그인에 NETSPASS 추가 인증이 있어 '로그인' 탭에서 직접 로그인하면 세션 쿠키를 `~/.config/work-bot/healthcare_cookies.json` 에 저장해 씀(만료 시 재로그인) |
| bots/daily-summary | 토큰 사용량 리포트 | 지금 사용량 / 날짜별 상세 / Mac+Windows 합산 기간 리포트 메일 |
| bots/sync | 업무 봇 동기화 | 상태 / 업데이트 받기 / 변경 올리기 |

## 봇 추가하는 법
`bots/<봇id>/` 폴더를 만들고 두 파일을 넣은 뒤 화면을 새로 고친다. 화면 코드는 고칠 필요 없음.
Claude Code에서도 쓰게 하려면 `skills/<이름>/SKILL.md` 를 추가하고 각 PC에서 `setup.py` 를 다시 실행.
Mac·Windows 둘 다에서 돌도록: 경로는 `os.path.expanduser("~")`·`os.path.join`, 파일은 `encoding="utf-8"`, 하위 프로세스는 `sys.executable`.

**bot.json** — 화면 정의
```json
{
  "name": "봇 이름", "icon": "🤖", "order": 30, "description": "설명",
  "actions": [
    {"id": "run", "label": "탭 이름", "button": "실행", "timeout": 120,
     "confirm": "(선택) 실행 전 확인 문구 — 메일 발송·생성처럼 되돌리기 어려운 기능에",
     "auto": false,
     "fields": [
       {"name": "q", "label": "입력", "type": "text|textarea|select|radio|checkbox|date|multi(여러 개 선택)",
        "required": true, "default": "", "placeholder": "", "help": "입력란 아래 안내",
        "options": ["select용"], "show_if": {"다른필드": "값"}, "suggest_action": "(선택) 자동완성 목록을 주는 숨은 기능 id",
        "fill_action": "(선택) 이 칸을 입력하면 data.values 로 다른 칸을 채우는 숨은 기능 id (직접 고친 칸은 유지)"}
     ],
     "init_action": "(선택) 탭을 처음 열 때 data.values 로 빈 칸을 채우는 숨은 기능 id",
     "options_action": "(선택) type=multi 칸의 선택지를 data={칸이름: [선택지]} 로 주는 숨은 기능 id", "options_on": ["바뀌면 선택지를 다시 불러올 칸"]}
  ]
}
```
`"group": "묶음 이름"` 을 주면 왼쪽 목록에서 같은 묶음끼리 모인다(지금: 기획 업무 / 사내 예약 / 리포트). `"system"` 은 맨 아래 따로(동기화). `"tagline"` 은 왼쪽 목록에 이름 아래 한 줄로 보이는 짧은 설명.
date 기본값: `today`, `month_start`. `"hidden": true` 인 기능은 탭에 안 보이고 화면 내부에서만 호출.

**adapter.py** — 실행. `argv[1]` = 기능 id, 표준입력 = 입력값 JSON, 표준출력 = 결과 JSON:
```json
{"ok": true, "headline": "(선택) 결과 맨 위에 크게 보일 한 줄", "text": "출력 텍스트", "summary": "최근 실행 목록에 보일 한 줄",
 "view": "text | cards | jira_plan | jira_settings", "data": {}, "quiet": false}
```
- `cards`: `data = {"cards": [{"label","value","unit","sub","gauge"}], "note"}`
- `quiet: true` 면 실행 기록에 남기지 않음(자동완성 등).
- 작업 파일은 환경변수 `BOT_HUB_RUNS` 폴더(runs/, 저장소 제외)에 둔다. 실행 기록은 runs/history.jsonl.

## 보안
- 127.0.0.1 에만 열리고, 실행 요청은 화면을 열 때 발급한 키와 Host 확인을 거친다(다른 웹사이트가 몰래 호출 못 함).
- 토큰·메일 인증 정보는 저장소에 넣지 않는다(.gitignore). 각 PC의 `~/.config/` 아래에 둔다.
