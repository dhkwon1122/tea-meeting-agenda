"""사용자 등록 — 개인별 Confluence 토큰·온프렘 LLM 설정을 **이 PC·이 Windows 계정**에만 보관한다.

2026-09-30 사용자: git 주소를 팀에 공유해 각자 자기 PC에서 쓰게 하려 한다. 토큰은 사람마다 다르고
도용되면 안 된다 — 첫 화면에서 사용자를 등록하고, 화면 위에 누구의 토큰·어떤 API를 쓰는지 보여 달라.

보관 원칙
- 저장 위치는 저장소 폴더가 아니라 **사용자 폴더**(Windows `%APPDATA%\\doc2report\\account.json`) —
  저장소 폴더를 통째로 복사·공유하거나 실수로 커밋해도 토큰이 따라가지 않는다.
- 토큰·API 키는 **Windows DPAPI**(CryptProtectData, 현재 사용자 범위)로 암호화한다. 같은 PC의 같은
  Windows 로그인 계정에서만 풀린다 — 파일을 다른 PC나 다른 계정으로 가져가면 못 쓴다. 표준 라이브러리
  (ctypes)만 쓴다(사내 PC는 오프라인 설치라 패키지를 늘리지 않는다).
- Windows가 아니면(개발용 Linux·Mac) 암호화 없이 파일 권한(600)만 건다 — 화면에 그렇게 표시한다.
- 토큰은 화면(브라우저)으로 절대 되돌려 보내지 않는다. 끝 4자리만 보여 준다.

변환 코드는 예전처럼 환경변수(CONFLUENCE_*, DOC2REPORT_LLM_*)를 읽는다 — 등록 정보를 서버 프로세스의
환경변수에 넣어 줄 뿐이라(`apply`) 변환 쪽에 사용자 개념을 들일 필요가 없다. 등록 정보가 있으면 스크립트
(scripts/*_env.ps1)로 넣은 환경변수보다 우선한다.
"""

from __future__ import annotations

import base64
import json
import os
import platform
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

HOME_ENV = "DOC2REPORT_HOME"  # 보관 폴더를 바꿀 때(테스트 등)
FILE_NAME = "account.json"

# 등록 항목 → 변환 코드가 읽는 환경변수
ENV_KEYS = {
    "confluence_url": "CONFLUENCE_URL",
    "confluence_username": "CONFLUENCE_USERNAME",
    "confluence_token": "CONFLUENCE_API_TOKEN",
    "llm_base_url": "DOC2REPORT_LLM_BASE_URL",
    "llm_model": "DOC2REPORT_MODEL",
    "llm_api_key": "DOC2REPORT_LLM_API_KEY",
}
SECRETS = ("confluence_token", "llm_api_key")

# 서버를 켤 때의 환경변수(스크립트로 넣은 값) — 등록을 지우면 이 값으로 되돌린다
_ORIGINAL_ENV = {env: os.environ.get(env) for env in (*ENV_KEYS.values(), "NO_PROXY")}


@dataclass
class Account:
    name: str
    values: dict[str, str] = field(default_factory=dict)  # ENV_KEYS의 항목(비밀 값은 평문 — 메모리에서만)
    verified_as: str = ""   # Confluence가 알려 준 토큰 주인(표시 이름·사용자 이름)
    verified_at: str = ""
    created_at: str = ""

    def secret_hint(self, key: str) -> str:
        value = self.values.get(key) or ""
        return f"…{value[-4:]}" if len(value) >= 8 else ("설정됨" if value else "")


def home() -> Path:
    custom = os.environ.get(HOME_ENV)
    if custom:
        return Path(custom)
    if platform.system() == "Windows" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "doc2report"
    return Path.home() / ".config" / "doc2report"


def path() -> Path:
    return home() / FILE_NAME


def protection() -> str:
    return "Windows 계정 암호화(DPAPI)" if _dpapi_available() else "파일 권한만(암호화 없음)"


def load() -> Account | None:
    file = path()
    if not file.is_file():
        return None
    data = json.loads(file.read_text(encoding="utf-8"))
    values = dict(data.get("values") or {})
    for key in SECRETS:
        sealed = (data.get("secrets") or {}).get(key)
        if sealed:
            values[key] = _unseal(sealed)
    return Account(name=data.get("name", ""), values=values, verified_as=data.get("verified_as", ""),
                   verified_at=data.get("verified_at", ""), created_at=data.get("created_at", ""))


def save(account: Account) -> None:
    folder = home()
    folder.mkdir(parents=True, exist_ok=True)
    data = {
        "name": account.name,
        "values": {k: v for k, v in account.values.items() if k not in SECRETS and v},
        "secrets": {k: _seal(v) for k, v in account.values.items() if k in SECRETS and v},
        "protection": protection(),
        "verified_as": account.verified_as,
        "verified_at": account.verified_at,
        "created_at": account.created_at or _now(),
    }
    file = path()
    tmp = file.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    _owner_only(tmp)
    os.replace(tmp, file)


def delete() -> None:
    path().unlink(missing_ok=True)
    apply(None)


def apply(account: Account | None) -> None:
    """등록 정보를 이 프로세스의 환경변수에 넣는다(없으면 켤 때의 값으로 되돌린다)."""
    for key, env in ENV_KEYS.items():
        value = account.values.get(key) if account else _ORIGINAL_ENV.get(env)
        if value:
            os.environ[env] = value
        else:
            os.environ.pop(env, None)
    _bypass_proxy_for_llm(account)


