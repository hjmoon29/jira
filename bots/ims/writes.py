"""IMS 등록·수정·삭제 작업 정의와 실행.

IMS 화면 코드(2026-10-22)에서 뽑은 쓰기 API 중, 기획·콘텐츠 관리 메뉴만 넣었다. 실제 기기에 영향을 주는 작업
(기기 제어, 펌웨어 업로드·OTA 배포 작업, 공장 출고, 인증서)과 권한·사용자·AI/RAG 관리, 파일 업로드(이미지)는 뺐다.
요청 본문(JSON)은 사용자가 넣는다. 본문 형식은 IMS 화면이 보내는 것과 같아야 하며, 이 봇에서 실행 시험은 하지 않았다.
"""
import datetime as dt
import json, os, re, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
# (메서드, base, 경로). 경로 끝 '/?' = 대상(예: xxxSeq)을 쿼리로 지정, '{}' = 경로에 값
OPS = [
    ("DELETE", "api", "/v1/admin/comCode"), ("POST", "api", "/v1/admin/comCode"), ("PUT", "api", "/v1/admin/comCode"),
    ("POST", "api", "/v1/admin/comCodeAuto"), ("POST", "api", "/v1/admin/comCodeGroup"), ("PUT", "api", "/v1/admin/comCodeGroup"),
    ("DELETE", "api", "/v1/admin/comCodeGroup/?"),
    ("POST", "api", "/v1/admin/notice"), ("PUT", "api", "/v1/admin/notice"), ("DELETE", "api", "/v1/admin/notice/{}"),
    ("PUT", "api", "/v1/device/codeMetadata"), ("DELETE", "api", "/v1/device/codeMetadata/meta"), ("PUT", "api", "/v1/device/codeMetadata/meta"),
    ("DELETE", "api", "/v1/device/popCode"), ("POST", "api", "/v1/device/popCode"), ("PUT", "api", "/v1/device/popCode"),
    ("POST", "api", "/v1/device/popCodeCopy"),
    ("POST", "api", "/v1/device/testProduct"), ("PUT", "api", "/v1/device/testProduct"), ("DELETE", "api", "/v1/device/testProduct/?"),
    ("POST", "api", "/v1/ota/aging/config"), ("PUT", "api", "/v1/ota/aging/config"), ("DELETE", "api", "/v1/ota/aging/config/?"),
    ("PUT", "api", "/v1/ota/aging/status"),
    ("DELETE", "api", "/v1/ota/userOtaReg"), ("POST", "api", "/v1/ota/userOtaReg"), ("PUT", "api", "/v1/ota/userOtaReg"),
    ("PUT", "api", "/v1/ota/userOtaReg/useYn"),
    ("DELETE", "api", "/v1/platform/project"), ("DELETE", "api", "/v1/platform/project/item"), ("POST", "api", "/v1/platform/project/item"),
    ("PUT", "api", "/v1/platform/project/item"),
    ("POST", "api", "/v1/protocol/api"), ("PUT", "api", "/v1/protocol/api"), ("DELETE", "api", "/v1/protocol/api/?"),
    ("POST", "api", "/v1/protocol/common/reflect"), ("POST", "api", "/v1/protocol/common/replicate"), ("POST", "api", "/v1/protocol/copyProtocol"),
    ("DELETE", "api", "/v1/protocol/func/batch"), ("POST", "api", "/v1/protocol/func/batch"), ("PUT", "api", "/v1/protocol/func/batch"),
    ("PUT", "api", "/v1/protocol/grid/modelProtocol"),
    ("POST", "api", "/v1/protocol/groupControl"), ("PUT", "api", "/v1/protocol/groupControl"), ("DELETE", "api", "/v1/protocol/groupControl/?"),
    ("PUT", "api", "/v1/protocol/groupControlValue"),
    ("POST", "api", "/v1/protocol/item"), ("PUT", "api", "/v1/protocol/item"), ("DELETE", "api", "/v1/protocol/item/?"),
    ("PUT", "api", "/v1/protocol/multiControlPopCode"), ("PUT", "api", "/v1/protocol/odmModelProtocol"),
    ("DELETE", "api", "/v1/protocol/odmProtocol"), ("POST", "api", "/v1/protocol/odmProtocol"), ("PUT", "api", "/v1/protocol/odmProtocol"),
    ("POST", "api", "/v1/protocol/odmProtocolItem"), ("PUT", "api", "/v1/protocol/odmProtocolItem"), ("DELETE", "api", "/v1/protocol/odmProtocolItem/{}"),
    ("POST", "api", "/v1/protocol/product"), ("POST", "api", "/v1/protocol/sdCategory"),
    ("DELETE", "api", "/v1/protocol/sdItemValue"), ("POST", "api", "/v1/protocol/sdItemValue"), ("PUT", "api", "/v1/protocol/sdItemValue"),
    ("DELETE", "api", "/v1/protocol/sdValue"),
    ("POST", "api", "/v1/protocol/tuyaErrorCode"), ("PUT", "api", "/v1/protocol/tuyaErrorCode"), ("DELETE", "api", "/v1/protocol/tuyaErrorCode/{}/{}"),
    ("DELETE", "api", "/v1/protocol/value"), ("POST", "api", "/v1/protocol/value"), ("PUT", "api", "/v1/protocol/value"),
    ("POST", "bo", "/v1/appMgmt/appSystemMaint"), ("PUT", "bo", "/v1/appMgmt/appSystemMaint"), ("DELETE", "bo", "/v1/appMgmt/appSystemMaint/?"),
    ("DELETE", "bo", "/v1/appMgmt/version"), ("POST", "bo", "/v1/appMgmt/version"), ("PUT", "bo", "/v1/appMgmt/version"),
    ("POST", "bo", "/v1/common/appCountry"), ("PUT", "bo", "/v1/common/appCountry"), ("DELETE", "bo", "/v1/common/appCountry/?"),
    ("POST", "bo", "/v1/common/appCountryProductRegion"), ("PUT", "bo", "/v1/common/appCountryProductRegion"),
    ("DELETE", "bo", "/v1/common/appCountryProductRegion/?"),
    ("POST", "bo", "/v1/common/appCustomerService"), ("PUT", "bo", "/v1/common/appCustomerService"), ("DELETE", "bo", "/v1/common/appCustomerService/?"),
    ("POST", "bo", "/v1/common/appProductCategory"), ("PUT", "bo", "/v1/common/appProductCategory"),
    ("DELETE", "bo", "/v1/common/appProductCategory/?"), ("PUT", "bo", "/v1/common/appProductCategory/sort"),
    ("POST", "bo", "/v1/contentMgmt/faq"), ("PUT", "bo", "/v1/contentMgmt/faq"), ("DELETE", "bo", "/v1/contentMgmt/faq/?"),
    ("POST", "bo", "/v1/contentMgmt/faq/prodReflection"), ("PUT", "bo", "/v1/contentMgmt/faq/sort"),
    ("POST", "bo", "/v1/contentMgmt/faqCategory"), ("PUT", "bo", "/v1/contentMgmt/faqCategory"), ("DELETE", "bo", "/v1/contentMgmt/faqCategory/?"),
    ("POST", "bo", "/v1/contentMgmt/faqCategory/prodReflection"), ("PUT", "bo", "/v1/contentMgmt/faqCategory/sort"),
    ("POST", "bo", "/v1/contentMgmt/notice"), ("PUT", "bo", "/v1/contentMgmt/notice"), ("DELETE", "bo", "/v1/contentMgmt/notice/?"),
    ("POST", "bo", "/v1/contentMgmt/pollution"), ("PUT", "bo", "/v1/contentMgmt/pollution"), ("DELETE", "bo", "/v1/contentMgmt/pollution/?"),
    ("POST", "bo", "/v1/contentMgmt/problemGuide"), ("PUT", "bo", "/v1/contentMgmt/problemGuide"), ("DELETE", "bo", "/v1/contentMgmt/problemGuide/?"),
    ("POST", "bo", "/v1/contentMgmt/supply"), ("PUT", "bo", "/v1/contentMgmt/supply"), ("DELETE", "bo", "/v1/contentMgmt/supply/?"),
    ("POST", "bo", "/v1/lifeStoryMgmt/keywordMgmt"), ("DELETE", "bo", "/v1/lifeStoryMgmt/keywordMgmt/?"),
    ("POST", "bo", "/v1/lifeStoryMgmt/lifeStory"), ("PUT", "bo", "/v1/lifeStoryMgmt/lifeStory"), ("DELETE", "bo", "/v1/lifeStoryMgmt/lifeStory/?"),
    ("POST", "bo", "/v1/lifeStoryMgmt/lifeStory/prodReflection"),
    ("DELETE", "bo", "/v1/productMgmt/appOdmDiagnosis"), ("POST", "bo", "/v1/productMgmt/appOdmDiagnosis"),
    ("PUT", "bo", "/v1/productMgmt/baseInfo"), ("PUT", "bo", "/v1/productMgmt/baseInfo/copy"), ("DELETE", "bo", "/v1/productMgmt/baseInfo/manual/?"),
    ("POST", "bo", "/v1/productMgmt/baseInfo/prodReflection"),
    ("PUT", "bo", "/v1/productMgmt/notiConfig"), ("PUT", "bo", "/v1/productMgmt/notiConfig/copy"),
    ("PUT", "bo", "/v1/productMgmt/problemGuide"), ("DELETE", "bo", "/v1/productMgmt/problemGuide/?"), ("PUT", "bo", "/v1/productMgmt/problemGuide/copy"),
    ("PUT", "bo", "/v1/productMgmt/routineConfig"), ("PUT", "bo", "/v1/productMgmt/routineConfig/copy"),
    ("PUT", "bo", "/v1/productMgmt/smartDiagnosis/collectInfo"), ("DELETE", "bo", "/v1/productMgmt/smartDiagnosis/content"),
    ("POST", "bo", "/v1/productMgmt/smartDiagnosis/content"), ("DELETE", "bo", "/v1/productMgmt/smartDiagnosis/item"),
    ("PUT", "bo", "/v1/productMgmt/smartDiagnosis/template"), ("POST", "bo", "/v1/productMgmt/smartDiagnosis/template/copy"),
    ("POST", "bo", "/v1/productMgmt/sterilizationConfig"), ("PUT", "bo", "/v1/productMgmt/sterilizationConfig/copy"),
    ("PUT", "bo", "/v1/productMgmt/supply"), ("DELETE", "bo", "/v1/productMgmt/supply/?"), ("PUT", "bo", "/v1/productMgmt/supply/copy"),
    ("POST", "bo", "/v1/routineMgmt/routine"), ("PUT", "bo", "/v1/routineMgmt/routine"), ("DELETE", "bo", "/v1/routineMgmt/routine/?"),
    ("PUT", "bo", "/v1/routineMgmt/routine/sort"),
    ("POST", "bo", "/v1/routineMgmt/routineItem"), ("PUT", "bo", "/v1/routineMgmt/routineItem"), ("DELETE", "bo", "/v1/routineMgmt/routineItem/?"),
]

