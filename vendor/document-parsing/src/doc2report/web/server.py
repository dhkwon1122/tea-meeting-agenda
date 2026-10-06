"""doc2report 웹 화면 — 파이썬 표준 라이브러리(http.server)만 쓴다.

FastAPI 같은 웹 프레임워크를 쓰지 않는 이유: 사내 PC는 인터넷이 막혀 wheelhouse로 오프라인
설치를 한다. 패키지가 늘면 wheelhouse를 다시 만들어 옮겨야 한다 — 이 도구는 한 사람이 자기 PC에서
쓰는 화면이라 표준 라이브러리로 충분하다. 화면(static/)도 외부 CDN을 쓰지 않는다(사내망 차단).

보안: 기본으로 127.0.0.1에만 열린다(같은 PC에서만 접속). Confluence 토큰·LLM 주소가 이 서버
프로세스의 환경변수에 있으므로 다른 PC에 열지 않는다. 쓰기 요청(POST)은 전용 헤더
(X-Doc2Report)가 있어야 받는다 — 다른 사이트가 브라우저를 시켜 몰래 요청하는 것을 막는다.
파일 열기·내려받기는 저장 폴더 바로 아래 파일만 된다.

**예전 서버가 남아 있는 문제(2026-09-29 사용자 PC)**: git pull 뒤 서버 창을 닫지 않고 다시 켜면,
예전 파이썬 코드가 새 화면 파일(static/)을 내보내 화면이 반쯤 깨진다(서식 목록이 비고 체크박스가 안
그려짐). 막는 장치 셋: ① Windows에서는 포트를 **독점**으로 잡는다(SO_EXCLUSIVEADDRUSE — 기본
SO_REUSEADDR는 Windows에서 두 프로세스가 같은 포트를 나눠 잡게 해 요청이 예전 서버로 갈 수 있다)
② 포트가 이미 doc2report면 `/api/shutdown`으로 끄고 넘겨받는다 ③ 서버가 켜진 뒤 코드가 바뀌었으면
`/api/status`의 `stale`로 화면에 "서버를 다시 켜세요"를 띄운다.

**사용자 등록과 토큰 보호(2026-09-30, 팀 공유 준비)**: 토큰은 각자 등록해 이 PC 사용자 폴더에 암호화해
둔다(`account.py`). 서버는 세 겹으로 막는다 — ① 127.0.0.1에서만 연다 ② **접속 열쇠**: 서버가 여는 주소
(`/?k=…`)로 들어온 브라우저에만 쿠키를 주고, 쿠키가 없으면 화면만 보이고 API는 잠긴다(같은 PC의 다른
Windows 사용자나 다른 프로그램이 내 서버로 내 토큰을 쓰지 못하게). 열쇠는 사용자 폴더에 두어 서버를 다시
켜도 즐겨찾기가 그대로 된다 ③ **Host 확인**: 127.0.0.1/localhost가 아닌 이름으로 온 요청은 거절한다
(외부 사이트가 자기 도메인을 127.0.0.1로 돌려 브라우저를 시키는 DNS 리바인딩 차단).
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import platform
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import tempfile
import threading
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from .. import account as account_mod
from ..sources.confluence import confluence_status, confluence_whoami, fetch_page_title
from ..transform.llm_polish import llm_status, llm_try
from . import options as opts
from .jobs import JobRunner, history

STATIC = Path(__file__).parent / "static"
PACKAGE = Path(__file__).resolve().parents[1]
API_VERSION = 7  # 화면(app.js)과 서버가 주고받는 형식이 바뀌면 올린다 — app.js의 API_VERSION과 같아야 함
MAX_UPLOAD = 50 * 1024 * 1024
_STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}
_CONTENT_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
    ".md": "text/plain; charset=utf-8",
}


class App:
    def __init__(self, output_dir: Path, access_key: str | None = None):
        self.access_key = access_key  # None이면 열쇠 확인을 안 한다(테스트용 — serve()는 항상 켠다)
        self.account = account_mod.load_and_apply()
        self.output_dir = output_dir.resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir = Path(tempfile.mkdtemp(prefix="doc2report-uploads-"))
        self.jobs = JobRunner(self.output_dir, self.upload_dir)
        self.fingerprint = code_fingerprint()
        self.server: ThreadingHTTPServer | None = None
        self.handed_over = False  # 새로 켠 서버에 포트를 넘겨주고 끝났는가

    def status(self) -> dict:
        from ..render.pdf import pdf_engine

        return {"version": API_VERSION, "stale": code_fingerprint() != self.fingerprint,
                "confluence": confluence_status(), "llm": llm_status(),
                "account": account_mod.summary(self.account),
                "pdf": pdf_engine(), "output_dir": str(self.output_dir),
                "profile_errors": opts.profile_errors()}

    def save_account(self, form: dict) -> dict:
        """등록·수정. Confluence 토큰이 있으면 Confluence에 물어 토큰 주인을 확인한다."""
        account = account_mod.update(form, self.account)
        account_mod.apply(account)
        error = self._verify(account) if not account.verified_as else ""
        account_mod.save(account)
        self.account = account
        return {"ok": True, "account": account_mod.summary(account), "verify_error": error}

    def verify_account(self) -> dict:
        if not self.account:
            raise ValueError("등록된 사용자가 없습니다")
        self.account.verified_as = ""
        error = self._verify(self.account)
        account_mod.save(self.account)
        return {"ok": True, "account": account_mod.summary(self.account), "verify_error": error}

    def delete_account(self) -> dict:
        account_mod.delete()
        self.account = None
        return {"ok": True, "account": account_mod.summary(None)}

    @staticmethod
    def _verify(account) -> str:
        if not (account.values.get("confluence_url") and account.values.get("confluence_token")):
            return ""
        try:
            account.verified_as = confluence_whoami()
            account.verified_at = account_mod._now()
            return ""
        except Exception as exc:
            account.verified_as = account.verified_at = ""
            return f"토큰 주인을 확인하지 못했습니다: {exc}"

    def output_file(self, name: str) -> Path:
        """저장 폴더 바로 아래 파일만 — "../" 같은 경로로 폴더 밖을 못 가리키게."""
        path = (self.output_dir / name).resolve()
        if path.parent != self.output_dir or not path.is_file():
            raise FileNotFoundError(name)
        return path


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "doc2report"

        def log_message(self, fmt, *args):  # 콘솔을 요청 로그로 채우지 않는다
            pass

        # ── GET ─────────────────────────────────────────────────────────

        def do_GET(self):
            url = urlparse(self.path)
            path = url.path
            if not self._host_ok():
                return self._error(403, "허용되지 않은 주소입니다 — http://127.0.0.1 로 여세요")
            try:
                key = (parse_qs(url.query).get("k") or [""])[0]
                if path == "/" and key and app.access_key and secrets.compare_digest(key, app.access_key):
                    return self._send(302, b"", "text/plain", cache=False, extra={
                        "Location": "/", "Set-Cookie": f"{self._cookie_name()}={app.access_key}; "
                        "Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000"})
                if path == "/favicon.ico":
                    return self._static("favicon.svg")
                if path in ("/", "/index.html"):
                    return self._static("index.html")
                if path.startswith("/static/"):
                    return self._static(path[len("/static/"):])
                if path == "/api/status":
                    if not self._authorized():  # 잠김: 버전만(예전 서버 인계 확인용)
                        return self._json({"version": API_VERSION, "locked": True})
                    return self._json(app.status())
                if not self._authorized():
                    return self._locked()
                if path == "/api/profiles":
                    return self._json({"profiles": [opts.profile_info(n) for n in opts.profile_names()],
                                       "schema": opts.schema()})
                if path == "/api/history":
                    return self._json({"items": history(app.output_dir)})
                if path.startswith("/api/jobs/"):
                    job = app.jobs.jobs.get(path.rsplit("/", 1)[-1])
                    return self._json(job.to_json()) if job else self._error(404, "작업이 없습니다")
                if path.startswith("/files/"):
                    return self._file(unquote(path[len("/files/"):]))
                return self._error(404, "없는 주소입니다")
            except Exception as exc:
                return self._error(500, str(exc))

        # ── POST ────────────────────────────────────────────────────────

        def do_POST(self):
            path = urlparse(self.path).path
            if self.headers.get("X-Doc2Report") != "1" or not self._host_ok():
                return self._error(403, "허용되지 않은 요청입니다")
            if path != "/api/shutdown" and not self._authorized():
                return self._locked()
            try:
                if path == "/api/upload":
                    return self._upload()
                body = self._body_json()
                if path == "/api/convert":
                    return self._json({"job": app.jobs.submit(body).id})
                if path == "/api/confluence/check":
                    page_id, title = fetch_page_title(body.get("url", ""))
                    return self._json({"ok": True, "page_id": page_id, "title": title})
                if path == "/api/llm/test":
                    sample = body.get("text") or "시스템 응답 속도를 개선하기 위해 인덱스를 재설계하였습니다."
                    return self._json({"ok": True, "before": sample, "after": llm_try(sample)})
                if path == "/api/shutdown":  # 새로 켠 서버가 예전 서버를 끄고 포트를 넘겨받을 때
                    self._json({"ok": True})
                    app.handed_over = True
                    threading.Thread(target=app.server.shutdown, daemon=True).start()
                    return None
                if path == "/api/account":
                    return self._json(app.save_account(body))
                if path == "/api/account/verify":
                    return self._json(app.verify_account())
                if path == "/api/account/delete":
                    return self._json(app.delete_account())
                if path == "/api/open":
                    return self._json(_open(app, body.get("name", ""), folder=bool(body.get("folder"))))
                return self._error(404, "없는 주소입니다")
            except Exception as exc:
                return self._json({"ok": False, "error": str(exc) or exc.__class__.__name__}, 400)

        # ── 도우미 ──────────────────────────────────────────────────────

        def _host_ok(self) -> bool:
            bound = app.server.server_address[0] if app.server else "127.0.0.1"
            if bound not in ("127.0.0.1", "localhost", "::1"):
                return True  # 사용자가 --host로 일부러 연 경우
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
            return host in ("127.0.0.1", "localhost", "::1")

        def _cookie_name(self) -> str:
            port = app.server.server_address[1] if app.server else 0
            return f"d2r_key_{port}"  # 쿠키는 포트를 가리지 않으므로 이름에 포트를 넣는다

        def _authorized(self) -> bool:
            if not app.access_key:
                return True
            wanted = self._cookie_name() + "="
            for part in (self.headers.get("Cookie") or "").split(";"):
                part = part.strip()
                if part.startswith(wanted):
                    return secrets.compare_digest(part[len(wanted):], app.access_key)
            return False

        def _locked(self):
            return self._json({"ok": False, "locked": True,
                               "error": "잠긴 화면입니다 — 서버 창에 표시된 주소로 여세요"}, 401)

        def _upload(self):
            length = int(self.headers.get("Content-Length") or 0)
            name = Path(unquote(self.headers.get("X-Filename") or "문서.docx")).name
            if not name.lower().endswith(".docx"):
                return self._json({"ok": False, "error": "Word(.docx) 파일만 올릴 수 있습니다 "
                                   "(.doc은 Word에서 .docx로 다시 저장해 주세요)"}, 400)
            if length <= 0 or length > MAX_UPLOAD:
                return self._json({"ok": False, "error": "파일이 비었거나 너무 큽니다(50MB 이하)"}, 400)
            upload_id = uuid.uuid4().hex
            folder = app.upload_dir / upload_id
            folder.mkdir(parents=True)
            target = folder / name
            target.write_bytes(self.rfile.read(length))
            if target.read_bytes()[:2] != b"PK":
                shutil.rmtree(folder, ignore_errors=True)
                return self._json({"ok": False, "error": "Word 파일로 읽을 수 없습니다 — 문서보안(DRM)이 "
                                   "걸린 파일이면 Word에서 열어 내용을 '글 붙여넣기'로 넣어 주세요"}, 400)
            return self._json({"ok": True, "upload_id": upload_id, "name": name, "size": length})

        def _body_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw.decode("utf-8") or "{}")

        def _static(self, name: str):
            path = (STATIC / name).resolve()
            if path.parent != STATIC.resolve() or not path.is_file():
                return self._error(404, "없는 파일입니다")
            # Windows는 레지스트리 설정에 따라 .js/.css를 text/plain으로 추측하기도 해서
            # (그러면 브라우저가 스타일을 버린다) 화면 파일 형식은 직접 정한다.
            ctype = _STATIC_TYPES.get(path.suffix.lower()) or (
                mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self._send(200, path.read_bytes(), ctype, cache=False)

        def _file(self, name: str):
            query = urlparse(self.path).query
            try:
                path = app.output_file(name)
            except FileNotFoundError:
                return self._error(404, "파일이 없습니다(지웠거나 옮겼을 수 있음)")
            ctype = _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
            inline = path.suffix.lower() in (".pdf", ".md") and "download" not in query
            disposition = ("inline" if inline else "attachment") + f"; filename*=UTF-8''{quote(path.name)}"
            self._send(200, path.read_bytes(), ctype, extra={"Content-Disposition": disposition})

        def _json(self, data, status: int = 200):
            self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8", cache=False)

        def _error(self, status: int, message: str):
            self._json({"ok": False, "error": message}, status)

        def _send(self, status, body: bytes, ctype: str, *, cache=True, extra=None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            if not cache:
                self.send_header("Cache-Control", "no-store")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

    return Handler


def _open(app: App, name: str, *, folder: bool) -> dict:
    """결과 파일(또는 저장 폴더)을 이 PC의 기본 프로그램으로 연다 — .docx는 Word로."""
    target = app.output_dir if folder and not name else app.output_file(name)
    system = platform.system()
    if system == "Windows":
        if folder and name:
            subprocess.Popen(["explorer", "/select,", str(target)])
        else:
            os.startfile(str(target))  # type: ignore[attr-defined]  # Windows 전용
        return {"ok": True}
    opener = "open" if system == "Darwin" else shutil.which("xdg-open")
    if not opener:
        return {"ok": False, "error": "이 PC에서는 파일을 바로 열 수 없습니다 — 내려받기를 쓰세요"}
    subprocess.Popen([opener, str(target if target.is_dir() or not folder else target.parent)])
    return {"ok": True}


def code_fingerprint() -> str:
    """패키지의 .py·화면 파일 수정 시각 — 서버가 켜진 뒤 git pull로 바뀌었는지 알아보려고."""
    digest = hashlib.sha1()
    for path in sorted(PACKAGE.rglob("*")):
        if path.suffix in (".py", ".js", ".html", ".css") and "__pycache__" not in path.parts:
            digest.update(f"{path}:{path.stat().st_mtime_ns}".encode())
    for path in sorted(opts.PROFILE_DIR.glob("*.yaml")):
        digest.update(f"{path}:{path.stat().st_mtime_ns}".encode())
    return digest.hexdigest()


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # Windows의 SO_REUSEADDR는 이미 쓰는 포트도 또 잡게 해 준다(요청이 예전 서버로 갈 수 있음) — 끄고
    # 독점 모드로 잡아 포트가 쓰이고 있으면 확실히 실패하게 한다.
    allow_reuse_address = platform.system() != "Windows"

    def server_bind(self):
        if platform.system() == "Windows" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def create_server(host: str, port: int, output_dir: Path,
                  access_key: str | None = None) -> tuple[ThreadingHTTPServer, App]:
    app = App(output_dir, access_key)
    server = _Server((host, port), make_handler(app))
    app.server = server
    return server, app


def access_key() -> str:
    """접속 열쇠 — 사용자 폴더에 두어 서버를 다시 켜도 같은 값(즐겨찾기·쿠키가 그대로 된다).
    사용자 폴더(%APPDATA%)는 다른 Windows 사용자가 읽을 수 없다."""
    file = account_mod.home() / "access.key"
    try:
        value = file.read_text(encoding="utf-8").strip()
        if len(value) >= 32:
            return value
    except OSError:
        pass
    value = secrets.token_urlsafe(32)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(value, encoding="utf-8")
    account_mod._owner_only(file)
    return value


def _take_over(port: int) -> str:
    """포트를 쓰고 있는 게 doc2report면 끄게 한다. 결과: "stopped" | "old" | "other"."""
    base = f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(base + "/api/status", timeout=3) as res:
            status = json.loads(res.read().decode("utf-8"))
    except Exception:
        return "other"
    if "version" not in status:
        return "old"  # 끄는 기능이 없는 예전 버전
    request = urllib.request.Request(base + "/api/shutdown", data=b"{}", method="POST",
                                     headers={"X-Doc2Report": "1", "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(request, timeout=3).close()
    except Exception:
        return "other"
    time.sleep(1.0)
    return "stopped"


def serve(host: str = "127.0.0.1", port: int = 8765, output_dir: Path | None = None,
          open_browser: bool = True) -> None:
    output_dir = output_dir or Path(os.environ.get("DOC2REPORT_OUTPUT_DIR") or Path.cwd() / "out" / "webapp")
    key = access_key()
    try:
        server, app = create_server(host, port, output_dir, key)
    except OSError:
        result = _take_over(port)
        if result == "stopped":
            print(f"이미 켜져 있던 doc2report 서버(포트 {port})를 끄고 새로 시작합니다.")
            server, app = create_server(host, port, output_dir, key)
        else:
            reason = ("예전 버전의 doc2report 서버" if result == "old"
                      else "다른 프로그램")
            print(f"\n[시작 실패] 포트 {port}를 {reason}가 쓰고 있습니다.")
            print("  - 예전에 띄운 doc2report 서버 창(검은 창)이 있으면 모두 닫고 다시 실행하세요.")
            print("  - 창을 찾기 어려우면 작업 관리자에서 python.exe / uv.exe 를 끝내세요.")
            print(f"  - 또는 다른 포트로: start_webapp.bat --port {port + 1}")
            sys.exit(1)
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{server.server_address[1]}/?k={key}"
    print(f"doc2report 웹 화면: {url}")
    print("  (주소 끝의 k=… 는 이 PC 사용자만 쓰는 접속 열쇠입니다 — 다른 사람에게 알려 주지 마세요)")
    who = app.account.name if app.account else "미등록 — 화면에서 사용자 등록"
    print(f"  사용자: {who}")
    print(f"  결과 저장 폴더: {app.output_dir}")
    print("  끝내려면 이 창에서 Ctrl+C")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
        if app.handed_over:
            print("\n새로 켠 doc2report 서버에 자리를 넘기고 이 서버는 종료합니다. 이 창은 닫아도 됩니다.")
    except KeyboardInterrupt:
        print("\n종료합니다.")
    finally:
        server.server_close()
        shutil.rmtree(app.upload_dir, ignore_errors=True)
