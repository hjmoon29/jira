#!/usr/bin/env python3
"""IoT업무봇 데이터 동기화: 이 PC 의 runs/ 와 Google Drive 'IoT업무봇/runs/' 를 양방향으로 맞춘다.

- Google Drive 폴더가 있는 PC(Windows G:, Mac Drive 앱): 그 폴더에 직접 읽고 쓴다 (업로드는 Drive 앱이 함)
- Drive 앱이 없는 PC(이 Mac): Drive API (~/.config/claude-daily/gmail.json + drive.json, 또는 WORKBOT_DRIVE_API 폴더)
- 마지막으로 맞춘 내용(md5)을 runs/.sync/manifest.json 에 두고, 바뀐 쪽만 옮긴다.
  두 쪽 다 바뀌면 shared_merge 규칙으로 합치고(내 할 일·실행 기록·id 목록), 규칙이 없으면 최근 수정본을 쓰고
  다른 쪽은 runs/.sync/conflicts/ 에 남긴다. 지운 파일은 전파하지 않는다.
- 제외: 제품별 진행상황(자체 동기화), 진행률·잠금·임시·로그, 업로드 원본, 30MB 넘는 파일
"""
import datetime, glob, hashlib, json, os, re, sys, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # bots/
import shared_merge  # noqa: E402

RUNS = os.path.abspath(os.environ.get("BOT_HUB_RUNS") or os.path.join(HERE, "..", "..", "runs"))
META = os.path.join(RUNS, ".sync")
MANIFEST = os.path.join(META, "manifest.json")
LAST = os.path.join(META, "last.json")
ROOT_NAME, SUB = "IoT업무봇", "runs"
MAX_BYTES = 30 * 1024 * 1024
SKIP_DIRS = {".sync", "progress", "product-status", "uploads"}
SKIP_FILE = re.compile(r"(\.lock|\.tmp|\.log|\.err|\.out|\.pyc)$|^last_prompt\.txt$|^\.drive_file_id$|^\.DS_Store$|^desktop\.ini$", re.I)
API_DIR = os.path.expanduser(os.environ.get("WORKBOT_DRIVE_API") or "~/.config/claude-daily")
FOLDER = "application/vnd.google-apps.folder"


def skip(rel):
    parts = rel.split("/")
    return parts[0] in SKIP_DIRS or parts[0].startswith("daily-") or bool(SKIP_FILE.search(parts[-1]))


def md5(b):
    return hashlib.md5(b).hexdigest()


def drive_root():
    env = os.environ.get("WORKBOT_DRIVE")
    if env:
        return env if os.path.isdir(env) else None
    home = os.path.expanduser("~")
    cands = [f"{l}:/{n}" for l in "GHIJ" for n in ("내 드라이브", "My Drive")]
    for base in sorted(glob.glob(os.path.join(home, "Library", "CloudStorage", "GoogleDrive-*"))):
        cands += [os.path.join(base, "내 드라이브"), os.path.join(base, "My Drive")]
    cands += [os.path.join(home, "Google Drive", "내 드라이브"), os.path.join(home, "Google Drive", "My Drive")]
    return next((c for c in cands if os.path.isdir(c)), None)


# ── 이 PC 의 runs/ ───────────────────────────────────────────
def walk(top):
    """{rel: (abs, mtime)} — rel 은 / 구분"""
    out = {}
    for d, dirs, files in os.walk(top):
        rel_d = os.path.relpath(d, top).replace("\\", "/")
        rel_d = "" if rel_d == "." else rel_d + "/"
        dirs[:] = [x for x in dirs if not skip(rel_d + x + "/x")]
        for f in files:
            rel = rel_d + f
            p = os.path.join(d, f)
            if skip(rel):
                continue
            try:
                st = os.stat(p)
            except OSError:
                continue
            if st.st_size <= MAX_BYTES:
                out[rel] = (p, st.st_mtime)
    return out