# 경로 앞부분 → 메뉴 이름 (긴 것 먼저 맞춤)
MENU = {
    "/v1/admin/comCodeGroup": "Admin 관리 > 공통 코드 그룹", "/v1/admin/comCode": "Admin 관리 > 공통 코드",
    "/v1/admin/notice": "Admin 관리 > IMS 공지사항",
    "/v1/device/codeMetadata": "기기 관리 > POP 코드 메타", "/v1/device/popCode": "기기 관리 > POP 코드",
    "/v1/device/testProduct": "기기 관리 > 테스트 제품",
    "/v1/ota/aging": "OTA > OTA Aging", "/v1/ota/userOtaReg": "OTA > 사용자 OTA 등록",
    "/v1/platform/project": "플랫폼 관리 > 프로젝트",
    "/v1/protocol/api": "IoT 프로토콜 > 프로토콜 API", "/v1/protocol/common": "IoT 프로토콜 > 프로토콜 공통",
    "/v1/protocol/copyProtocol": "IoT 프로토콜 > 프로토콜 복사", "/v1/protocol/func": "IoT 프로토콜 > 기능",
    "/v1/protocol/grid": "IoT 프로토콜 > 프로토콜 통합 관리", "/v1/protocol/groupControl": "IoT 프로토콜 > 그룹 제어",
    "/v1/protocol/item": "IoT 프로토콜 > 프로토콜 항목", "/v1/protocol/multiControlPopCode": "IoT 프로토콜 > 멀티 제어 POP 코드",
    "/v1/protocol/odmModelProtocol": "IoT 프로토콜 > ODM 모델 프로토콜", "/v1/protocol/odmProtocolItem": "IoT 프로토콜 > 제품별 프로토콜(ODM)",
    "/v1/protocol/odmProtocol": "IoT 프로토콜 > 프로토콜 META(ODM)", "/v1/protocol/product": "IoT 프로토콜 > 제품별 프로토콜",
    "/v1/protocol/sdCategory": "IoT 프로토콜 > 스마트진단 카테고리", "/v1/protocol/sdItemValue": "IoT 프로토콜 > 스마트진단 항목 값",
    "/v1/protocol/sdValue": "IoT 프로토콜 > 스마트진단 값", "/v1/protocol/tuyaErrorCode": "IoT 프로토콜 > ODM 에러 코드",
    "/v1/protocol/value": "IoT 프로토콜 > 프로토콜 값",
    "/v1/appMgmt/appSystemMaint": "IoCareApp > 시스템 점검", "/v1/appMgmt/version": "IoCareApp > 앱 업데이트",
    "/v1/common/appCountryProductRegion": "IoCareApp > 제품 향지", "/v1/common/appCountry": "IoCareApp > 제품 출시국가",
    "/v1/common/appCustomerService": "IoCareApp > 고객센터", "/v1/common/appProductCategory": "IoCareApp > 제품 카테고리",
    "/v1/contentMgmt/faqCategory": "IoCareApp > FAQ 카테고리", "/v1/contentMgmt/faq": "IoCareApp > FAQ",
    "/v1/contentMgmt/notice": "IoCareApp > 공지사항", "/v1/contentMgmt/pollution": "IoCareApp > 오염원",
    "/v1/contentMgmt/problemGuide": "IoCareApp > 문제 해결 가이드", "/v1/contentMgmt/supply": "IoCareApp > 소모품(컨텐츠)",
    "/v1/lifeStoryMgmt/keywordMgmt": "IoCareApp > 라이프스토리 키워드", "/v1/lifeStoryMgmt/lifeStory": "IoCareApp > 라이프스토리",
    "/v1/productMgmt/appOdmDiagnosis": "IoCareApp > 제품 ODM 진단", "/v1/productMgmt/baseInfo": "IoCareApp > 제품 기준 정보",
    "/v1/productMgmt/notiConfig": "IoCareApp > 제품 알림 노출 설정", "/v1/productMgmt/problemGuide": "IoCareApp > 제품 문제 해결 컨텐츠",
    "/v1/productMgmt/routineConfig": "IoCareApp > 제품 자동화 설정", "/v1/productMgmt/smartDiagnosis": "IoCareApp > 제품 스마트 고장진단",
    "/v1/productMgmt/sterilizationConfig": "IoCareApp > 제품 정기 살균", "/v1/productMgmt/supply": "IoCareApp > 제품 소모품",
    "/v1/routineMgmt/routineItem": "IoCareApp > 자동화 항목", "/v1/routineMgmt/routine": "IoCareApp > 자동화 컨텐츠",
}
VERB = {"POST": "등록", "PUT": "수정", "DELETE": "삭제"}
SUFFIX = {"sort": "순서 변경", "copy": "복사", "prodReflection": "운영 반영", "reflect": "반영", "replicate": "복제", "useYn": "사용 여부 변경",
          "status": "상태 변경", "meta": "메타", "item": "항목", "batch": "일괄", "manual": "매뉴얼", "content": "내용", "template": "템플릿",
          "collectInfo": "수집 정보", "modelProtocol": "모델 프로토콜", "groupControlValue": "그룹 제어 값", "popCodeCopy": "복사",
          "comCodeAuto": "자동 등록", "Auto": "자동 등록", "Copy": "복사", "config": "", "Value": "값"}
