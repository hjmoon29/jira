"""IMS 조회 항목 정의. 각 항목 = (id, 묶음, 이름, 필요한 입력, 조회 함수).
조회 함수는 Session 과 입력값(q)을 받아 [(표 이름, 행 목록)] 을 돌려준다. 모두 읽기 전용 API 만 쓴다.
요청 모양은 IMS 화면이 실제로 보내는 요청을 기록해서 맞췄다(2026-10-22)."""
import datetime as dt

PROD_TYPES = {"청정기": "01", "정수기": "02", "비데": "03", "제습기": "04", "가습기": "05", "매트리스": "06",
              "BLEOnly 비데": "07", "모션베드": "08", "음식물처리기": "09", "에어컨": "10", "청소기": "11"}
# 제품 유형 → 앱 제품 카테고리(공통 코드 073)
APP_CATEGORY = {"01": "1", "02": "2", "03": "3", "04": "4", "05": "4", "06": "5", "07": "6", "08": "7", "09": "8",
                "10": "9", "11": "10"}
MAX_ROWS = 20000


class InputError(Exception):
    pass


def _content(r):
    return r.get("content") if isinstance(r, dict) else r


def _rows(c, key=None):
    if key and isinstance(c, dict):
        c = c.get(key)
    if isinstance(c, dict) and isinstance(c.get("content"), list):
        c = c["content"]
    if c is None:
        return []
    return c if isinstance(c, list) else [c]


def _iso_range(q):
    """화면처럼 한국 시간 하루 시작~끝을 UTC ISO 로."""
    s = dt.datetime.strptime(q["start"], "%Y-%m-%d") - dt.timedelta(hours=9)
    e = dt.datetime.strptime(q["end"], "%Y-%m-%d") + dt.timedelta(days=1, hours=-9, milliseconds=-1)
    f = lambda d: d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"
    return f(s), f(e)


def _ms_range(q):
    s = dt.datetime.strptime(q["start"], "%Y-%m-%d").replace(tzinfo=dt.timezone(dt.timedelta(hours=9)))
    e = dt.datetime.strptime(q["end"], "%Y-%m-%d").replace(tzinfo=dt.timezone(dt.timedelta(hours=9))) + dt.timedelta(days=1)
    return int(s.timestamp() * 1000), int(e.timestamp() * 1000) - 1


def _need(q, *keys):
    names = {"model": "제품(모델)", "prod_type": "제품 유형", "start": "시작일", "end": "종료일", "keyword": "검색어"}
    miss = [names[k] for k in keys if not q.get(k)]
    if miss:
        raise InputError("이 항목은 " + ", ".join(miss) + " 을(를) 넣어야 조회됩니다.")


def _paged_body(s, base, path, body, page_key="flat", first=0, size=500):
    """페이지 단위로 끝까지 모은다. page_key=flat: {page, pageSize} / info: {pageInfo:{page, limit}}"""
    out, page = [], first
    while True:
        b = dict(body)
        if page_key == "flat":
            b.update(page=page, pageSize=size)
        else:
            b["pageInfo"] = {"page": page, "limit": size}
        r = s.call("POST", base, path, b)
        rows = _rows(_content(r))
        out += rows
        c = r.get("content") if isinstance(r, dict) else None
        info = (r.get("pageInfo") if isinstance(r, dict) else None) or (c.get("pageInfo") if isinstance(c, dict) else None) or {}
        total = info.get("totalRecord") or info.get("total")
        if len(rows) < size or (total and len(out) >= total) or len(out) >= MAX_ROWS:
            return out
        page += 1


def _paged_get(s, base, path, params, size=500, key=None):
    out, page = [], 0  # page 는 0부터 (page=1 이면 pageSize 만큼 건너뜀)
    while True:
        r = s.call("GET", base, path, params={**params, "page": page, "pageSize": size})
        rows = _rows(_content(r), key)
        out += rows
        info = r.get("pageInfo") or {}
        if len(rows) < size or len(out) >= (info.get("totalRecord") or 10 ** 9) or len(out) >= MAX_ROWS:
            return out
        page += 1


def _get(s, base, path, key=None, **params):
    return _rows(_content(s.call("GET", base, path, params=params)), key)


def _post(s, base, path, body, key=None):
    return _rows(_content(s.call("POST", base, path, body)), key)


def _keyword_body(kw):
    kw = (kw or "").strip()
    k = kw.replace(":", "").replace("-", "")
    if len(k) == 12 and all(c in "0123456789abcdefABCDEF" for c in k):
        return {"macAddr": k.upper()}
    if kw.upper().startswith("ORD"):
        return {"orderNo": kw}
    return {"serial": kw} if kw else {}


