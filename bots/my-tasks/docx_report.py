#!/usr/bin/env python3
"""내 할 일 → 워드 문서(.docx). 서식은 '[기획 사양 정리] 말련향 정수기 2종' 9장 '업무 가이드'와 같게 맞춘다.
  - 제목(H1, 파란 글자 + 아래 선) · 안내문 · '근거 구분' 배지 범례 · 회색 주석
  - 기한 묶음마다 ▍소제목(H2) + 표 [No. | 할 일 | 근거 · 요청자 | 방법 · 참고 | 기한]
  - 맨 끝 '최근 완료' 2열 표(첫 열 굵게·회색 배경)
"""
import datetime, os, re

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.shared import Pt, RGBColor, Twips

FONT = "Malgun Gothic"
BLUE, HEAD_BG, GRAY, QUOTE, LINK = "0055CC", "DEEBFF", "626F86", "42526E", "0052CC"
BORDER = "C1C7D0"
WD = "월화수목금토일"
# 배지 색 (배경, 글자) — 참고 문서와 같은 값
ST = {"요청": ("E3FCEF", "006644"), "Jira": ("DEEBFF", "0747A6"), "약속": ("FFF0B3", "974F0C"), "메모": ("EAE6FF", "403294"),
      "추정": ("FFF0B3", "974F0C"), "완료": ("E3FCEF", "006644"), "제외": ("EBECF0", "42526E"), "보류": ("FFF0B3", "974F0C"),
      "높음": ("FFEBE6", "BF2600"), "보통": ("DEEBFF", "0747A6"), "낮음": ("EBECF0", "42526E")}
RED = "BF2600"
WIDTHS = [620, 760, 1950, 3300, 2076, 1200]  # DXA, 합 9906 (A4, 여백 1000)
HEADER = ["No.", "중요도", "할 일", "근거 · 요청자", "방법 · 참고", "기한"]


# ---------- 낮은 수준 도우미 ----------
def _shade(el_pr, fill):
    sh = OxmlElement("w:shd")
    sh.set(qn("w:val"), "clear"), sh.set(qn("w:color"), "auto"), sh.set(qn("w:fill"), fill)
    el_pr.append(sh)


def run(p, text, size=9.5, bold=False, color=None, italic=False, bg=None):
    r = p.add_run(text)
    f = r.font
    f.name, f.size, f.bold, f.italic = FONT, Pt(size), bold or None, italic or None
    r._element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    if color:
        f.color.rgb = RGBColor.from_string(color)
    if bg:
        _shade(r._element.get_or_add_rPr(), bg)
    return r


def badge(p, key, label=None):
    bg, fg = ST[key]
    return run(p, f" {label or key} ", size=8, bold=True, color=fg, bg=bg)


def link(p, text, url, size=8.5):
    rid = p.part.relate_to(url, RT.HYPERLINK, is_external=True)
    h = OxmlElement("w:hyperlink")
    h.set(qn("r:id"), rid)
    r = run(p, text, size=size, color=LINK)
    r.font.underline = True
    h.append(r._element)
    p._p.append(h)


def _cell_fmt(cell, width, bg=None):
    tc_pr = cell._tc.get_or_add_tcPr()
    w = OxmlElement("w:tcW")
    w.set(qn("w:w"), str(width)), w.set(qn("w:type"), "dxa")
    tc_pr.append(w)
    bd = OxmlElement("w:tcBorders")
    for side in ("top", "left", "bottom", "right"):
        e = OxmlElement(f"w:{side}")
        e.set(qn("w:val"), "single"), e.set(qn("w:sz"), "4"), e.set(qn("w:color"), BORDER)
        bd.append(e)
    tc_pr.append(bd)
    if bg:
        _shade(tc_pr, bg)
    mar = OxmlElement("w:tcMar")
    for side, v in (("top", 60), ("bottom", 60), ("left", 100), ("right", 100)):
        e = OxmlElement(f"w:{side}")
        e.set(qn("w:w"), str(v)), e.set(qn("w:type"), "dxa")
        mar.append(e)
    tc_pr.append(mar)


