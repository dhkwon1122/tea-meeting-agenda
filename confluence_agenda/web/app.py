"""안건 제목을 줄바꿈으로 한 번에 입력받는 간단한 웹 UI.

    python -m confluence_agenda.web

기본적으로 0.0.0.0:10001 에서 뜬다 (PORT 환경변수로 바꿀 수 있음).
CLI(confluence_agenda.cli)와 로직은 동일하고, 입력 방식만 다르다 — 한
텍스트영역에 안건 제목을 줄바꿈으로 여러 개 적으면 한 번에 소스가
만들어진다. MAIL_API_TOKEN 등이 설정돼 있으면 화면에서 바로 메일도
보낼 수 있다 — 자주 보내는 대상은 태그(체크박스)로 골라 중복 선택하고,
그 외 주소는 자유 입력란에 콤마로 구분해서 추가한다.
"""

from __future__ import annotations

import io
import os
from typing import List, Optional
from urllib.parse import quote

from flask import Flask, redirect, render_template_string, request, send_file

from ..builder import (
    build_agenda_email_html,
    build_agenda_page_body,
    build_email_subject,
    parse_agenda_input,
)
from ..mailer import MailConfigError, is_mail_configured, send_report_email
from . import auth, confluence_credentials
from .contacts import load_preset_contacts
from .diagram import build_macro_diagram_html
from .docx_export import DocxExportUnavailable, convert_confluence_url_to_docx
from .docx_export import is_feature_available as docx_export_configured
from .docx_export import resolve_token as resolve_confluence_token

app = Flask(__name__)

# DATABASE_URL이 설정돼 있으면(= Researcher-board와 같은 app_users 테이블에
# 접근 가능하면) 로그인을 요구한다. 세션 쿠키 서명을 위해 SESSION_SECRET이
# 반드시 있어야 한다 - 없으면 재시작마다 세션이 전부 끊기거나(무작위 키),
# 공격자가 세션을 위조할 수 있는 약한 키를 쓰게 되므로 서버 기동 시(main())
# 확실히 막는다. 여기서는 값이 있으면 반영만 하고, 없다고 예외를 던지지는
# 않는다 - 이 모듈은 `import confluence_agenda.web.<아무거나>`만 해도 함께
# 로드되므로(패키지 __init__.py가 .app을 가져옴), 여기서 예외를 던지면
# auth_check.py 같은 진단 스크립트조차 실행 전에 막혀버린다.
if auth.is_configured():
    _session_secret = os.environ.get("SESSION_SECRET", "").strip()
    if _session_secret:
        app.secret_key = _session_secret

_LOGIN_EXEMPT_PATHS = {"/login", "/auth/login", "/logout"}


@app.before_request
def _require_login():
    if not auth.is_configured():
        return None
    if request.path in _LOGIN_EXEMPT_PATHS:
        return None
    if auth.get_current_user() is None:
        next_url = request.full_path if request.query_string else request.path
        return redirect(f"/login?next={quote(next_url)}")
    return None