# 운영(앱 사용자)에 바로 보이거나 여러 데이터를 한꺼번에 바꾸는 작업 — 실행 때 한 번 더 확인
HIGH = re.compile(r"prodReflection|/reflect|/replicate|/copy|Copy|/batch|/sort")


def _menu(path):
    for k in sorted(MENU, key=len, reverse=True):
        if path.startswith(k):
            return MENU[k], path[len(k):]
    return path, ""


def label(op):
    m, b, p = op
    menu, rest = _menu(p)
    parts = [x for x in rest.strip("/?").split("/") if x and x != "{}"]
    extra = " ".join(SUFFIX.get(x, x) for x in parts).strip()
    verb = VERB[m]
    if extra in ("순서 변경", "복사", "운영 반영", "반영", "복제", "사용 여부 변경", "상태 변경", "자동 등록"):
        verb = extra
        extra = ""
    return f"{menu} — {verb}{(' (' + extra + ')') if extra else ''}"


BY_LABEL = {}
for _op in OPS:
    _lb = label(_op)
    while _lb in BY_LABEL:  # 같은 이름이면 경로를 붙여 구분
        _lb += f" [{_op[2]}]"
    BY_LABEL[_lb] = _op

try:
    with open(os.path.join(HERE, "templates.json"), encoding="utf-8") as _f:
        TEMPLATES = json.load(_f)