# ---------- 항목들 ----------

def device_info(s, q):
    body = {"macAddr": "", "orderNo": "", "popCode": q.get("model", ""), "prodTypeCode": q.get("prod_type", ""),
            "returnYn": "", "serial": "", "protocolTypeCode": "", "productModel": ""}
    body.update(_keyword_body(q.get("keyword")))
    if not (q.get("keyword") or q.get("model") or q.get("prod_type")):
        raise InputError("운영 기기 조회는 검색어(시리얼·MAC·주문번호), 제품, 제품 유형 중 하나는 넣어야 합니다.")
    return [("운영 기기", _paged_body(s, "api", "/v1/device/info/list", body, size=200))]


def pop_codes(s, q):
    body = {"prodTypeCode": q.get("prod_type", ""), "devModelCode": q.get("model", ""), "svcCode": "", "productModel": "",
            "productName": q.get("keyword", ""), "useYn": "Y"}
    return [("POP 코드", _paged_body(s, "api", "/v1/device/popCode/list", body))]


def proto_grid(s, q):
    _need(q, "prod_type")
    c = _content(s.call("POST", "api", "/v1/protocol/grid/list", {"prodTypeCode": q["prod_type"]}))
    return [("프로토콜", _rows(c, "rows")), ("적용 모델", _rows(c, "productModelList"))]


def proto_api(s, q):
    _need(q, "prod_type")
    return [("프로토콜 API", _get(s, "api", "/v1/protocol/api", prodTypeCode=q["prod_type"], typeCode="M"))]


def ota_hist(s, q):
    _need(q, "start", "end")
    a, b = _iso_range(q)
    return [("OTA 이력", _paged_body(s, "api", "/v1/ota/hist/list", {"startDate": a, "endDate": b, "dateRange": [a, b]},
                                   page_key="info", size=100))]


def ota_aging(s, q):
    _need(q, "start", "end")
    a, b = _iso_range(q)
    return [("OTA Aging", _post(s, "api", "/v1/ota/aging/config/list", {"startDate": a, "endDate": b, "dateRange": [a, b]}))]


def pairing(s, q):
    _need(q, "start", "end")
    a, b = _iso_range(q)
    body = {"startDate": a, "endDate": b, "dateRange": [a, b]}
    return [("제품 페어링 통계", _post(s, "api", "/v1/dashboard/devicePairingStats", body)),
            ("IoCare 앱 페어링 실패", _post(s, "api", "/v1/dashboard/ioCareAppPairingFailStats", body)),
            ("서비스매니저 앱 페어링 실패", _post(s, "api", "/v1/dashboard/svcMgrAppPairingFailStats", body))]


def data_stats(s, q):
    _need(q, "prod_type", "start", "end")
    a, b = _ms_range(q)
    body = {"statsTypeCode": "1", "statsUnitType": "M", "startDate": a, "endDate": b, "prodTypeCode": q["prod_type"],
            "protocolType": "M", "devModelCodeList": [q["model"]] if q.get("model") else []}
    c = _content(s.call("POST", "api", "/v1/dashboard/ioCareDataStats", body))
    if isinstance(c, dict):
        return [(k, _rows(v)) for k, v in c.items() if isinstance(v, list)] or [("제품 Data 통계", [c])]
    return [("제품 Data 통계", _rows(c))]


def projects(s, q):
    year = (q.get("start") or str(dt.date.today().year))[:4]
    return [(f"프로젝트 {year}", _post(s, "api", "/v1/platform/project/list", {"projectYear": year}))]


def com_codes(s, q):
    out = []
    for g in _get(s, "api", "/v1/admin/comCodeGroup/list", iocareYn=""):
        for c in g.get("comCodeList") or []:
            out.append({"groupId": g["groupId"], "groupNm": g["groupNm"], "code": c.get("code"), "codeNm": c.get("codeNm"),
                        "langCode": c.get("langCode")})
    kw = (q.get("keyword") or "").strip()
    if kw:
        out = [r for r in out if kw in (r["groupNm"] or "") or kw in (r["codeNm"] or "") or kw == r["groupId"]]
    return [("공통 코드", out)]