def write_file(path, b, mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    open(tmp, "wb").write(b)
    os.replace(tmp, path)
    if mtime:
        os.utime(path, (mtime, mtime))


# ── 원격: Drive 폴더 ─────────────────────────────────────────
class FolderRemote:
    def __init__(self, top):
        self.top = top
        self.kind = "Google Drive 폴더"

    def list(self):
        return {rel: {"path": p, "mtime": mt, "md5": None} for rel, (p, mt) in walk(self.top).items()}

    def md5(self, rel, info):
        return md5(open(info["path"], "rb").read())

    def read(self, rel, info):
        return open(info["path"], "rb").read()

    def write(self, rel, info, b):
        write_file(os.path.join(self.top, *rel.split("/")), b)


# ── 원격: Drive API ──────────────────────────────────────────
class ApiRemote:
    API = "https://www.googleapis.com/drive/v3/files"
    UP = "https://www.googleapis.com/upload/drive/v3/files"

    def __init__(self):
        self.kind = "Google Drive (API)"
        client = json.load(open(os.path.join(API_DIR, "gmail.json")))
        conf = json.load(open(os.path.join(API_DIR, "drive.json")))
        body = urllib.parse.urlencode({"client_id": client["client_id"], "client_secret": client["client_secret"],
                                       "refresh_token": conf["refresh_token"], "grant_type": "refresh_token"}).encode()
        self.token = self.http("POST", "https://oauth2.googleapis.com/token", body, "application/x-www-form-urlencoded", auth=False)["access_token"]
        self.folders = {}  # rel 폴더 → id ("" = runs)
        root = self.find(f"name = '{ROOT_NAME}' and mimeType = '{FOLDER}'", prefer_child="product-status")
        if not root:
            root = self.mkdir(ROOT_NAME, None)
        self.folders[""] = self.find(f"name = '{SUB}' and mimeType = '{FOLDER}' and '{root}' in parents") or self.mkdir(SUB, root)

    def http(self, method, url, body=None, ctype="application/json", auth=True, raw=False):
        h = {"Content-Type": ctype}
        if auth:
            h["Authorization"] = "Bearer " + self.token
        with urllib.request.urlopen(urllib.request.Request(url, body, h, method=method), timeout=60) as r:
            b = r.read()
        return b if raw else json.loads(b or b"{}")

    def query(self, q, fields="files(id,name,mimeType,md5Checksum,modifiedTime,size,parents)"):
        files, tok = [], None
        while True:
            p = {"q": q + " and trashed = false", "fields": "nextPageToken," + fields, "pageSize": 1000,
                 "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
            if tok:
                p["pageToken"] = tok
            r = self.http("GET", self.API + "?" + urllib.parse.urlencode(p))
            files += r.get("files", [])
            tok = r.get("nextPageToken")
            if not tok:
                return files

    def find(self, q, prefer_child=None):
        hits = self.query(q)
        if prefer_child and len(hits) > 1:
            for h in hits:
                if self.query(f"name = '{prefer_child}' and '{h['id']}' in parents"):
                    return h["id"]
        return hits[0]["id"] if hits else None

    def mkdir(self, name, parent):
        meta = {"name": name, "mimeType": FOLDER}
        if parent:
            meta["parents"] = [parent]
        return self.http("POST", self.API + "?supportsAllDrives=true", json.dumps(meta).encode())["id"]

    def list(self):
        out, todo = {}, [("", self.folders[""])]
        while todo:
            rel_d, fid = todo.pop()
            for f in self.query(f"'{fid}' in parents"):
                rel = (rel_d + "/" if rel_d else "") + f["name"]
                if f["mimeType"] == FOLDER:
                    if not skip(rel + "/x"):
                        self.folders[rel] = f["id"]
                        todo.append((rel, f["id"]))
                elif not skip(rel) and int(f.get("size") or 0) <= MAX_BYTES and "md5Checksum" in f:
                    mt = datetime.datetime.strptime(f["modifiedTime"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc).timestamp()
                    out[rel] = {"id": f["id"], "mtime": mt, "md5": f["md5Checksum"]}
        return out

    def md5(self, rel, info):
        return info["md5"]

    def read(self, rel, info):
        return self.http("GET", f"{self.API}/{info['id']}?alt=media&supportsAllDrives=true", raw=True)

    def folder(self, rel_d):
        if rel_d in self.folders:
            return self.folders[rel_d]
        parent = self.folder(rel_d.rsplit("/", 1)[0] if "/" in rel_d else "")
        name = rel_d.rsplit("/", 1)[-1]
        fid = self.find(f"name = '{name}' and mimeType = '{FOLDER}' and '{parent}' in parents") or self.mkdir(name, parent)
        self.folders[rel_d] = fid
        return fid

    def write(self, rel, info, b):
        if info and info.get("id"):
            self.http("PATCH", f"{self.UP}/{info['id']}?uploadType=media&supportsAllDrives=true", b, "application/octet-stream")
            return
        parent = self.folder(rel.rsplit("/", 1)[0] if "/" in rel else "")
        boundary = "workbot" + md5(b)[:16]
        meta = json.dumps({"name": rel.rsplit("/", 1)[-1], "parents": [parent]}).encode()
        body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode() + meta +
                f"\r\n--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n".encode() + b + f"\r\n--{boundary}--".encode())
        self.http("POST", f"{self.UP}?uploadType=multipart&supportsAllDrives=true", body, f"multipart/related; boundary={boundary}")


def remote():
    root = drive_root()
    if root:
        top = os.path.join(root, ROOT_NAME, SUB)
        os.makedirs(top, exist_ok=True)
        return FolderRemote(top)
    if all(os.path.isfile(os.path.join(API_DIR, f)) for f in ("gmail.json", "drive.json")):
        return ApiRemote()
    return None


# ── 동기화 ──────────────────────────────────────────────────
def sync():
    t0 = time.time()
    os.makedirs(META, exist_ok=True)
    try:
        r = remote()
    except Exception as e:  # noqa: BLE001
        return finish({"ok": False, "text": f"Drive 연결 실패: {e.__class__.__name__}: {str(e)[:150]}"}, t0)
    if not r:
        return finish({"ok": False, "text": "Google Drive 폴더도, Drive API 인증(~/.config/claude-daily)도 없어 데이터 동기화를 못 합니다."}, t0)
    try:
        base = json.load(open(MANIFEST, encoding="utf-8"))
    except (OSError, ValueError):
        base = {}
    local, rem = walk(RUNS), r.list()
    up, down, merged, conflicts, errors = [], [], [], [], []
    for rel in sorted(set(local) | set(rem)):
        try:
            lb = open(local[rel][0], "rb").read() if rel in local else None
            L = md5(lb) if lb is not None else None
            R = r.md5(rel, rem[rel]) if rel in rem else None
            B = base.get(rel)
            if L == R:
                base[rel] = L
                continue
            lp = os.path.join(RUNS, *rel.split("/"))
            if R is None or (L is not None and R == B):            # 이 PC 쪽만 바뀜 / 원격에 없음
                r.write(rel, rem.get(rel), lb)
                base[rel] = L
                up.append(rel)
            elif L is None or L == B:                                # 원격 쪽만 바뀜 / 이 PC 에 없음
                rb = r.read(rel, rem[rel])
                write_file(lp, rb, rem[rel]["mtime"])
                base[rel] = md5(rb)
                down.append(rel)
            else:                                                    # 두 쪽 다 바뀜
                rb = r.read(rel, rem[rel])
                theirs_newer = rem[rel]["mtime"] > local[rel][1]
                mb = shared_merge.merge_bytes(rel, lb, rb, theirs_newer)
                if mb is None:
                    win, lose = (rb, lb) if theirs_newer else (lb, rb)
                    keep = os.path.join(META, "conflicts", time.strftime("%Y%m%d-%H%M%S") + "_" + rel.replace("/", "__"))
                    write_file(keep, lose)
                    conflicts.append(rel)
                    mb = win
                else:
                    merged.append(rel)
                write_file(lp, mb)
                if md5(mb) != R:
                    r.write(rel, rem[rel], mb)
                base[rel] = md5(mb)
        except Exception as e:  # noqa: BLE001  한 파일이 실패해도 나머지는 계속
            errors.append(f"{rel}: {e.__class__.__name__}: {str(e)[:120]}")
    json.dump(base, open(MANIFEST, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    parts = [f"{r.kind}와 맞춤"]
    for label, xs in (("올림", up), ("받음", down), ("합침", merged), ("충돌(최근 수정본 사용)", conflicts)):
        if xs:
            parts.append(f"{label} {len(xs)}개: " + ", ".join(xs[:8]) + (" …" if len(xs) > 8 else ""))
    if errors:
        parts.append(f"실패 {len(errors)}개: " + " / ".join(errors[:3]))
    if len(parts) == 1:
        parts.append("변경 없음")
    return finish({"ok": not errors, "text": "\n".join(parts), "changed": bool(up or down or merged or conflicts),
                   "up": len(up), "down": len(down), "merged": len(merged), "conflicts": len(conflicts)}, t0)


def finish(res, t0):
    res["at"] = time.strftime("%Y-%m-%d %H:%M")
    res["sec"] = round(time.time() - t0, 1)
    try:
        os.makedirs(META, exist_ok=True)
        json.dump(res, open(LAST, "w", encoding="utf-8"), ensure_ascii=False)
    except OSError:
        pass
    return res


def last():
    try:
        return json.load(open(LAST, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


if __name__ == "__main__":
    print(json.dumps(sync(), ensure_ascii=False, indent=1))