def table(doc, widths, header, rows, first_col_bold=False):
    """rows: 각 칸은 '글자' 또는 fill(cell) 함수"""
    t = doc.add_table(rows=0, cols=len(widths))
    tbl_pr = t._tbl.tblPr
    tw = OxmlElement("w:tblW")
    tw.set(qn("w:w"), str(sum(widths))), tw.set(qn("w:type"), "dxa")
    tbl_pr.append(tw)
    lay = OxmlElement("w:tblLayout")
    lay.set(qn("w:type"), "fixed")
    tbl_pr.append(lay)
    grid = t._tbl.tblGrid
    for gc, w in zip(grid.findall(qn("w:gridCol")), widths):
        gc.set(qn("w:w"), str(w))

    def fill_text(cell, text, bold=False):
        cell.paragraphs[0]._p.getparent().remove(cell.paragraphs[0]._p)
        for line in str(text).split("\n"):
            p = cell.add_paragraph()
            p.paragraph_format.space_after = Twips(0)
            run(p, line, size=9, bold=bold)

    if header:
        r = t.add_row()
        trp = r._tr.get_or_add_trPr()
        th = OxmlElement("w:tblHeader")
        th.set(qn("w:val"), "true")
        trp.append(th)
        for i, h in enumerate(header):
            _cell_fmt(r.cells[i], widths[i], HEAD_BG)
            fill_text(r.cells[i], h, bold=True)
    for row in rows:
        r = t.add_row()
        for i, c in enumerate(row):
            fc = first_col_bold and i == 0
            _cell_fmt(r.cells[i], widths[i], "F4F5F7" if fc else None)
            if callable(c):
                cell = r.cells[i]
                cell.paragraphs[0]._p.getparent().remove(cell.paragraphs[0]._p)
                c(cell)
                if not cell.paragraphs:
                    cell.add_paragraph()
            else:
                fill_text(r.cells[i], c, bold=fc)
    return t


def cp(cell):
    """표 칸 안의 한 문단 (참고 문서 P: 8.5pt, 아래 1.5pt)"""
    p = cell.add_paragraph()
    p.paragraph_format.space_after = Twips(30)
    return p


def h1(doc, text):
    p = doc.add_paragraph(style="Heading 1")
    run(p, text, size=14, bold=True, color=BLUE)
    pf = p.paragraph_format
    pf.space_before, pf.space_after = Twips(360), Twips(140)
    ppr = p._p.get_or_add_pPr()
    bdr = OxmlElement("w:pBdr")
    b = OxmlElement("w:bottom")
    b.set(qn("w:val"), "single"), b.set(qn("w:sz"), "6"), b.set(qn("w:space"), "4"), b.set(qn("w:color"), "4C9AFF")
    bdr.append(b)
    ppr.append(bdr)


def h2(doc, text, color=None):
    p = doc.add_paragraph(style="Heading 2")
    run(p, "▍", size=11.5, color=color or "1D7AFC")
    run(p, text, size=11.5, bold=True, color=color or BLUE)
    pf = p.paragraph_format
    pf.space_before, pf.space_after = Twips(240), Twips(100)


def body_p(doc, after=80):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Twips(after)
    return p


def _footer(section):
    p = section.footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run(p, "Coway Confidential · ", size=8, color=GRAY)

    def field(code):
        r = run(p, "", size=8, color=GRAY)
        for kind, txt in (("begin", None), (None, code), ("separate", None), (None, None), ("end", None)):
            if kind:
                e = OxmlElement("w:fldChar")
                e.set(qn("w:fldCharType"), kind)
            elif txt:
                e = OxmlElement("w:instrText")
                e.set(qn("xml:space"), "preserve")
                e.text = txt
            else:
                e = OxmlElement("w:t")
                e.text = "1"
            r._element.append(e)
    field(" PAGE ")
    run(p, " / ", size=8, color=GRAY)
    field(" NUMPAGES ")


# ---------- 할 일 → 칸 내용 ----------
def kind_of(t):
    req = (t.get("requester") or "").strip()
    ev = t.get("evidence") or []
    if req.startswith("본인"):
        return "메모" if ev and all(e.startswith("memo:") for e in ev) else "약속"
    if any(e.startswith("jira:") for e in ev) and "Jira" in (t.get("how") or ""):
        return "Jira"
    return "요청"


def link_label(url):
    m = re.search(r"/browse/([A-Z0-9]+-\d+)", url)
    if m:
        return m.group(1)
    if "mail.google.com" in url:
        return "메일 원문"
    if "wiki.coway.com" in url:
        return "위키"
    if "docs.google.com" in url or "drive.google.com" in url:
        return "Drive 문서"
    return "원문"


def fmt_due(t):
    d = t.get("due") or ""
    if not d:
        return "미정"
    try:
        return f"{d[5:].replace('-', '/')}({WD[datetime.date.fromisoformat(d).weekday()]})"
    except ValueError:
        return d