def model_info(s, q):
    """제품(모델) 하나의 IoCare+ 앱 설정을 한 번에: 기준 정보·소모품·알림·자동화·정기 살균·스마트 진단·ODM 진단."""
    _need(q, "model")
    m = q["model"]
    pt = q.get("prod_type") or ""
    base = [r for r in _paged_body(s, "bo", "/v1/productMgmt/baseInfo/page", {"devModelCode": m}) if r.get("devModelCode") == m]
    if base and not pt:
        pt = base[0].get("prodTypeCode", "")
    flag = (base[0].get("flagshipModelCode") if base else "") or m
    out = [("기준 정보", base or _get(s, "bo", "/v1/productMgmt/baseInfo/content", devModelCode=m))]
    reg = _content(s.call("GET", "bo", "/v1/productMgmt/supply", params={"devModelCode": m, "prodTypeCode": pt})) or {}
    out.append(("등록 소모품", [{"구분": "방문", **x} for x in reg.get("visitSupplyList") or []] +
                [{"구분": "멤버십", **x} for x in reg.get("membershipSupplyList") or []]))
    noti = _content(s.call("GET", "bo", "/v1/productMgmt/notiConfig", params={"devModelCode": m})) or {}
    out.append(("알림 노출 설정", noti.get("itemList") or []))
    out.append(("자동화 설정", _get(s, "bo", "/v1/productMgmt/routineConfig/regRoutineList", devModelCode=m)))
    strl = _content(s.call("GET", "bo", "/v1/productMgmt/sterilizationConfig", params={"devModelCode": m})) or {}
    out.append(("정기 살균", strl.get("allStrlList") or []))
    out.append(("스마트 고장진단", _get(s, "bo", "/v1/productMgmt/smartDiagnosis/list", flagshipModelCode=flag, langCode="ko")))
    out.append(("ODM 진단", _get(s, "bo", "/v1/productMgmt/appOdmDiagnosis/list", popCode=m)))
    out.append(("ODM 프로토콜", _get(s, "api", "/v1/protocol/odmProtocol/list", popCode=m)))
    out.append(("ODM 에러 코드", _get(s, "api", "/v1/protocol/tuyaErrorCode/list", popCode=m)))
    return out


def app_problem(s, q):
    _need(q, "prod_type")
    return [("문제 해결 컨텐츠", _get(s, "bo", "/v1/productMgmt/problemGuide/list",
                                 prodCategoryCode=APP_CATEGORY[q["prod_type"]], level=1))]


def _simple(title, method, base, path, key=None, **params):
    def f(s, q):
        if method == "GET":
            return [(title, _get(s, base, path, key, **params))]
        return [(title, _post(s, base, path, params, key))]
    return f


def _page(title, path, key=None):
    return lambda s, q: [(title, _paged_get(s, "bo", path, {}, key=key))]


def _info_paged(title, path):
    return lambda s, q: [(title, _paged_body(s, "api", path, {}, page_key="info", size=100))]