PAGE_TEMPLATE = """
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Confluence 안건 보고 소스 생성기</title>
<style>
  :root {
    --blue: #1a73e8;
    --blue-dark: #1765cc;
    --blue-tint: #e8f0fe;
    --text: #202124;
    --text-muted: #5f6368;
    --border: #dadce0;
    --surface: #ffffff;
    --bg: #f8f9fa;
    --ok-bg: #e6f4ea;
    --ok-text: #137333;
    --error-bg: #fce8e6;
    --error-text: #c5221f;
  }
  * { box-sizing: border-box; }
  body {
    font-family: "Google Sans", Roboto, -apple-system, BlinkMacSystemFont, "Segoe UI",
                 "Malgun Gothic", Arial, sans-serif;
    background: var(--bg);
    color: var(--text);
    margin: 0;
    padding: 40px 16px 64px;
  }
  .page { max-width: 720px; margin: 0 auto; }
  header { margin-bottom: 28px; }
  h1 { font-size: 1.5rem; font-weight: 500; margin: 0 0 6px; }
  .subtitle { color: var(--text-muted); font-size: 0.9rem; margin: 0; }

  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 24px;
    margin-bottom: 20px;
  }
  .card h2 {
    font-size: 0.95rem; font-weight: 500; color: var(--text-muted);
    margin: 0 0 16px; text-transform: uppercase; letter-spacing: 0.03em;
  }

  .field { margin-bottom: 18px; }
  .field:last-child { margin-bottom: 0; }
  .field label { display: block; font-size: 0.85rem; color: var(--text-muted); margin-bottom: 6px; }

  textarea, input[type=text] {
    width: 100%; border: 1px solid var(--border); border-radius: 8px;
    padding: 10px 12px; font-size: 0.9rem; color: var(--text);
    font-family: inherit; background: var(--surface); transition: border-color .15s, box-shadow .15s;
  }
  textarea:focus, input[type=text]:focus {
    outline: none; border-color: var(--blue); box-shadow: 0 0 0 1px var(--blue);
  }
  #titles { height: 260px; resize: vertical; }
  #output {
    height: 300px; resize: vertical; font-family: "SFMono-Regular", Consolas, monospace;
    font-size: 0.8rem; background: var(--bg); color: var(--text-muted);
  }

  .chip-group { display: flex; flex-wrap: wrap; gap: 8px; }
  .chip input[type=checkbox] { position: absolute; opacity: 0; width: 0; height: 0; }
  .chip label {
    display: inline-flex; align-items: center; padding: 6px 14px; border-radius: 999px;
    border: 1px solid var(--border); background: var(--bg); color: var(--text-muted);
    font-size: 0.85rem; cursor: pointer; user-select: none; transition: all .15s; margin: 0;
  }
  .chip input[type=checkbox]:checked + label {
    background: var(--blue-tint); border-color: var(--blue); color: var(--blue); font-weight: 500;
  }
  .chip input[type=checkbox]:focus-visible + label { outline: 2px solid var(--blue); outline-offset: 1px; }

  .actions { display: flex; gap: 10px; margin-top: 4px; }
  button {
    font-family: inherit; font-size: 0.9rem; font-weight: 500; border-radius: 20px;
    padding: 10px 22px; cursor: pointer; border: 1px solid transparent; transition: all .15s;
  }
  button.primary { background: var(--blue); color: #fff; }
  button.primary:hover { background: var(--blue-dark); }
  button.secondary { background: var(--surface); color: var(--blue); border-color: var(--border); }
  button.secondary:hover { background: var(--blue-tint); border-color: var(--blue); }

  .message {
    padding: 12px 16px; border-radius: 8px; margin-bottom: 20px; font-size: 0.88rem;
  }
  .message.ok { background: var(--ok-bg); color: var(--ok-text); }
  .message.error { background: var(--error-bg); color: var(--error-text); }

  .output-actions { margin-top: 12px; }

  .diagram-item { margin-bottom: 24px; }
  .diagram-item:last-child { margin-bottom: 0; }
  .diagram-item-label {
    font-size: 0.75rem; font-weight: 600; color: var(--text-muted);
    text-transform: uppercase; letter-spacing: .04em; margin-bottom: 8px;
  }
  .diagram-box { border-radius: 10px; padding: 14px 16px; border: 1.5px solid; }
  .diagram-box--heading { background: var(--blue-tint); border-color: var(--blue); }
  .diagram-box--attachment { background: #f1f3f4; border-color: #5f6368; }
  .diagram-box-title {
    font-size: 0.72rem; font-weight: 600; color: var(--text-muted);
    text-transform: uppercase; letter-spacing: .03em; margin-bottom: 6px;
  }
  .diagram-box-heading-text { font-size: 0.95rem; font-weight: 500; margin-bottom: 8px; }
  .diagram-badges, .diagram-sub { display: flex; flex-wrap: wrap; gap: 6px; }
  .diagram-sub { margin-top: 10px; padding-top: 10px; border-top: 1px dashed #c4c7c5; }
  .diagram-badge {
    display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: 0.75rem;
    font-family: "SFMono-Regular", Consolas, monospace; word-break: break-word; max-width: 100%;
  }
  .diagram-badge--anchor { background: #fef7e0; color: #a35a00; }
  .diagram-badge--macro { background: #e8f0fe; color: #1a73e8; }
  .diagram-badge--include { background: #e6f4ea; color: #137333; }
  .diagram-badge--back { background: #fce8e6; color: #c5221f; }
  .diagram-arrow {
    display: flex; align-items: center; justify-content: center; gap: 8px;
    margin: 6px 0; color: var(--text-muted); font-size: 0.78rem;
  }
  .diagram-arrow-glyph { font-size: 1.1rem; }

  .header-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
  .user-info { font-size: 0.85rem; color: var(--text-muted); white-space: nowrap; }
  .user-info a { color: var(--blue); text-decoration: none; margin-left: 10px; }
  .user-info a:hover { text-decoration: underline; }
</style>
</head>
<body>
<div class="page">
  <header>
    <div class="header-row">
      <div>
        <h1>Confluence 안건 보고 소스 생성기</h1>
        <p class="subtitle">
          안건 제목을 한 줄씩 입력하세요(대략 10개 내외 권장). 이미 써둔 본문이
          있으면 제목 아래 줄에 이어서 적고, 다음 안건과는 빈 줄로 구분하세요
          — 본문을 안 쓴 안건은 기존처럼 자리표시자로 채워집니다.
        </p>
      </div>
      {% if current_user %}
      <div class="user-info">{{ current_user.display_name }}님<a href="/logout">로그아웃</a></div>
      {% endif %}
    </div>
  </header>

  {% if message %}
    <div class="message {{ 'ok' if message_ok else 'error' }}">{{ message }}</div>
  {% endif %}

  {% if docx_export_configured %}
  <div class="card">
    <h2>컨플루언스 → Word</h2>
    {% if docx_error %}<div class="message error">{{ docx_error }}</div>{% endif %}
    {% if docx_message %}<div class="message ok">{{ docx_message }}</div>{% endif %}

    {% if confluence_pat_storage_available %}
    <div class="field">
      <label>내 Confluence PAT(개인 액세스 토큰)</label>
      {% if confluence_pat_registered %}
        <p class="subtitle" style="margin:0 0 10px;">등록되어 있습니다. 변환에는 이 토큰이 쓰입니다.</p>
      {% else %}
        <p class="subtitle" style="margin:0 0 10px;">
          아직 등록되지 않았습니다 - 등록하지 않으면 관리자가 설정한 공용 토큰(있는 경우)으로 동작합니다.
        </p>
      {% endif %}
      <form method="post" action="/confluence-pat" style="display:flex; gap:8px; margin-bottom:8px;">
        <input type="text" name="confluence_pat" placeholder="새 PAT 입력"
               autocomplete="off" style="flex:1;">
        <button class="secondary" type="submit">{{ '업데이트' if confluence_pat_registered else '등록' }}</button>
      </form>
      {% if confluence_pat_registered %}
      <form method="post" action="/confluence-pat/delete">
        <button class="secondary" type="submit">내 PAT 삭제</button>
      </form>
      {% endif %}
    </div>
    <hr style="border:none; border-top:1px solid var(--border); margin:18px 0;">
    {% endif %}

    <form method="post" action="/confluence-to-docx">
      <div class="field">
        <label for="confluence_url">컨플루언스 페이지 URL</label>
        <input type="text" id="confluence_url" name="confluence_url"
               placeholder="https://wiki.사내주소/pages/viewpage.action?pageId=123456">
      </div>
      <button class="secondary" type="submit">Word로 변환해서 받기</button>
    </form>
  </div>
  {% endif %}

  <form method="post">
    <div class="card">
      <h2>안건</h2>
      <div class="field">
        <textarea id="titles" name="titles"
                  placeholder="예산안 승인&#10;부서별 예산안을 검토하고 승인합니다.&#10;&#10;채용 계획">{{ titles_text }}</textarea>
      </div>
    </div>

    {% if mail_configured %}
    <div class="card">
      <h2>메일 발송</h2>
      <div class="field">
        <label>받는 사람</label>
        <div class="chip-group">
          {% for name, email in preset_contacts %}
          <span class="chip">
            <input type="checkbox" id="preset-{{ loop.index }}" name="preset_to" value="{{ email }}"
                   {% if email in selected_presets %}checked{% endif %}>
            <label for="preset-{{ loop.index }}">{{ name }}</label>
          </span>
          {% endfor %}
        </div>
      </div>
      <div class="field">
        <label for="extra_to">그 외 이메일 (콤마로 구분)</label>
        <input type="text" id="extra_to" name="extra_to" value="{{ extra_to }}"
               placeholder="someone@example.com, other@example.com">
      </div>
      <div class="field">
        <label for="subject">메일 제목</label>
        <input type="text" id="subject" name="subject" value="{{ subject }}">
      </div>
    </div>
    {% endif %}

    <div class="actions">
      <button class="primary" type="submit" name="action" value="generate">소스 생성</button>
      {% if mail_configured %}
      <button class="secondary" type="submit" name="action" value="send_mail">메일로 보내기</button>
      {% endif %}
    </div>
  </form>

  {% if output %}
  <div class="card">
    <h2>생성된 소스</h2>
    <textarea id="output" readonly>{{ output }}</textarea>
    <div class="output-actions">
      <button class="secondary" type="button" onclick="copyOutput()">소스 복사</button>
    </div>
  </div>
  {% endif %}

  {% if diagram_html %}
  <div class="card">
    <h2>매크로 연결 구조</h2>
    <p class="subtitle" style="margin-bottom:16px;">
      안건마다 "① 제목 섹션"이 "(첨부N)" 링크로 "② 첨부 섹션"의 앵커로 이동하고,
      그 안에서 include로 하위 페이지를 불러온 뒤 "(돌아가기)" 링크로 다시
      제목 섹션의 앵커로 돌아갑니다.
    </p>
    {{ diagram_html | safe }}
  </div>
  {% endif %}
</div>

  <script>
    function copyOutput() {
      var ta = document.getElementById('output');
      ta.select();
      navigator.clipboard.writeText(ta.value).then(function () {
        alert('복사되었습니다. Confluence 편집기 "마크업 삽입"에 붙여넣으세요.');
      });
    }
  </script>
</body>
</html>
"""