def _bypass_proxy_for_llm(account: Account | None) -> None:
    """사내 LLM 주소는 프록시 예외(NO_PROXY)에 넣는다. PC에 HTTP_PROXY가 잡혀 있으면 httpx가 LLM 요청을
    사내 Squid로 돌려 403이 났다(실제로 겪음 — 예전엔 onprem_env 스크립트의 NO_PROXY로 피했다). 사내 서버는
    프록시를 거칠 이유가 없으므로 등록된 LLM 주소는 자동으로 뺀다."""
    original = _ORIGINAL_ENV.get("NO_PROXY") or ""
    host = urlparse((account.values.get("llm_base_url") if account else "") or "").hostname
    hosts = [h for h in original.split(",") if h.strip()]
    if host and host not in [h.strip() for h in hosts]:
        hosts.append(host)
    if hosts:
        os.environ["NO_PROXY"] = ",".join(hosts)
    else:
        os.environ.pop("NO_PROXY", None)


def load_and_apply() -> Account | None:
    """시작할 때 한 번 — 등록돼 있으면 그 값을 쓴다. 파일이 망가졌거나 다른 계정에서 만든 것이면
    (DPAPI가 못 푼다) 등록이 없는 것으로 보고 화면에서 다시 등록하게 한다."""
    try:
        account = load()
    except Exception:
        return None
    if account:
        apply(account)
    return account


def summary(account: Account | None) -> dict:
    """화면 위 표시용 — 비밀 값은 끝 4자리만."""
    env = os.environ
    confluence = {
        "url": env.get("CONFLUENCE_URL", ""),
        "secret": (account.secret_hint("confluence_token") if account
                  else ("설정됨" if env.get("CONFLUENCE_API_TOKEN") else "")),
        "verified_as": account.verified_as if account else "",
        "verified_at": account.verified_at if account else "",
        "source": "등록 사용자" if account and account.values.get("confluence_token")
                  else ("환경변수(스크립트)" if env.get("CONFLUENCE_API_TOKEN") else ""),
    }
    llm = {
        "base_url": env.get("DOC2REPORT_LLM_BASE_URL", ""),
        "model": env.get("DOC2REPORT_MODEL", ""),
        "secret": account.secret_hint("llm_api_key") if account else "",
        "source": "등록 사용자" if account and account.values.get("llm_base_url")
                  else ("환경변수(스크립트)" if env.get("DOC2REPORT_LLM_BASE_URL") else ""),
    }
    return {
        "registered": account is not None,
        "name": account.name if account else "",
        "protection": protection(),
        "confluence": confluence,
        "llm": llm,
        # 등록 화면을 채울 기본값(주소·모델명만 — 토큰은 절대 안 채운다)
        "defaults": {"confluence_url": env.get("CONFLUENCE_URL", ""),
                     "confluence_username": env.get("CONFLUENCE_USERNAME", ""),
                     "llm_base_url": env.get("DOC2REPORT_LLM_BASE_URL", ""),
                     "llm_model": env.get("DOC2REPORT_MODEL", "")},
    }


def update(form: dict, current: Account | None) -> Account:
    """등록 화면에서 받은 값으로 새 등록 정보를 만든다. 비밀 값 칸을 비워 두면 예전 값을 그대로 둔다
    (수정할 때마다 토큰을 다시 넣지 않게). "clear_llm_key"로 LLM 키를 지울 수 있다."""
    name = (form.get("name") or "").strip()
    if not name:
        raise ValueError("사용자 이름을 넣어 주세요")
    values = dict(current.values) if current else {}
    for key in ENV_KEYS:
        if key not in form:
            continue
        value = (form.get(key) or "").strip()
        if key in SECRETS and not value:
            continue  # 빈 칸 = 그대로
        values[key] = value
    if form.get("clear_llm_key"):
        values.pop("llm_api_key", None)
    if values.get("llm_base_url") and not values.get("llm_model"):
        raise ValueError("LLM 주소를 넣었으면 모델명도 넣어 주세요")
    same_token = current is not None and current.values.get("confluence_token") == values.get("confluence_token")
    return Account(name=name, values={k: v for k, v in values.items() if v},
                   verified_as=current.verified_as if current and same_token else "",
                   verified_at=current.verified_at if current and same_token else "",
                   created_at=current.created_at if current else _now())


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ── 암호화 (Windows DPAPI) ─────────────────────────────────────────────

_ENTROPY = b"doc2report-account-v1"


def _dpapi_available() -> bool:
    return platform.system() == "Windows"


def _seal(value: str) -> str:
    raw = value.encode("utf-8")
    if _dpapi_available():
        return "dpapi:" + base64.b64encode(_dpapi(raw, protect=True)).decode()
    return "plain:" + base64.b64encode(raw).decode()


def _unseal(sealed: str) -> str:
    kind, _, body = sealed.partition(":")
    raw = base64.b64decode(body)
    if kind == "dpapi":
        return _dpapi(raw, protect=False).decode("utf-8")
    if kind == "plain":
        return raw.decode("utf-8")
    raise ValueError(f"알 수 없는 보관 방식: {kind}")


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(raw: bytes) -> Blob:
        buf = ctypes.create_string_buffer(raw, len(raw))
        return Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    source, entropy, out = blob(data), blob(_ENTROPY), Blob()
    flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
    if protect:
        ok = crypt32.CryptProtectData(ctypes.byref(source), "doc2report", ctypes.byref(entropy),
                                      None, None, flags, ctypes.byref(out))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(source), None, ctypes.byref(entropy),
                                        None, None, flags, ctypes.byref(out))
    if not ok:
        raise OSError("토큰을 암호화/복호화하지 못했습니다 — 다른 Windows 계정이나 PC에서 만든 등록 정보일 수 있습니다")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def _owner_only(file: Path) -> None:
    try:
        os.chmod(file, 0o600)  # Windows에서는 읽기 전용 여부만 바뀌므로 사실상 무시된다(DPAPI가 보호)
    except OSError:
        pass