# (id, 묶음, 이름, 쓰는 입력, 함수)
ITEMS = [
    ("model_info", "IoCareApp 관리", "제품별 앱 설정 한눈에 (기준정보·소모품·알림·자동화·살균·진단)", ["model"], model_info),
    ("base_info", "IoCareApp 관리", "제품 관리 > 기준 정보 (전체 모델)", [], lambda s, q: [("기준 정보", _paged_body(s, "bo", "/v1/productMgmt/baseInfo/page", {}))]),
    ("supply", "IoCareApp 관리", "컨텐츠 관리 > 소모품", [], _simple("소모품", "GET", "bo", "/v1/contentMgmt/supply/list")),
    ("pollution", "IoCareApp 관리", "컨텐츠 관리 > 오염원", [], _simple("오염원", "GET", "bo", "/v1/contentMgmt/pollution/list")),
    ("problem_guide", "IoCareApp 관리", "컨텐츠 관리 > 문제 해결 가이드", [], _simple("문제 해결 가이드", "GET", "bo", "/v1/contentMgmt/problemGuide/list")),
    ("app_problem", "IoCareApp 관리", "제품 관리 > 문제 해결 컨텐츠 (제품 유형별)", ["prod_type"], app_problem),
    ("faq", "IoCareApp 관리", "컨텐츠 관리 > FAQ", [], _simple("FAQ", "GET", "bo", "/v1/contentMgmt/faq/list")),
    ("faq_category", "IoCareApp 관리", "컨텐츠 관리 > FAQ 카테고리", [], _simple("FAQ 카테고리", "GET", "bo", "/v1/contentMgmt/faqCategory/list")),
    ("app_notice", "IoCareApp 관리", "컨텐츠 관리 > 공지사항 (앱)", [], _page("앱 공지사항", "/v1/contentMgmt/notice/page")),
    ("lifestory", "IoCareApp 관리", "라이프스토리 > 라이프스토리 컨텐츠", [], _page("라이프스토리", "/v1/lifeStoryMgmt/lifeStory/page")),
    ("lifestory_kw", "IoCareApp 관리", "라이프스토리 > 키워드 설정", [], _simple("라이프스토리 키워드", "GET", "bo", "/v1/lifeStoryMgmt/keywordMgmt/list")),
    ("routine", "IoCareApp 관리", "자동화 관리 > 자동화 컨텐츠", [], _simple("자동화 컨텐츠", "GET", "bo", "/v1/routineMgmt/routine/list")),
    ("routine_item", "IoCareApp 관리", "자동화 관리 > 자동화 항목 설정", [], _simple("자동화 항목", "GET", "bo", "/v1/routineMgmt/routineItem/list")),
    ("smart_img", "IoCareApp 관리", "스마트 고장진단 이미지 목록", [], _simple("진단 이미지", "GET", "bo", "/v1/productMgmt/smartDiagnosis/img/list")),
    ("app_country", "IoCareApp 관리", "공통 설정 > 제품 출시국가", [], _simple("출시국가", "GET", "bo", "/v1/common/appCountry/list")),
    ("app_region", "IoCareApp 관리", "공통 설정 > 제품 향지", [], _simple("제품 향지", "GET", "bo", "/v1/common/appCountryProductRegion/list")),
    ("app_cs", "IoCareApp 관리", "공통 설정 > 고객센터", [], _simple("고객센터", "GET", "bo", "/v1/common/appCustomerService/list")),
    ("app_category", "IoCareApp 관리", "공통 설정 > 제품 카테고리", [], _simple("제품 카테고리", "GET", "bo", "/v1/common/appProductCategory/list")),
    ("app_version", "IoCareApp 관리", "앱 관리 > 앱 업데이트", [], _page("앱 업데이트", "/v1/appMgmt/version/page", key="appVersionList")),
    ("app_maint", "IoCareApp 관리", "앱 관리 > 시스템 점검", [], _page("시스템 점검", "/v1/appMgmt/appSystemMaint/page")),
    ("total_stats", "대시 보드", "IoCare 제품 현황 (모델·국가별 판매/연결)", [], _simple("제품 현황", "GET", "api", "/v1/dashboard/deviceTotalProdStats")),
    ("pairing", "대시 보드", "제품 페어링 통계 + 앱 페어링 실패", ["start", "end"], pairing),
    ("data_stats", "대시 보드", "제품 Data 통계", ["prod_type", "model", "start", "end"], data_stats),
    ("device_info", "기기 관리", "운영 기기 조회 (시리얼·MAC·주문번호·제품)", ["keyword", "model", "prod_type"], device_info),
    ("pop_codes", "기기 관리", "POP 코드 관리 (제품 모델 목록)", ["prod_type", "model", "keyword"], pop_codes),
    ("models", "기기 관리", "전체 모델 목록 (POP 코드·제품명·플랫폼)", [], _simple("모델", "GET", "api", "/v1/ota/firmware/popCode/list")),
    ("svc_codes", "기기 관리", "서비스 코드 목록", [], _simple("서비스 코드", "GET", "api", "/v1/device/svcCode/list", prodTypeCode="")),
    ("ota_fw", "OTA", "펌웨어 관리", [], _info_paged("펌웨어", "/v1/ota/firmware/list")),
    ("ota_job", "OTA", "작업 관리", [], _info_paged("OTA 작업", "/v1/ota/job/list")),
    ("ota_user", "OTA", "사용자 OTA 등록 관리", [], _info_paged("사용자 OTA 등록", "/v1/ota/userOtaReg/list")),
    ("ota_hist", "OTA", "이력 관리", ["start", "end"], ota_hist),
    ("ota_aging", "OTA", "OTA Aging", ["start", "end"], ota_aging),
    ("proto_grid", "IoT 프로토콜", "프로토콜 통합 관리 (제품 유형별 전체 항목·모델 적용)", ["prod_type"], proto_grid),
    ("proto_api", "IoT 프로토콜", "프로토콜 API (제품 유형별)", ["prod_type"], proto_api),
    ("projects", "플랫폼 관리", "프로젝트 관리 (시작일의 연도)", ["start"], projects),
    ("ims_notice", "Admin 관리", "IMS 공지사항", [], lambda s, q: [("IMS 공지사항", _paged_body(s, "api", "/v1/admin/notice/list", {"noticeTypeCode": "", "title": "", "writers": []}, size=100))]),
    ("com_codes", "Admin 관리", "공통 코드 (검색어로 거르기)", ["keyword"], com_codes),
]
BY_LABEL = {f"{g} > {n}": (i, need, fn) for i, g, n, need, fn in ITEMS}