def _parse_extra_emails(raw: str) -> List[str]:
    # 콤마와 줄바꿈 둘 다 구분자로 허용한다.
    return [part.strip() for chunk in raw.splitlines() for part in chunk.split(",") if part.strip()]


@app.route("/", methods=["GET", "POST"])
def index():
    # 매 요청마다 새로 읽어서, 서버 재시작 없이 contacts.json 수정이 바로 반영되게 한다.
    preset_contacts = load_preset_contacts()
    preset_email_to_name = {email: name for name, email in preset_contacts}

    def display_name(email: str) -> str:
        return preset_email_to_name.get(email, email)

    # 메일 제목은 안건 내용과 무관하게 오늘 날짜 기준 고정 문구다. 화면에
    # 미리 채워둬서 "소스를 고쳐도 제목은 안 바뀐다"는 걸 바로 보여준다 -
    # 다른 문구가 필요하면 이 칸에서 직접 덮어쓰면 된다.
    default_subject = build_email_subject()

    titles_text = ""
    selected_presets: List[str] = []
    extra_to = ""
    subject = default_subject
    output: Optional[str] = None
    diagram_html: Optional[str] = None
    message: Optional[str] = None
    message_ok = True

    if request.method == "POST":
        titles_text = request.form.get("titles", "")
        selected_presets = request.form.getlist("preset_to")
        extra_to = request.form.get("extra_to", "")
        subject = request.form.get("subject", "").strip() or default_subject
        action = request.form.get("action")

        items = parse_agenda_input(titles_text)
        if not items:
            message, message_ok = "안건 제목을 한 줄에 하나씩 입력해주세요.", False
        else:
            output = build_agenda_page_body(items)
            diagram_html = build_macro_diagram_html(items)

            if action == "send_mail":
                # dict.fromkeys로 순서를 유지한 채 중복 제거.
                recipients = list(dict.fromkeys(selected_presets + _parse_extra_emails(extra_to)))
                if not recipients:
                    message, message_ok = "받는 사람을 한 명 이상 선택하거나 입력해주세요.", False
                elif not is_mail_configured():
                    message, message_ok = (
                        "MAIL_API_TOKEN 등 메일 API 환경변수가 설정되지 않아 메일을 보낼 수 없습니다.",
                        False,
                    )
                else:
                    try:
                        send_report_email(
                            recipients,
                            subject=subject,
                            body_html=build_agenda_email_html(output),
                        )
                        names = ", ".join(display_name(addr) for addr in recipients)
                        message, message_ok = f"메일을 보냈습니다: {names}", True
                    except MailConfigError as e:
                        message, message_ok = f"메일 발송 실패: {e}", False

    current_user = auth.get_current_user()
    pat_storage_available = confluence_credentials.is_configured()
    pat_registered = bool(
        current_user and pat_storage_available and confluence_credentials.status(current_user["user_id"])
    )

    return render_template_string(
        PAGE_TEMPLATE,
        titles_text=titles_text,
        preset_contacts=preset_contacts,
        selected_presets=selected_presets,
        extra_to=extra_to,
        subject=subject,
        output=output,
        diagram_html=diagram_html,
        message=message,
        message_ok=message_ok,
        mail_configured=is_mail_configured(),
        current_user=current_user,
        docx_export_configured=docx_export_configured(),
        docx_error=request.args.get("docx_error"),
        docx_message=request.args.get("docx_message"),
        confluence_pat_storage_available=pat_storage_available,
        confluence_pat_registered=pat_registered,
    )


