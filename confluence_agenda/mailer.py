"""안건 보고 내용을 사내 메일 발송 REST API로 보내는 기능 (선택).

`dhkwon1122/ai-friendly-doc` 저장소(`src/ai_friendly_doc/web/mailer.py`)의
구현을 그대로 참고했다. SMTP가 아니라 사내 메일 API(기본값
openapi.samsung.net)를 REST 호출로 쓴다. JSON을 문자열로 직렬화해서
그대로 요청 본문(body)에 담아 POST한다(Content-Type:
application/json;charset=utf-8) - multipart/form-data가 아니다. (원본
저장소의 기록: 스펙 문서의 Content-Disposition 언급 때문에 처음엔
multipart로 "mail" 파트에 실어 보내도록 구현했다가 "CO400" 파라미터
오류를 겪었고, 실제로 성공한 참조 코드가 `data=json.dumps(payload)`로
평문 JSON 바디를 보내는 것이었다.)

userId 쿼리 파라미터도 requests의 params= kwarg로 넘기면 API가 못
읽는다 - URL 문자열에 직접 `?userId=...`를 붙여서 보내야 한다. 파라미터
이름은 대소문자까지 정확히 "userId"여야 한다 - "userID"로 보내면
파라미터를 못 찾아 오류가 난다.

MAIL_API_TOKEN이 설정된 경우에만 동작한다.

send_report_email()의 attachments 인자(파일 첨부)는 추측으로 구현한
것이다 - 자세한 내용은 그 함수의 docstring 참고.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Optional, Sequence, Union
from urllib.parse import quote

import requests

_logger = logging.getLogger(__name__)

DEFAULT_MAIL_API_BASE_URL = "https://openapi.samsung.net/mail/api/v2.0"
DEFAULT_TIMEOUT_SECONDS = 30.0


class MailConfigError(RuntimeError):
    """메일 API 설정 누락이나 발송 실패 시 발생."""


def _parse_bool_env(value: str | None, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() not in ("false", "0", "no", "off")


def is_mail_configured() -> bool:
    return bool(os.environ.get("MAIL_API_TOKEN"))


def _require_env(name: str) -> str:
    # .env에 값을 붙여넣는 과정에서 앞뒤 공백/개행이 섞여 들어가기 쉽고,
    # 그러면 자격증명 자체는 맞아도 헤더 값이 미묘하게 달라져 401이 나는
    # 흔한 원인이라 방어적으로 strip한다.
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise MailConfigError(
            f"{name} 환경변수가 설정되지 않았습니다. MAIL_API_TOKEN / MAIL_API_SYSTEM_ID / "
            "MAIL_API_USER_ID 를 모두 설정해주세요."
        )
    return value


def send_report_email(
    to: Union[str, Sequence[str]],
    subject: str,
    body_html: str,
    *,
    attachments: Optional[Sequence[dict]] = None,
) -> None:
    """body_html(HTML)을 to(문자열 하나 또는 이메일 주소 목록)에 보낸다.

    문자열 하나를 넘기면 수신자 한 명, 목록을 넘기면 한 통의 메일을
    여러 명에게(recipients 배열에 모두 담아) 동시에 보낸다.

    MAIL_API_TOKEN/MAIL_API_SYSTEM_ID/MAIL_API_USER_ID 중 하나라도 없으면
    MailConfigError를 던진다. 호출 자체가 실패해도(네트워크 오류, 4xx/5xx
    응답) 원인을 그대로 담아 MailConfigError로 감싸서 던진다 - 호출자가
    화면에 실패 사유를 보여줄 수 있도록.

    attachments는 [{"filename": str, "content": bytes, "content_type": str}, ...]
    형태 - 지금까지 이 메일 API는 본문 HTML만 보내는 용도로만 써 봤고
    (이 저장소도, 참고한 dhkwon1122/ai-friendly-doc도 마찬가지), 첨부파일을
    실제로 지원하는지는 확인된 적이 없다. 아래 "attachments" JSON 필드명/
    구조(base64 인코딩)는 추측이다 - 실패하면(특히 요청 형식 오류) 이 함수가
    던지는 MailConfigError에 응답 본문이 그대로 담기니, 그 내용을 보고
    실제 스펙에 맞게 필드명/구조를 고쳐야 한다(Confluence Dep-Ticket 헤더를
    찾아낼 때처럼).
    """
    to_emails = [to] if isinstance(to, str) else list(to)
    if not to_emails:
        raise MailConfigError("받는 사람이 한 명 이상 필요합니다.")

    token = _require_env("MAIL_API_TOKEN")
    system_id = _require_env("MAIL_API_SYSTEM_ID")
    user_id = _require_env("MAIL_API_USER_ID")
    sender_address = os.environ.get("MAIL_API_SENDER_ADDRESS") or user_id

    base_url = (os.environ.get("MAIL_API_BASE_URL") or DEFAULT_MAIL_API_BASE_URL).rstrip("/")
    # requests의 params= kwarg로 넘기면 API가 쿼리스트링을 못 읽는다 - URL에
    # 직접 ?userId=...를 붙여서 보내야 한다. 파라미터 이름은 "userID"가
    # 아니라 "userId"여야 한다 - 대소문자가 다르면 API가 파라미터 자체를
    # 못 찾아 오류를 낸다.
    url = f"{base_url}/mails/send?userId={quote(user_id, safe='')}"

    # 사내 프록시(HTTP_PROXY/HTTPS_PROXY)가 이 사내 API 호출을 제대로
    # 못 넘겨서(예: 502 Bad Gateway) 실패하는 경우가 있다. MAIL_API_NO_PROXY=true면
    # 환경변수 프록시를 무시하고 이 호출만 직접 나가도록 강제한다.
    no_proxy = _parse_bool_env(os.environ.get("MAIL_API_NO_PROXY"), default=False)
    proxies = {"http": None, "https": None} if no_proxy else None
    verify_ssl = _parse_bool_env(os.environ.get("MAIL_API_VERIFY_SSL"), default=True)

    mail_payload = {
        "subject": subject,
        "contents": body_html,
        "contentType": "html",
        "docSecuType": "PERSONAL",
        "sender": {"emailAddress": sender_address},
        "recipients": [{"emailAddress": addr, "recipientType": "TO"} for addr in to_emails],
    }
    if attachments:
        mail_payload["attachments"] = [
            {
                "fileName": attachment["filename"],
                "contentType": attachment.get("content_type", "application/octet-stream"),
                "data": base64.b64encode(attachment["content"]).decode("ascii"),
            }
            for attachment in attachments
        ]
    # 한글이 섞이므로 인코딩을 명시적으로 UTF-8 바이트로 고정한다 (str로
    # 넘기면 라이브러리가 기본 인코딩을 쓸 수 있어서 깨질 수 있다).
    body = json.dumps(mail_payload, ensure_ascii=False).encode("utf-8")

    try:
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "System-ID": system_id,
                "Content-Type": "application/json;charset=utf-8",
            },
            data=body,
            timeout=DEFAULT_TIMEOUT_SECONDS,
            proxies=proxies,
            verify=verify_ssl,
        )
        response.raise_for_status()
    except requests.HTTPError as e:
        # 상태 코드만으로는 401/403의 실제 원인(토큰 만료, System-ID/userID
        # 불일치 등)을 알 수 없는 경우가 많다 - 응답 본문이 있으면 에러
        # 메시지에 그대로 포함해서 화면에서 바로 원인을 확인할 수 있게 한다.
        body_preview = (e.response.text or "").strip()[:500] if e.response is not None else ""
        _logger.warning("메일 발송 API 호출 실패: %s | 응답 본문: %s", e, body_preview)
        message = f"이메일 발송에 실패했습니다: {e}"
        if body_preview:
            message += f" | 응답 본문: {body_preview}"
        raise MailConfigError(message) from e
    except requests.RequestException as e:
        _logger.warning("메일 발송 API 호출 실패: %s", e)
        raise MailConfigError(f"이메일 발송에 실패했습니다: {e}") from e

    try:
        mail_id = response.json().get("mailId")
    except ValueError:
        mail_id = None
    _logger.info("메일 발송 성공 (mailId=%s)", mail_id)