def task_row(no, t):
    def c_title(cell):
        p = cp(cell)
        run(p, t.get("title") or "", size=9)

    def c_basis(cell):
        p = cp(cell)
        badge(p, kind_of(t))
        run(p, " " + (t.get("requester") or "확인 필요"), size=8.5)
        if t.get("guessed"):
            run(p, " ", size=8.5)
            badge(p, "추정")
        if t.get("how"):
            run(cp(cell), t["how"], size=8.5)
        if t.get("quote"):
            run(cp(cell), f"“{t['quote']}”", size=8.5, color=QUOTE, italic=True)

    def c_method(cell):
        for line in [x.strip() for x in (t.get("detail") or "").split("\n") if x.strip()]:
            run(cp(cell), line, size=8.5)
        links = t.get("links") or []
        if links:
            p = cp(cell)
            seen = {}
            for i, u in enumerate(links):
                lb = link_label(u)
                seen[lb] = seen.get(lb, 0) + 1
                if i:
                    run(p, " · ", size=8.5)
                link(p, lb + (f" {seen[lb]}" if lb in ("메일 원문", "위키", "Drive 문서") and seen[lb] > 1 else ""), u)
        hist = t.get("history") or []
        if hist:
            run(cp(cell), f"갱신 {hist[-1].get('at', '')[5:]}: {hist[-1].get('why', '')}", size=8, color=GRAY)

    def c_due(cell):
        run(cp(cell), fmt_due(t), size=9, bold=bool(t.get("overdue_days")), color=RED if t.get("overdue_days") else None)
        if t.get("overdue_days"):
            run(cp(cell), f"{t['overdue_days']}일 지남", size=8.5, bold=True, color=RED)
        if t.get("due_text"):
            run(cp(cell), t["due_text"], size=8, color=GRAY)

    return [no, c_pri(t), c_title, c_basis, c_method, c_due]


def c_pri(t):
    def f(cell):
        badge(cp(cell), t.get("priority") if t.get("priority") in ("높음", "보통", "낮음") else "보통")
    return f


def fmt_day(d):
    try:
        x = datetime.date.fromisoformat(d[:10])
        return f"{x:%m/%d}({WD[x.weekday()]})"
    except ValueError:
        return d


