#!/usr/bin/env python3
"""Mac(시스템 Python 3.9)에서도 도는지 문법 검사. 두 PC 공통 규칙: 봇 코드는 Python 3.9 문법으로.

- 3.9에서 실행하면 ast.parse 가 그대로 3.9 문법 검사가 된다(Mac).
- 3.10+ 에서 실행하면(Windows) feature_version=(3, 9)로 match 문 등 새 문법을 잡고,
  3.12+ 에서는 f-string { } 안의 백슬래시·바깥과 같은 따옴표(3.12부터만 허용)를 토큰으로 따로 찾는다.
  python3 py39check.py <파일...>   → 문제 있으면 목록 출력 후 종료 코드 1
"""
import ast, io, sys, tokenize


def _fstring_issues(src, path):
    """3.12+ 토큰(FSTRING_START/MIDDLE/END)으로 f-string 식 부분의 3.9 비호환을 찾는다"""
    out = []
    FS, FE = getattr(tokenize, "FSTRING_START", None), getattr(tokenize, "FSTRING_END", None)
    if FS is None:
        return out
    stack = []  # [바깥 따옴표, 식 깊이]
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == FS:
            q = tok.string.lstrip("rRfFbBuU")[:3]
            q = q if q in ('"""', "'''") else q[:1]
            if stack and stack[-1][1] > 0 and q[:1] == stack[-1][0][:1]:
                out.append(f"{path}:{tok.start[0]} f-string 안에 바깥과 같은 따옴표({q[:1]}) — 3.9에서 오류")
            stack.append([q, 0])
            continue
        if tok.type == FE:
            if stack:
                stack.pop()
            continue
        if not stack:
            continue
        top = stack[-1]
        if tok.type == tokenize.OP and tok.string == "{":
            top[1] += 1
        elif tok.type == tokenize.OP and tok.string == "}":
            top[1] = max(0, top[1] - 1)
        elif top[1] > 0:
            if "\\" in tok.string and tok.type != tokenize.COMMENT:
                out.append(f"{path}:{tok.start[0]} f-string {{ }} 안에 백슬래시 — 3.9에서 오류(밖에서 변수로 계산)")
            if tok.type == tokenize.STRING and tok.string.lstrip("rRbBuU")[:1] == top[0][:1]:
                out.append(f"{path}:{tok.start[0]} f-string 안에 바깥과 같은 따옴표 — 3.9에서 오류")
            if tok.type == tokenize.COMMENT:
                out.append(f"{path}:{tok.start[0]} f-string 안 주석 — 3.9에서 오류")
    return out


def problems(path):
    try:
        src = open(path, encoding="utf-8").read()
    except (OSError, UnicodeDecodeError) as e:
        return [f"{path}: 읽기 실패 {e}"]
    try:
        if sys.version_info >= (3, 10):
            ast.parse(src, filename=path, feature_version=(3, 9))
        else:
            ast.parse(src, filename=path)
    except SyntaxError as e:
        return [f"{path}:{e.lineno} {e.msg} — Python 3.9(Mac)에서 오류"]
    if sys.version_info >= (3, 12):
        try:
            return sorted(set(_fstring_issues(src, path)))
        except Exception:  # noqa: BLE001  검사기 자체 문제로 올리기를 막지는 않음
            return []
    return []


def check(paths):
    out = []
    for p in paths:
        if p.endswith(".py"):
            out += problems(p)
    return out


if __name__ == "__main__":
    found = check(sys.argv[1:])
    print("\n".join(found) if found else "OK: Python 3.9 문법 문제 없음")
    sys.exit(1 if found else 0)
