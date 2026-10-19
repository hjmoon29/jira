# 봇 허브

여러 업무 봇을 한 화면에서 실행하는 로컬 프로그램. Mac·Windows가 회사 Git 저장소로 같은 코드·설정을 같이 쓰고 같이 고친다.
각 PC 안에서만 열린다(http://127.0.0.1:8765).

## 실행
- Mac: `봇허브 실행.command` 더블클릭 / Windows: `봇허브 실행.bat` 더블클릭
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

## 같이 쓰기 (동기화)
- 화면의 **🔄 봇 허브 동기화**: 상태 확인 → **업데이트 받기**(다른 PC 변경 받기) / **변경 올리기**(이 PC 변경 올리기)
- Jira 기본 하위 문서 목록·제품 등록(`bots/jira-tasks/config.json`)도 저장소에 들어 있어 두 PC가 같은 설정을 쓴다.
  설정을 바꾼 뒤 '변경 올리기' 를 해야 다른 PC에 반영된다.
- 작업 시작 전 '업데이트 받기', 끝나면 '변경 올리기' 습관. 충돌이 나면 Claude Code에 "봇 허브 git 충돌 해결해줘".

## 들어 있는 봇
| 폴더 | 봇 | 기능 |
|---|---|---|
| bots/jira-tasks | Jira 하위 문서 생성 | 명령으로 생성 / 기본 하위 문서 / 별도 하위 문서 / 설정(기본 문서 목록·제품 등록) / 담당자 찾기 |
| bots/daily-summary | 토큰 사용량 리포트 | 지금 사용량 / 날짜별 상세 / Mac+Windows 합산 기간 리포트 메일 |
| bots/sync | 봇 허브 동기화 | 상태 / 업데이트 받기 / 변경 올리기 |

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
       {"name": "q", "label": "입력", "type": "text|textarea|select|radio|checkbox|date",
        "required": true, "default": "", "placeholder": "", "help": "입력란 아래 안내",
        "options": ["select용"], "show_if": {"다른필드": "값"}, "suggest_action": "(선택) 자동완성 목록을 주는 숨은 기능 id"}
     ]}
  ]
}
```
date 기본값: `today`, `month_start`. `"hidden": true` 인 기능은 탭에 안 보이고 화면 내부에서만 호출.

**adapter.py** — 실행. `argv[1]` = 기능 id, 표준입력 = 입력값 JSON, 표준출력 = 결과 JSON:
```json
{"ok": true, "text": "출력 텍스트", "summary": "최근 실행 목록에 보일 한 줄",
 "view": "text | cards | jira_plan | jira_settings", "data": {}, "quiet": false}
```
- `cards`: `data = {"cards": [{"label","value","unit","sub","gauge"}], "note"}`
- `quiet: true` 면 실행 기록에 남기지 않음(자동완성 등).
- 작업 파일은 환경변수 `BOT_HUB_RUNS` 폴더(runs/, 저장소 제외)에 둔다. 실행 기록은 runs/history.jsonl.

## 보안
- 127.0.0.1 에만 열리고, 실행 요청은 화면을 열 때 발급한 키와 Host 확인을 거친다(다른 웹사이트가 몰래 호출 못 함).
- 토큰·메일 인증 정보는 저장소에 넣지 않는다(.gitignore). 각 PC의 `~/.config/` 아래에 둔다.