# ---------- 문서 ----------
def build(v, name="", out_path=None):
    today = datetime.date.fromisoformat(v["today"])
    now = datetime.datetime.now()
    lr = v.get("last_run") or {}
    who = f"{name}님" if name else "내"

    doc = Document()
    st = doc.styles["Normal"]
    st.font.name, st.font.size = FONT, Pt(9.5)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    st.paragraph_format.line_spacing = 1.0
    for s in ("Heading 1", "Heading 2"):  # 기본 제목 서식(색·글꼴) 대신 run 서식이 보이게
        hs = doc.styles[s]
        hs.font.name, hs.font.color.rgb = FONT, RGBColor.from_string(BLUE)
        hs.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Twips(11906), Twips(16838)
    sec.top_margin = sec.bottom_margin = sec.left_margin = sec.right_margin = Twips(1000)
    _footer(sec)

    # 표지 줄
    p = body_p(doc, 100)
    run(p, f"[IoT업무봇] 내 할 일 — {today:%Y.%m.%d}({WD[today.weekday()]})", size=17, bold=True, color="172B4D")
    p = body_p(doc, 200)
    run(p, f"작성 기준 {now:%Y.%m.%d %H:%M} · 마지막 확인 {lr.get('at', '-')} · 새 자료 {lr.get('items', 0)}건 확인 → "
           f"새로 찾은 일 {lr.get('new', 0)} · 바뀐 일 {lr.get('updated', 0)} · 자동 완료 {lr.get('done', 0)}", size=8.5, color=GRAY)

    # 9장과 같은 머리
    h1(doc, f"✅ {who} 업무 가이드")
    late = sum(1 for t in v["open"] if t["group"] == "지난 기한")
    hi = sum(1 for t in v["open"] if t.get("priority") == "높음")
    p = body_p(doc)
    run(p, f"메일 · Jira · 위키 · Google Drive · 업무메모에서 찾은 할 일 {len(v['open'])}건을 기한 순(같은 기한 묶음 안에서는 중요도 순)으로 정리했습니다. 중요도 높음 {hi}건.")
    if late:
        run(p, f" 기한이 지났는데 아직 완료되지 않은 일이 {late}건 있습니다.", bold=True, color=RED)
    p = body_p(doc)
    run(p, "근거 구분  ", size=8.5, bold=True)
    for k, d in (("요청", "다른 사람이 직접 요청·배정"), ("Jira", "Jira 이슈 담당·멘션"), ("약속", "내가 하겠다고 한 일(보낸 메일·댓글)"),
                 ("메모", "업무메모에 적은 일"), ("추정", "요청자·방식이 자료에 분명하지 않음")):
        badge(p, k)
        run(p, f" {d}   ", size=8.5)
    p = body_p(doc)
    run(p, "중요도  ", size=8.5, bold=True)
    for k, d in (("높음", "기한 임박·지남, 윗선·법인·여러 부서가 기다림, 일정 영향"), ("보통", "일반 업무"), ("낮음", "참고·확인 수준, 기한 여유")):
        badge(p, k)
        run(p, f" {d}   ", size=8.5)
    p = body_p(doc)
    run(p, "기한은 자료에 적힌 표현(기한 칸 회색 글씨)을 오늘 날짜 기준으로 바꾼 값입니다. 인용문은 원문 일부만 옮겼으니 정확한 내용은 원문 링크에서 확인하세요. "
           "완료 · 내 일 아님 · 보류 표시와 보류 항목의 기한 재지정 · 삭제는 IoT업무봇 화면에서 할 수 있습니다.", size=8, color=GRAY)
    errs = lr.get("errors") or []
    if errs:
        p = body_p(doc)
        run(p, "이번에 읽지 못한 자료: " + " / ".join(errs), size=8, color="BF2600")

    week_s = today - datetime.timedelta(days=today.weekday())
    week_e = week_s + datetime.timedelta(days=6)
    titles = {"지난 기한": "기한 지남 · 아직 완료 안 됨 (먼저 처리)", "오늘": f"오늘 ({today:%m/%d})", "이번 주": f"이번 주 ({week_s:%m/%d} ~ {week_e:%m/%d})",
              "다음 주 이후": f"다음 주 이후 ({week_e + datetime.timedelta(days=1):%m/%d} ~)", "기한 미정": "기한 미정"}
    letter = iter("ABCDEFGHIJ")
    for g in v["groups"]:
        ts = [t for t in v["open"] if t["group"] == g]
        if not ts:
            continue
        L = next(letter)
        h2(doc, f"{L}. {titles[g]}", color=RED if g == "지난 기한" else None)
        table(doc, WIDTHS, HEADER, [task_row(f"{L}-{i}", t) for i, t in enumerate(ts, 1)])
    if not v["open"]:
        run(body_p(doc), "남은 할 일이 없습니다.")

    # 보류
    hold = v.get("hold") or []
    if hold:
        L = next(letter)
        h2(doc, f"{L}. 보류 ({len(hold)}건)")
        run(body_p(doc), "직접 보류한 일입니다. IoT업무봇 화면에서 기한을 정해 할 일로 되돌리거나 삭제할 수 있습니다.", size=8, color=GRAY)
        rows = []
        for i, t in enumerate(hold, 1):
            def c_t(cell, t=t):
                run(cp(cell), t.get("title") or "", size=9)
                if t.get("detail"):
                    run(cp(cell), t["detail"].split("\n")[0], size=8.5, color=QUOTE)

            def c_w(cell, t=t):
                p = cp(cell)
                badge(p, "보류")
                run(p, f" {fmt_day(t.get('held') or '')}", size=8.5)
                run(cp(cell), f"원래 기한 {fmt_due(t)}", size=8.5, color=GRAY)

            def c_r(cell, t=t):
                p = cp(cell)
                run(p, (t.get("requester") or "확인 필요") + (" · " + t["how"] if t.get("how") else ""), size=8.5)
            rows.append([f"{L}-{i}", c_pri(t), c_t, c_w, c_r])
        table(doc, [620, 760, 3250, 1700, 3576], ["No.", "중요도", "할 일", "보류 · 원래 기한", "근거 · 요청자"], rows)

    # 완료 (날짜별, 최근 7일)
    cut = (today - datetime.timedelta(days=7)).isoformat()
    days = [x for x in v.get("done_days") or [] if x["date"] >= cut]
    if days:
        L = next(letter)
        h2(doc, f"{L}. 완료 (날짜별 · 최근 7일 {sum(len(x['items']) for x in days)}건)")
        for x in days:
            p = body_p(doc, 60)
            p.paragraph_format.space_before = Twips(120)
            run(p, f"{fmt_day(x['date'])} · {len(x['items'])}건", size=9.5, bold=True, color="172B4D")
            rows = []
            for t in x["items"]:
                def c_left(cell, t=t):
                    run(cp(cell), t.get("title") or "", size=9, bold=True)

                def c_right(cell, t=t):
                    p = cp(cell)
                    badge(p, "완료")
                    run(p, f" {(t.get('closed') or '')[11:16]} · {'자동 판단' if t.get('closed_by') == '자동' else '직접 표시'}", size=8.5)
                    if t.get("closed_why"):
                        run(cp(cell), t["closed_why"], size=8.5, color=QUOTE)
                rows.append([c_left, c_right])
            table(doc, [3300, 6606], ["할 일", "완료 근거"], rows, first_col_bold=True)

    out_path = out_path or default_path(today)
    try:
        doc.save(out_path)
    except PermissionError:  # 같은 파일을 워드로 열어 둔 경우
        base, ext = os.path.splitext(out_path)
        out_path = f"{base}_{now:%H%M}{ext}"
        doc.save(out_path)
    return out_path


def default_path(today=None):
    today = today or datetime.date.today()
    return os.path.join(os.path.expanduser("~"), "Downloads", f"[IoT업무봇] 내 할 일_{today:%y%m%d}.docx")