except (OSError, ValueError):
    TEMPLATES = {}


class InputError(Exception):
    pass


def template(op):
    """등록·수정 본문 틀(조회 결과의 필드 이름, 값은 비움). 복사·순서 변경 같은 부가 작업은 틀이 없다."""
    m, b, p = op
    return TEMPLATES.get(b + p) if m in ("POST", "PUT") else None


def build(op, path_args, query, body):
    """보낼 요청(메서드, base, 경로, 쿼리, 본문)을 만든다. 형식 오류는 InputError."""
    m, b, p = op
    path = p
    n = path.count("{}")
    args = [a.strip() for a in re.split(r"[,\s]+", path_args or "") if a.strip()]
    if len(args) != n:
        raise InputError(f"이 작업은 경로 값 {n}개가 필요합니다 (쉼표로 구분)." if n else "이 작업은 경로 값이 필요 없습니다. 비워 주세요.")
    for a in args:
        path = path.replace("{}", urllib.parse.quote(a, safe=""), 1)
    params = {}
    for line in (query or "").replace("&", "\n").splitlines():
        if line.strip():
            if "=" not in line:
                raise InputError(f"쿼리는 이름=값 형식으로 한 줄에 하나씩 넣어 주세요: {line.strip()}")
            k, v = line.split("=", 1)
            params[k.strip()] = v.strip()
    if path.endswith("/?"):
        path = path[:-1]
        if not params:
            raise InputError("이 작업은 대상(예: faqSeq=12)을 쿼리로 지정해야 합니다.")
    data = None
    if (body or "").strip():
        try:
            data = json.loads(body)
        except ValueError as e:
            raise InputError(f"본문 JSON 형식 오류: {e}")
    elif m in ("POST", "PUT"):
        raise InputError("본문(JSON)을 넣어 주세요.")
    if m == "DELETE" and not params and data is None and not n:
        raise InputError("삭제 대상을 쿼리나 본문으로 지정해 주세요.")
    return m, b, path, params, data


def log(entry):
    runs = os.path.abspath(os.environ.get("BOT_HUB_RUNS") or os.path.join(HERE, "..", "..", "runs"))
    os.makedirs(os.path.join(runs, "ims"), exist_ok=True)
    entry["time"] = dt.datetime.now().isoformat(timespec="seconds")
    with open(os.path.join(runs, "ims", "changes.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
