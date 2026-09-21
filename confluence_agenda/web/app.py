"""안건 제목을 줄바꿈으로 한 번에 입력받는 간단한 웹 UI.

    python -m confluence_agenda.web

기본적으로 0.0.0.0:10001 에서 뜬다 (PORT 환경변수로 바꿀 수 있음).
CLI(confluence_agenda.cli)와 로직은 동일하고, 입력 방식만 다르다 — 한
텍스트영역에 안건 제목을 줄바꿈으로 여러 개 적으면 한 번에 소스가
만들어진다. MAIL_API_TOKEN 등이 설정돼 있으면 화면에서 바로 메일도
보낼 수 있다.
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
  input[type=email], input[type=text] { padding: 0.4rem; width: 320px; }
  label { display: inline-block; margin-right: 1.5rem; }
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
      <label>받는 사람 이메일
        <input type="email" name="to" value="{{ to }}" placeholder="someone@example.com">
      </label>
      <label>메일 제목(선택)
        <input type="text" name="subject" value="{{ subject }}" placeholder="생략 시 자동 생성">
      </label>
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


@app.route("/", methods=["GET", "POST"])
def index():
    titles_text = ""
    to = ""
    subject = ""
    output: Optional[str] = None
    message: Optional[str] = None
    message_ok = True

    if request.method == "POST":
        titles_text = request.form.get("titles", "")
        to = request.form.get("to", "").strip()
        subject = request.form.get("subject", "").strip()
        action = request.form.get("action")

        titles = _parse_titles(titles_text)
        if not titles:
            message, message_ok = "안건 제목을 한 줄에 하나씩 입력해주세요.", False
        else:
            items = [AgendaItem(title=title) for title in titles]
            output = build_agenda_page_body(items)

            if action == "send_mail":
                if not to:
                    message, message_ok = "받는 사람 이메일을 입력해주세요.", False
                elif not is_mail_configured():
                    message, message_ok = (
                        "MAIL_API_TOKEN 등 메일 API 환경변수가 설정되지 않아 메일을 보낼 수 없습니다.",
                        False,
                    )
                else:
                    try:
                        send_report_email(
                            to,
                            subject=subject or build_email_subject(items),
                            body_html=build_agenda_email_html(items),
                        )
                        message, message_ok = f"메일을 보냈습니다: {to}", True
                    except MailConfigError as e:
                        message, message_ok = f"메일 발송 실패: {e}", False

    return render_template_string(
        PAGE_TEMPLATE,
        titles_text=titles_text,
        to=to,
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