@app.route("/confluence-to-docx", methods=["POST"])
def confluence_to_docx():
    url = request.form.get("confluence_url", "").strip()
    if not url:
        return redirect(f"/?docx_error={quote('컨플루언스 페이지 URL을 입력해주세요.')}")

    current_user = auth.get_current_user()
    token = resolve_confluence_token(current_user["user_id"] if current_user else None)
    if not token:
        return redirect(
            f"/?docx_error={quote('Confluence 개인 액세스 토큰(PAT)이 없습니다. 아래에서 내 PAT을 등록해주세요.')}"
        )

    try:
        data, filename = convert_confluence_url_to_docx(url, token=token)
    except DocxExportUnavailable as e:
        return redirect(f"/?docx_error={quote(str(e))}")
    except Exception as e:
        # 페이지를 못 찾음/권한 없음/네트워크 오류 등 docx_export.py가 던지는 오류.
        return redirect(f"/?docx_error={quote(f'변환 실패: {e}')}")

    return send_file(
        io.BytesIO(data),
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@app.route("/confluence-pat", methods=["POST"])
def save_confluence_pat():
    current_user = auth.get_current_user()
    if current_user is None:
        return redirect(f"/?docx_error={quote('로그인이 필요합니다.')}")

    pat = request.form.get("confluence_pat", "").strip()
    if not pat:
        return redirect(f"/?docx_error={quote('PAT을 입력해주세요.')}")

    try:
        confluence_credentials.set_pat(current_user["user_id"], pat)
    except confluence_credentials.CredentialStorageUnavailable as e:
        return redirect(f"/?docx_error={quote(str(e))}")

    return redirect(f"/?docx_message={quote('내 Confluence PAT을 저장했습니다.')}")


@app.route("/confluence-pat/delete", methods=["POST"])
def delete_confluence_pat():
    current_user = auth.get_current_user()
    if current_user is None:
        return redirect(f"/?docx_error={quote('로그인이 필요합니다.')}")

    confluence_credentials.delete_pat(current_user["user_id"])
    return redirect(f"/?docx_message={quote('내 Confluence PAT을 삭제했습니다.')}")


LOGIN_PAGE_TEMPLATE = """
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>로그인 - Confluence 안건 보고 소스 생성기</title>
<style>
  :root { --blue: #1a73e8; --blue-dark: #1765cc; --text: #202124; --text-muted: #5f6368;
          --border: #dadce0; --surface: #ffffff; --bg: #f8f9fa; --error-bg: #fce8e6; --error-text: #c5221f; }
  * { box-sizing: border-box; }
  body {
    font-family: "Google Sans", Roboto, -apple-system, BlinkMacSystemFont, "Segoe UI",
                 "Malgun Gothic", Arial, sans-serif;
    background: var(--bg); color: var(--text); margin: 0;
    min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 16px;
  }
  .card {
    background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
    padding: 32px; width: 100%; max-width: 360px;
  }
  h1 { font-size: 1.2rem; font-weight: 500; margin: 0 0 4px; }
  .subtitle { color: var(--text-muted); font-size: 0.85rem; margin: 0 0 24px; }
  .field { margin-bottom: 16px; }
  .field label { display: block; font-size: 0.85rem; color: var(--text-muted); margin-bottom: 6px; }
  input[type=text], input[type=password] {
    width: 100%; border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px;
    font-size: 0.9rem; font-family: inherit;
  }
  input[type=text]:focus, input[type=password]:focus {
    outline: none; border-color: var(--blue); box-shadow: 0 0 0 1px var(--blue);
  }
  button {
    width: 100%; font-family: inherit; font-size: 0.9rem; font-weight: 500; border-radius: 20px;
    padding: 10px 22px; cursor: pointer; border: none; background: var(--blue); color: #fff;
    margin-top: 8px;
  }
  button:hover { background: var(--blue-dark); }
  .message { padding: 10px 14px; border-radius: 8px; margin-bottom: 18px; font-size: 0.85rem;
             background: var(--error-bg); color: var(--error-text); }
</style>
</head>
<body>
  <form class="card" method="post" action="/auth/login">
    <h1>로그인</h1>
    <p class="subtitle">Researcher-board 계정으로 로그인하세요.</p>
    {% if error %}<div class="message">{{ error }}</div>{% endif %}
    <input type="hidden" name="next" value="{{ next_url }}">
    <div class="field">
      <label for="user_id">아이디</label>
      <input type="text" id="user_id" name="user_id" autofocus required>
    </div>
    <div class="field">
      <label for="password">비밀번호</label>
      <input type="password" id="password" name="password" required>
    </div>
    <button type="submit">로그인</button>
  </form>
</body>
</html>
"""

_LOGIN_ERROR_MESSAGES = {
    "invalid": "아이디 또는 비밀번호가 올바르지 않습니다.",
    "must_change_password": "임시 비밀번호 상태입니다. Researcher-board에서 먼저 비밀번호를 변경해주세요.",
}


@app.route("/login", methods=["GET"])
def login_page():
    next_url = request.args.get("next") or "/"
    error_code = request.args.get("error")
    error = _LOGIN_ERROR_MESSAGES.get(error_code) if error_code else None
    return render_template_string(LOGIN_PAGE_TEMPLATE, next_url=next_url, error=error)


@app.route("/auth/login", methods=["POST"])
def auth_login():
    user_id = request.form.get("user_id", "").strip()
    password = request.form.get("password", "")
    next_url = request.form.get("next") or "/"

    try:
        user = auth.authenticate(user_id, password)
    except auth.PasswordChangeRequired:
        return redirect(f"/login?error=must_change_password&next={quote(next_url)}")

    if user is None:
        return redirect(f"/login?error=invalid&next={quote(next_url)}")

    auth.set_session(user)
    return redirect(next_url)


@app.route("/logout")
def logout():
    auth.clear_session()
    return redirect("/login")


def main() -> None:
    if auth.is_configured() and not app.secret_key:
        raise RuntimeError(
            "DATABASE_URL이 설정되어 로그인이 필요한데 SESSION_SECRET이 없습니다. "
            ".env.example을 참고해 설정해주세요."
        )
    port = int(os.environ.get("PORT", "10001"))
    # threaded=True: 컨플루언스 -> Word 변환은 몇 초~몇십 초 걸릴 수 있어서,
    # 그동안 다른 요청(안건 소스 생성 등)이 막히지 않게 한다.
    app.run(host="0.0.0.0", port=port, threaded=True)


if __name__ == "__main__":
    main()
