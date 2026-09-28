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

import os
from typing import List, Optional

from flask import Flask, render_template_string, request

from ..builder import (
    AgendaItem,
    build_agenda_email_html,
    build_agenda_page_body,
    build_email_subject,
)
from ..mailer import MailConfigError, is_mail_configured, send_report_email
from .contacts import load_preset_contacts

app = Flask(__name__)

PAGE_TEMPLATE = """
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>Confluence 안건 보고 소스 생성기</title>
<style>
  body { font-family: -apple-system, "Malgun Gothic", sans-serif; max-width: 860px;
         margin: 2rem auto; padding: 0 1rem; color: #172b4d; }
  h1 { font-size: 1.4rem; }
  textarea { width: 100%; box-sizing: border-box; font-family: "SFMono-Regular", Consolas, monospace;
             font-size: 0.85rem; }
  #titles { height: 200px; }
  #output { height: 320px; margin-top: 0.5rem; }
  .row { margin: 1rem 0; }
  .hint { color: #6b778c; font-size: 0.85rem; }
  button { padding: 0.5rem 1rem; margin-right: 0.5rem; cursor: pointer; }
  .message { padding: 0.6rem 0.8rem; border-radius: 4px; margin: 1rem 0; }
  .message.ok { background: #e3fcef; color: #006644; }
  .message.error { background: #ffebe6; color: #bf2600; }
  input[type=text] { padding: 0.4rem; width: 100%; box-sizing: border-box; }
  label.field-label { display: block; font-size: 0.85rem; color: #42526e; margin-bottom: 0.3rem; }

  .chip-group { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 0.6rem; }
  .chip input[type=checkbox] { position: absolute; opacity: 0; pointer-events: none; }
  .chip label {
    display: inline-block; padding: 0.35rem 0.9rem; border-radius: 999px;
    border: 1px solid #dfe1e6; background: #f4f5f7; color: #42526e;
    font-size: 0.85rem; cursor: pointer; user-select: none;
  }
  .chip input[type=checkbox]:checked + label {
    background: #deebff; border-color: #4c9aff; color: #0052cc; font-weight: 600;
  }
  .chip input[type=checkbox]:focus-visible + label { outline: 2px solid #4c9aff; }
</style>
</head>
<body>
  <h1>Confluence 안건 보고 소스 생성기</h1>
  <p class="hint">안건 제목을 한 줄에 하나씩 입력하세요 (대략 10개 내외 권장).</p>

  {% if message %}
    <div class="message {{ 'ok' if message_ok else 'error' }}">{{ message }}</div>
  {% endif %}

  <form method="post">
    <div class="row">
      <textarea id="titles" name="titles"
                placeholder="예산안 승인&#10;채용 계획&#10;...">{{ titles_text }}</textarea>
    </div>

    {% if mail_configured %}
    <div class="row">
      <span class="field-label">받는 사람</span>
      <div class="chip-group">
        {% for name, email in preset_contacts %}
        <span class="chip">
          <input type="checkbox" id="preset-{{ loop.index }}" name="preset_to" value="{{ email }}"
                 {% if email in selected_presets %}checked{% endif %}>
          <label for="preset-{{ loop.index }}">{{ name }}</label>
        </span>
        {% endfor %}
      </div>
      <label class="field-label" for="extra_to">그 외 이메일 (콤마로 구분)</label>
      <input type="text" id="extra_to" name="extra_to" value="{{ extra_to }}"
             placeholder="someone@example.com, other@example.com">
    </div>
    <div class="row">
      <label class="field-label" for="subject">메일 제목(선택)</label>
      <input type="text" id="subject" name="subject" value="{{ subject }}" placeholder="생략 시 자동 생성">
    </div>
    {% endif %}

    <div class="row">
      <button type="submit" name="action" value="generate">소스 생성</button>
      {% if mail_configured %}
      <button type="submit" name="action" value="send_mail">메일로 보내기</button>
      {% endif %}
    </div>
  </form>

  {% if output %}
  <div class="row">
    <textarea id="output" readonly>{{ output }}</textarea>
    <div style="margin-top:0.5rem;">
      <button type="button" onclick="copyOutput()">소스 복사</button>
    </div>
  </div>
  {% endif %}

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


def _parse_titles(raw: str) -> List[str]:
    return [line.strip() for line in raw.splitlines() if line.strip()]


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

    titles_text = ""
    selected_presets: List[str] = []
    extra_to = ""
    subject = ""
    output: Optional[str] = None
    message: Optional[str] = None
    message_ok = True

    if request.method == "POST":
        titles_text = request.form.get("titles", "")
        selected_presets = request.form.getlist("preset_to")
        extra_to = request.form.get("extra_to", "")
        subject = request.form.get("subject", "").strip()
        action = request.form.get("action")

        titles = _parse_titles(titles_text)
        if not titles:
            message, message_ok = "안건 제목을 한 줄에 하나씩 입력해주세요.", False
        else:
            items = [AgendaItem(title=title) for title in titles]
            output = build_agenda_page_body(items)

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
                            subject=subject or build_email_subject(items),
                            body_html=build_agenda_email_html(items),
                        )
                        names = ", ".join(display_name(addr) for addr in recipients)
                        message, message_ok = f"메일을 보냈습니다: {names}", True
                    except MailConfigError as e:
                        message, message_ok = f"메일 발송 실패: {e}", False

    return render_template_string(
        PAGE_TEMPLATE,
        titles_text=titles_text,
        preset_contacts=preset_contacts,
        selected_presets=selected_presets,
        extra_to=extra_to,
        subject=subject,
        output=output,
        message=message,
        message_ok=message_ok,
        mail_configured=is_mail_configured(),
    )


def main() -> None:
    port = int(os.environ.get("PORT", "10001"))
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
