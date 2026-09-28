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
  #titles { height: 180px; resize: vertical; }
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
</style>
</head>
<body>
<div class="page">
  <header>
    <h1>Confluence 안건 보고 소스 생성기</h1>
    <p class="subtitle">안건 제목을 한 줄에 하나씩 입력하세요 (대략 10개 내외 권장).</p>
  </header>

  {% if message %}
    <div class="message {{ 'ok' if message_ok else 'error' }}">{{ message }}</div>
  {% endif %}

  <form method="post">
    <div class="card">
      <h2>안건</h2>
      <div class="field">
        <textarea id="titles" name="titles"
                  placeholder="예산안 승인&#10;채용 계획&#10;...">{{ titles_text }}</textarea>
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

    # 메일 제목은 안건 내용과 무관하게 오늘 날짜 기준 고정 문구다. 화면에
    # 미리 채워둬서 "소스를 고쳐도 제목은 안 바뀐다"는 걸 바로 보여준다 -
    # 다른 문구가 필요하면 이 칸에서 직접 덮어쓰면 된다.
    default_subject = build_email_subject()

    titles_text = ""
    selected_presets: List[str] = []
    extra_to = ""
    subject = default_subject
    output: Optional[str] = None
    message: Optional[str] = None
    message_ok = True

    if request.method == "POST":
        titles_text = request.form.get("titles", "")
        selected_presets = request.form.getlist("preset_to")
        extra_to = request.form.get("extra_to", "")
        subject = request.form.get("subject", "").strip() or default_subject
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
                            subject=subject,
                            body_html=build_agenda_email_html(output),
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
