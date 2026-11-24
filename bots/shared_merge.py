"""두 PC 가 같은 데이터 파일을 동시에 고쳤을 때 합치는 규칙 (데이터 동기화·내 할 일 저장에서 같이 씀)"""
import json, re


def _key_t(t):
    return t.get("updated") or t.get("found") or ""


def merge_tasks(ours, theirs):
    """내 할 일 목록: id 기준. 같은 id·같은 found 면 updated 가 최신인 쪽(같으면 ours).
    같은 id 인데 found 가 다르면(두 PC 가 따로 만든 다른 할 일) theirs 쪽에 새 번호를 준다."""
    out = [dict(t) for t in ours]
    idx = {t.get("id"): i for i, t in enumerate(out)}
    n = max([int(t["id"][1:]) for t in list(ours) + list(theirs) if re.fullmatch(r"t\d+", str(t.get("id", "")))] or [0])
    for t in theirs:
        i = idx.get(t.get("id"))
        if i is None:
            out.append(dict(t))
            idx[t.get("id")] = len(out) - 1
        elif out[i].get("found") != t.get("found"):
            if any(x.get("found") == t.get("found") and x.get("title") == t.get("title") for x in out):
                continue  # 이미 번호를 바꿔 들어와 있는 같은 할 일
            n += 1
            out.append(dict(t, id=f"t{n}"))
        elif _key_t(t) > _key_t(out[i]):
            out[i] = dict(t)
    return out


def merge_state(ours, theirs):
    """내 할 일 state.json: 소스별 '마지막으로 읽은 시각'·파일별 seen 은 최신값, 나머지는 last_run 이 최신인 쪽"""
    newer, older = (theirs, ours) if (theirs.get("last_run") or {}).get("at", "") > (ours.get("last_run") or {}).get("at", "") else (ours, theirs)
    out = dict(older)
    out.update(newer)
    for k in set(ours) | set(theirs):
        a, b = ours.get(k), theirs.get(k)
        if isinstance(a, dict) and isinstance(b, dict) and k != "last_run":
            m = dict(a)
            for kk, vv in b.items():
                if kk not in m or (isinstance(vv, str) and isinstance(m[kk], str) and vv > m[kk]):
                    m[kk] = vv
            out[k] = m
    return out


def merge_id_list(ours, theirs, prefer_theirs=False):
    """id 가 있는 항목 목록(예약 등): 합집합, 같은 id 는 prefer 쪽"""
    out = [dict(x) for x in ours]
    idx = {x.get("id"): i for i, x in enumerate(out)}
    for x in theirs:
        i = idx.get(x.get("id"))
        if i is None:
            out.append(dict(x))
        elif prefer_theirs:
            out[i] = dict(x)
    return out


def merge_jsonl(ours, theirs):
    """실행 기록(history.jsonl): 줄 합집합, 시각순"""
    seen, rows = set(), []
    for line in (ours + "\n" + theirs).splitlines():
        s = line.strip()
        if s and s not in seen:
            seen.add(s)
            try:
                t = json.loads(s).get("t", 0)
            except ValueError:
                t = 0
            rows.append((t, s))
    rows.sort(key=lambda r: r[0])
    return "\n".join(s for _, s in rows) + "\n"


def merge_bytes(rel, ours, theirs, theirs_newer):
    """rel(runs/ 기준 경로)에 맞는 규칙으로 합친 바이트, 합칠 규칙이 없으면 None"""
    try:
        if rel.endswith(".jsonl"):
            return merge_jsonl(ours.decode("utf-8"), theirs.decode("utf-8")).encode("utf-8")
        if not rel.endswith(".json"):
            return None
        a, b = json.loads(ours.decode("utf-8")), json.loads(theirs.decode("utf-8"))
        if rel == "my-tasks/tasks.json" and isinstance(a, list) and isinstance(b, list):
            m = merge_tasks(a, b)
        elif rel == "my-tasks/state.json" and isinstance(a, dict) and isinstance(b, dict):
            m = merge_state(a, b)
        elif isinstance(a, list) and isinstance(b, list) and all(isinstance(x, dict) and "id" in x for x in a + b):
            m = merge_id_list(a, b, prefer_theirs=theirs_newer)
        else:
            return None
        return json.dumps(m, ensure_ascii=False, indent=1).encode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
