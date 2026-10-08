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

import html
import io
import json
import os
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple
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
from .docx_export import DocxExportUnavailable, convert_confluence_url_to_docx, create_agenda_page
from .docx_export import diagnose_connection as diagnose_confluence_connection
from .docx_export import docx_bytes_to_preview_html
from .docx_export import is_feature_available as docx_export_configured
from .docx_export import load_agenda_page_for_editing
from .docx_export import resolve_token as resolve_confluence_token
from .docx_export import update_agenda_page
from .docx_export import verify_token as verify_confluence_token

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

_APP_STYLE = """
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

  /* 버튼처럼 보이는 링크(<a>) - 다운로드/미리보기처럼 새 탭/다른 URL로 보내는
     동작은 버튼보다 링크가 더 맞아서, button.primary/secondary와 같은
     모양을 <a>에도 쓸 수 있게 별도 클래스로 둔다. */
  .btn-primary, .btn-secondary {
    display: inline-flex; align-items: center; text-decoration: none;
    font-family: inherit; font-size: 0.9rem; font-weight: 500; border-radius: 20px;
    padding: 10px 22px; cursor: pointer; border: 1px solid transparent; transition: all .15s;
  }
  .btn-primary { background: var(--blue); color: #fff; }
  .btn-primary:hover { background: var(--blue-dark); }
  .btn-secondary { background: var(--surface); color: var(--blue); border-color: var(--border); }
  .btn-secondary:hover { background: var(--blue-tint); border-color: var(--blue); }

  .message {
    padding: 12px 16px; border-radius: 8px; margin-bottom: 20px; font-size: 0.88rem;
    white-space: pre-wrap;
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

  .nav-row { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 20px; }
  .nav-link {
    display: inline-flex; align-items: center; gap: 6px;
    font-family: inherit; font-size: 0.9rem; font-weight: 500; border-radius: 20px;
    padding: 10px 22px; cursor: pointer; border: 1px solid var(--border);
    background: var(--surface); color: var(--blue); text-decoration: none; transition: all .15s;
  }
  .nav-link:hover { background: var(--blue-tint); border-color: var(--blue); }
  .nav-link.active { background: var(--blue); color: #fff; border-color: var(--blue); }
  .back-link { font-size: 0.85rem; color: var(--blue); text-decoration: none; }
  .back-link:hover { text-decoration: underline; }

  .choice-grid { display: flex; gap: 16px; flex-wrap: wrap; }
  .choice-card {
    flex: 1 1 240px; display: flex; flex-direction: column; gap: 8px;
    background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
    padding: 28px 24px; text-decoration: none; color: var(--text); transition: all .15s;
  }
  .choice-card:hover {
    border-color: var(--blue); box-shadow: 0 2px 10px rgba(26, 115, 232, 0.12);
    transform: translateY(-1px);
  }
  .choice-icon { font-size: 2rem; }
  .choice-title { font-size: 1.05rem; font-weight: 600; }
  .choice-desc { font-size: 0.85rem; color: var(--text-muted); }
"""

# 두 기능(안건 페이지 생성/워드 파일 변환) 전환 버튼 - 메인 화면뿐 아니라
# 어느 화면에서든 맨 위에서 바로 전환할 수 있어야 한다는 요청으로, 각
# 페이지 템플릿 맨 위에 그대로 이어붙인다(_APP_STYLE과 같은 방식).
_TOP_NAV_HTML = """
  <div class="nav-row">
    <a class="nav-link {{ 'active' if active_nav == 'agenda' else '' }}" href="/agenda">📝 안건 페이지 생성</a>
    {% if docx_export_configured %}
    <a class="nav-link {{ 'active' if active_nav == 'agenda_edit' else '' }}" href="/agenda/edit">✏️ 안건 수정</a>
    <a class="nav-link {{ 'active' if active_nav == 'docx' else '' }}" href="/confluence-to-docx">📄 워드 파일 변환</a>
    {% endif %}
  </div>
"""

HOME_PAGE_TEMPLATE = """
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Confluence 안건 도우미</title>
<style>""" + _APP_STYLE + """</style>
</head>
<body>
<div class="page">""" + _TOP_NAV_HTML + """
  <header>
    <div class="header-row">
      <div>
        <h1>Confluence 안건 도우미</h1>
        <p class="subtitle">원하는 기능을 선택하세요.</p>
      </div>
      {% if current_user %}
      <div class="user-info">{{ current_user.display_name }}님<a href="/logout">로그아웃</a></div>
      {% endif %}
    </div>
  </header>

  <div class="choice-grid">
    <a class="choice-card" href="/agenda">
      <div class="choice-icon">📝</div>
      <div class="choice-title">안건 페이지 생성</div>
      <div class="choice-desc">
        안건 제목을 입력해 Confluence 안건 보고 소스를 만들고, 바로 메일로 보냅니다.
      </div>
    </a>
    {% if docx_export_configured %}
    <a class="choice-card" href="/confluence-to-docx">
      <div class="choice-icon">📄</div>
      <div class="choice-title">워드 파일 변환</div>
      <div class="choice-desc">Confluence 페이지 URL을 Word(.docx) 파일로 변환합니다.</div>
    </a>
    {% endif %}
  </div>
</div>
</body>
</html>
"""

PAGE_TEMPLATE = """
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Confluence 안건 보고 소스 생성기</title>
<style>""" + _APP_STYLE + """</style>
</head>
<body>
<div class="page">""" + _TOP_NAV_HTML + """
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

  <form method="post">
    <div class="card">
      <h2>안건</h2>
      <div class="field">
        <textarea id="titles" name="titles"
                  placeholder="예산안 승인&#10;부서별 예산안을 검토하고 승인합니다.&#10;&#10;채용 계획">{{ titles_text }}</textarea>
      </div>
    </div>

    {% if docx_export_configured %}
    <div class="card">
      <h2>Confluence에 자동 생성</h2>
      <p class="subtitle" style="margin:0 0 16px;">
        위 안건으로 새 안건 페이지를 만들고, 안건별 상세 페이지도 그 하위
        페이지로 한 번에 만듭니다 - "템플릿에서 페이지 만들기" 버튼을 안건
        수만큼 손으로 누를 필요가 없어집니다.
      </p>
      <div class="field">
        <label for="parent_url">상위 페이지 URL</label>
        <input type="text" id="parent_url" name="parent_url" value="{{ parent_url }}"
               placeholder="https://wiki.사내주소/pages/viewpage.action?pageId=123456">
      </div>
      <div class="field">
        <label for="page_title">새 안건 페이지 제목</label>
        <input type="text" id="page_title" name="page_title" value="{{ page_title }}"
               placeholder="9.21(월) 스탭팀장 미팅 피플팀 안건">
      </div>
      <div class="field">
        <label for="mirror_parent_url">미러링 페이지의 상위 페이지 URL</label>
        <input type="text" id="mirror_parent_url" name="mirror_parent_url" value="{{ mirror_parent_url }}"
               placeholder="https://wiki.사내주소/pages/viewpage.action?pageId=654321">
      </div>
    </div>
    {% endif %}

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
      {% if docx_export_configured %}
      <button class="primary" type="submit" name="action" value="publish_confluence">Confluence에 자동 생성</button>
      {% endif %}
      <button class="secondary" type="submit" name="action" value="generate">소스 생성</button>
      {% if mail_configured %}
      <button class="secondary" type="submit" name="action" value="send_mail">메일로 보내기</button>
      {% endif %}
    </div>
  </form>

  {% if publish_result %}
  <div class="card">
    <h2>생성 결과</h2>
    <p>
      <a class="btn-primary" href="{{ publish_result.url }}" target="_blank">
        {{ publish_result.title }} 열기
      </a>
    </p>
    <ul>
      {% for detail in publish_result.detail_pages %}
      <li>
        {% if detail.ok %}
          ✅ <a href="{{ detail.url }}" target="_blank">{{ detail.title }}</a>
        {% else %}
          ❌ {{ detail.title }} - {{ detail.error }}
        {% endif %}
      </li>
      {% endfor %}
    </ul>
    {% if publish_result.mirror_page %}
    <p>
      미러 페이지:
      {% if publish_result.mirror_page.ok %}
        ✅ <a href="{{ publish_result.mirror_page.url }}" target="_blank">{{ publish_result.mirror_page.title }}</a>
      {% else %}
        ❌ {{ publish_result.mirror_page.title }} - {{ publish_result.mirror_page.error }}
      {% endif %}
    </p>
    {% endif %}
  </div>
  {% endif %}

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

AGENDA_EDIT_PAGE_TEMPLATE = """
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>안건 수정</title>
<style>""" + _APP_STYLE + """</style>
</head>
<body>
<div class="page">""" + _TOP_NAV_HTML + """
  <header>
    <div class="header-row">
      <div>
        <h1>안건 수정</h1>
        <p class="subtitle">
          이미 만든 안건 페이지의 URL을 넣어 불러온 뒤, 제목을 고치거나
          추가/삭제하고 저장하세요.
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

  <form method="post">
    <div class="card">
      <h2>안건 페이지</h2>
      <div class="field">
        <label for="page_url">안건 페이지 URL</label>
        <input type="text" id="page_url" name="page_url" value="{{ page_url }}"
               placeholder="https://wiki.사내주소/pages/viewpage.action?pageId=123456">
      </div>
      <button class="secondary" type="submit" name="action" value="load">불러오기</button>
    </div>

    {% if loaded %}
    <div class="card">
      <h2>{{ loaded.title }}</h2>
      <p class="subtitle" style="margin:0 0 16px;">
        제목을 고치거나 "삭제"를 체크한 뒤 저장하세요. 아래 빈 칸에 제목을
        쓰면 새 안건으로 추가됩니다. 삭제된 항목의 상세 페이지는 완전
        삭제가 아니라 휴지통으로 이동합니다(복구 가능).
      </p>
      <div id="agenda-existing-rows">
        {% for item in loaded['items'] %}
        <div class="field" style="display:flex; gap:8px; align-items:center;">
          <input type="hidden" name="orig_index" value="{{ item.index }}">
          <input type="text" name="title" value="{{ item.title }}" style="flex:1;">
          <label style="display:flex; align-items:center; gap:4px; white-space:nowrap; font-size:0.85rem; color:var(--text-muted);">
            <input type="checkbox" name="delete_orig_index" value="{{ item.index }}"> 삭제
          </label>
        </div>
        {% endfor %}
      </div>
      <div id="agenda-new-rows">
        {% for _ in range(3) %}
        <div class="field" style="display:flex; gap:8px; align-items:center;">
          <input type="hidden" name="orig_index" value="">
          <input type="text" name="title" value="" placeholder="새 안건 제목" style="flex:1;">
        </div>
        {% endfor %}
      </div>
      <button class="secondary" type="button" onclick="addAgendaRow()">+ 항목 추가</button>
      <div class="actions" style="margin-top:16px;">
        <button class="primary" type="submit" name="action" value="save">저장</button>
      </div>
    </div>
    {% endif %}
  </form>

  {% if save_result %}
  <div class="card">
    <h2>저장 결과</h2>
    <p>
      <a class="btn-primary" href="{{ save_result.url }}" target="_blank">
        {{ save_result.title }} 열기
      </a>
    </p>
    <ul>
      {% for detail in save_result.detail_pages %}
      <li>
        {% if detail.ok %}
          ✅ <a href="{{ detail.url }}" target="_blank">{{ detail.title }}</a>
        {% else %}
          ❌ {{ detail.title }} - {{ detail.error }}
        {% endif %}
      </li>
      {% endfor %}
      {% for trashed in save_result.trashed_detail_pages %}
      <li>
        {% if trashed.ok %}
          🗑️ {{ trashed.title }} - 휴지통으로 이동했습니다
        {% else %}
          ❌ {{ trashed.title }} - 휴지통으로 이동 실패: {{ trashed.error }}
        {% endif %}
      </li>
      {% endfor %}
    </ul>
  </div>
  {% endif %}
</div>

  <script>
    function addAgendaRow() {
      var container = document.getElementById('agenda-new-rows');
      var row = document.createElement('div');
      row.className = 'field';
      row.style.cssText = 'display:flex; gap:8px; align-items:center;';
      row.innerHTML =
        '<input type="hidden" name="orig_index" value="">' +
        '<input type="text" name="title" value="" placeholder="새 안건 제목" style="flex:1;">';
      container.appendChild(row);
    }
  </script>
</body>
</html>
"""

CONFLUENCE_DOCX_PAGE_TEMPLATE = """
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Confluence → Word 변환기</title>
<style>""" + _APP_STYLE + """</style>
</head>
<body>
<div class="page">""" + _TOP_NAV_HTML + """
  <header>
    <div class="header-row">
      <div>
        <h1>Confluence → Word 변환기</h1>
        <p class="subtitle">
          컨플루언스 페이지 URL을 넣으면 .docx로 변환해 바로 받습니다.
        </p>
      </div>
      {% if current_user %}
      <div class="user-info">{{ current_user.display_name }}님<a href="/logout">로그아웃</a></div>
      {% endif %}
    </div>
  </header>

  {% if docx_error %}<div class="message error">{{ docx_error }}</div>{% endif %}
  {% if docx_message %}<div class="message ok">{{ docx_message }}</div>{% endif %}

  {% if confluence_pat_storage_available %}
  <div class="card">
    <h2>내 Confluence PAT</h2>
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
  {% endif %}

  <div class="card">
    <h2>변환</h2>
    <form id="convert-form">
      <div class="field">
        <label for="confluence_url">컨플루언스 페이지 URL</label>
        <input type="text" id="confluence_url" name="confluence_url"
               placeholder="https://wiki.사내주소/pages/viewpage.action?pageId=123456">
      </div>
      <button class="primary" type="submit" id="convert-submit">Word로 변환하기</button>
      <p id="convert-progress" class="subtitle" style="margin:12px 0 0; display:none;"></p>
    </form>

    <div id="convert-result" style="display:none; margin-top:18px; padding-top:18px; border-top:1px solid var(--border);">
      <div style="display:flex; gap:10px; flex-wrap:wrap;">
        <a id="result-download" class="btn-primary" href="#">다운로드</a>
        <a id="result-preview" class="btn-secondary" href="#" target="_blank">다운로드 없이 미리보기</a>
      </div>
      {% if mail_configured %}
      <form id="email-form" style="display:flex; gap:8px; margin-top:12px;">
        <input type="email" id="email_to" placeholder="메일로 받을 사람 이메일" style="flex:1;" required>
        <button class="secondary" type="submit">메일로 보내기</button>
      </form>
      <p id="email-result" class="subtitle" style="margin:8px 0 0; display:none;"></p>
      {% endif %}
    </div>
  </div>
</div>

<script>
  (function () {
    var form = document.getElementById('convert-form');
    var submitBtn = document.getElementById('convert-submit');
    var progressEl = document.getElementById('convert-progress');
    var resultEl = document.getElementById('convert-result');
    var downloadLink = document.getElementById('result-download');
    var previewLink = document.getElementById('result-preview');
    var emailForm = document.getElementById('email-form');
    var emailResultEl = document.getElementById('email-result');

    function showProgress(text) {
      progressEl.textContent = text;
      progressEl.style.display = 'block';
    }

    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      submitBtn.disabled = true;
      resultEl.style.display = 'none';
      showProgress('시작하는 중...');

      fetch('/confluence-to-docx/start', { method: 'POST', body: new FormData(form) })
        .then(function (resp) { return resp.json().then(function (body) { return [resp.ok, body]; }); })
        .then(function (result) {
          var ok = result[0], body = result[1];
          if (!ok) {
            showProgress(body.error || '시작하지 못했습니다.');
            submitBtn.disabled = false;
            return;
          }
          poll(body.job_id);
        })
        .catch(function (err) {
          showProgress('요청 실패: ' + err);
          submitBtn.disabled = false;
        });
    });

    function poll(jobId) {
      fetch('/confluence-to-docx/status/' + jobId)
        .then(function (resp) { return resp.json(); })
        .then(function (status) {
          if (status.status === 'running') {
            showProgress(status.message);
            setTimeout(function () { poll(jobId); }, 800);
          } else if (status.status === 'done') {
            showProgress('완료');
            downloadLink.href = '/confluence-to-docx/download/' + jobId;
            previewLink.href = '/confluence-to-docx/preview/' + jobId;
            if (emailForm) {
              emailForm.onsubmit = function (ev) {
                ev.preventDefault();
                emailResultEl.textContent = '보내는 중...';
                emailResultEl.style.display = 'block';
                var body = new FormData();
                body.append('email_to', document.getElementById('email_to').value);
                fetch('/confluence-to-docx/email/' + jobId, { method: 'POST', body: body })
                  .then(function (resp) { return resp.json().then(function (b) { return [resp.ok, b]; }); })
                  .then(function (result) {
                    var ok = result[0], b = result[1];
                    emailResultEl.textContent = ok ? b.message : (b.error || '메일 발송에 실패했습니다.');
                  })
                  .catch(function (err) { emailResultEl.textContent = '요청 실패: ' + err; });
              };
            }
            resultEl.style.display = 'block';
            submitBtn.disabled = false;
          } else {
            showProgress(status.message || '변환에 실패했습니다.');
            submitBtn.disabled = false;
          }
        })
        .catch(function (err) {
          showProgress('상태 확인 실패: ' + err);
          submitBtn.disabled = false;
        });
    }
  })();
</script>
</body>
</html>
"""

CONFLUENCE_DOCX_PREVIEW_TEMPLATE = """
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>미리보기 - {{ filename }}</title>
<style>""" + _APP_STYLE + """
  .docx-preview {
    background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
    padding: 32px; font-size: 0.95rem; line-height: 1.5; color: var(--text);
  }
  .docx-preview .docx-title { font-size: 1.3rem; font-weight: 700; text-align: center;
    text-decoration: underline; margin: 0 0 16px; }
  .docx-preview .docx-heading { font-weight: 700; }
  .docx-preview .docx-p { margin: 2px 0; }
  .docx-table { border-collapse: collapse; width: 100%; margin: 10px 0; }
  .docx-table td { border: 1px solid var(--border); padding: 6px 10px; font-size: 0.85rem; text-align: center; }
</style>
</head>
<body>
<div class="page">
  <header>
    <h1>미리보기</h1>
    <p class="subtitle">
      {{ filename }}
      <a class="back-link" href="/confluence-to-docx">← 변환기로 돌아가기</a>
    </p>
  </header>
  <div style="margin-bottom:16px;">
    <a class="btn-primary" href="/confluence-to-docx/download/{{ job_id }}">다운로드</a>
  </div>
  {{ preview_html | safe }}
</div>
</body>
</html>
"""


def _parse_extra_emails(raw: str) -> List[str]:
    # 콤마와 줄바꿈 둘 다 구분자로 허용한다.
    return [part.strip() for chunk in raw.splitlines() for part in chunk.split(",") if part.strip()]


# 안건/미러 페이지의 상위 페이지는 거의 매번 똑같은 곳이라서(사용자 확인:
# "상위 페이지는 거의 고정이야"), 지난번에 성공적으로 만들었을 때 쓴 URL을
# 그대로 기억해 다음 화면에 기본값으로 채워준다. contacts.json과 같은
# 방식(파일 기반, 저장소 루트, 환경변수로 경로 재정의 가능)이다.
_AGENDA_PUBLISH_STATE_PATH = Path(
    os.environ.get("AGENDA_PUBLISH_STATE_FILE")
    or Path(__file__).resolve().parent.parent.parent / "agenda_publish_state.json"
)


def _load_last_publish_parents() -> dict:
    try:
        raw = json.loads(_AGENDA_PUBLISH_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_last_publish_parents(parent_url: str, mirror_parent_url: str) -> None:
    try:
        _AGENDA_PUBLISH_STATE_PATH.write_text(
            json.dumps(
                {"parent_url": parent_url, "mirror_parent_url": mirror_parent_url}, ensure_ascii=False
            ),
            encoding="utf-8",
        )
    except OSError:
        pass  # 기본값 저장에 실패해도 이번 생성 자체는 이미 끝났다 - 조용히 넘어간다.


@app.route("/", methods=["GET"])
def home():
    return render_template_string(
        HOME_PAGE_TEMPLATE,
        current_user=auth.get_current_user(),
        docx_export_configured=docx_export_configured(),
        active_nav=None,
    )


@app.route("/agenda", methods=["GET", "POST"])
def agenda_page():
    # 매 요청마다 새로 읽어서, 서버 재시작 없이 contacts.json 수정이 바로 반영되게 한다.
    preset_contacts = load_preset_contacts()
    preset_email_to_name = {email: name for name, email in preset_contacts}

    def display_name(email: str) -> str:
        return preset_email_to_name.get(email, email)

    # 메일 제목은 안건 내용과 무관하게 오늘 날짜 기준 고정 문구다. 화면에
    # 미리 채워둬서 "소스를 고쳐도 제목은 안 바뀐다"는 걸 바로 보여준다 -
    # 다른 문구가 필요하면 이 칸에서 직접 덮어쓰면 된다.
    default_subject = build_email_subject()

    # 안건/미러 페이지의 상위 페이지는 거의 매번 똑같은 곳이라서, 지난번에
    # 성공적으로 만들었을 때 쓴 URL을 기본값으로 미리 채워둔다.
    last_parents = _load_last_publish_parents()

    titles_text = ""
    selected_presets: List[str] = []
    extra_to = ""
    subject = default_subject
    parent_url = last_parents.get("parent_url", "")
    mirror_parent_url = last_parents.get("mirror_parent_url", "")
    page_title = ""
    output: Optional[str] = None
    diagram_html: Optional[str] = None
    publish_result: Optional[dict] = None
    message: Optional[str] = None
    message_ok = True

    if request.method == "POST":
        titles_text = request.form.get("titles", "")
        selected_presets = request.form.getlist("preset_to")
        extra_to = request.form.get("extra_to", "")
        subject = request.form.get("subject", "").strip() or default_subject
        parent_url = request.form.get("parent_url", "").strip()
        mirror_parent_url = request.form.get("mirror_parent_url", "").strip()
        page_title = request.form.get("page_title", "").strip()
        action = request.form.get("action")

        items = parse_agenda_input(titles_text)
        if not items:
            message, message_ok = "안건 제목을 한 줄에 하나씩 입력해주세요.", False
        else:
            # publish_confluence는 API로 직접 만들어서, 손으로 붙여넣을 소스/버튼
            # 안내가 필요 없다 - 오히려 "이미 만든 페이지를 또 손으로 만들라"는
            # 것처럼 보여 혼란스럽다.
            if action != "publish_confluence":
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

            elif action == "publish_confluence":
                if not parent_url:
                    message, message_ok = "상위 페이지 URL을 입력해주세요.", False
                elif not page_title:
                    message, message_ok = "새 안건 페이지 제목을 입력해주세요.", False
                elif not mirror_parent_url:
                    message, message_ok = "미러링할 페이지의 상위 페이지 URL을 입력해주세요.", False
                else:
                    current_user = auth.get_current_user()
                    token = resolve_confluence_token(current_user["user_id"] if current_user else None)
                    if not token:
                        message, message_ok = (
                            "Confluence 개인 액세스 토큰(PAT)이 없습니다. "
                            "워드 변환기 화면에서 내 PAT을 등록해주세요.",
                            False,
                        )
                    else:
                        try:
                            publish_result = create_agenda_page(
                                parent_url,
                                page_title,
                                items,
                                token=token,
                                mirror_parent_url=mirror_parent_url,
                            )
                            _save_last_publish_parents(parent_url, mirror_parent_url)
                            message, message_ok = (
                                f"'{publish_result['title']}' 안건 페이지를 만들었습니다.",
                                True,
                            )
                        except Exception as e:
                            message, message_ok = f"안건 페이지 생성 실패: {e}", False

    return render_template_string(
        PAGE_TEMPLATE,
        titles_text=titles_text,
        preset_contacts=preset_contacts,
        selected_presets=selected_presets,
        extra_to=extra_to,
        subject=subject,
        parent_url=parent_url,
        mirror_parent_url=mirror_parent_url,
        page_title=page_title,
        output=output,
        diagram_html=diagram_html,
        publish_result=publish_result,
        message=message,
        message_ok=message_ok,
        mail_configured=is_mail_configured(),
        docx_export_configured=docx_export_configured(),
        current_user=auth.get_current_user(),
        active_nav="agenda",
    )


@app.route("/agenda/edit", methods=["GET", "POST"])
def agenda_edit_page():
    if not docx_export_configured():
        return redirect("/")

    page_url = ""
    loaded: Optional[dict] = None
    save_result: Optional[dict] = None
    message: Optional[str] = None
    message_ok = True

    if request.method == "POST":
        action = request.form.get("action")
        page_url = request.form.get("page_url", "").strip()
        current_user = auth.get_current_user()
        token = resolve_confluence_token(current_user["user_id"] if current_user else None)

        if action == "load":
            if not page_url:
                message, message_ok = "안건 페이지 URL을 입력해주세요.", False
            elif not token:
                message, message_ok = (
                    "Confluence 개인 액세스 토큰(PAT)이 없습니다. "
                    "워드 변환기 화면에서 내 PAT을 등록해주세요.",
                    False,
                )
            else:
                try:
                    loaded = load_agenda_page_for_editing(page_url, token=token)
                    message, message_ok = f"'{loaded['title']}' 안건 페이지를 불러왔습니다.", True
                except Exception as e:
                    message, message_ok = f"안건 페이지를 불러오지 못했습니다: {e}", False

        elif action == "save":
            orig_indexes_raw = request.form.getlist("orig_index")
            titles = request.form.getlist("title")
            deleted_orig_indexes = {
                int(raw) for raw in request.form.getlist("delete_orig_index") if raw
            }

            items: List[Tuple[Optional[int], str]] = []
            for orig_raw, title in zip(orig_indexes_raw, titles):
                title = title.strip()
                orig_index = int(orig_raw) if orig_raw else None
                if orig_index is not None and orig_index in deleted_orig_indexes:
                    continue  # 삭제 표시된 기존 항목은 최종 목록에서 뺀다.
                if not title:
                    continue  # 제목 없는 새 빈 칸은 무시한다.
                items.append((orig_index, title))

            if not page_url:
                message, message_ok = "안건 페이지 URL을 입력해주세요.", False
            elif not items:
                message, message_ok = "최소 1개 이상의 안건이 필요합니다.", False
            elif not token:
                message, message_ok = (
                    "Confluence 개인 액세스 토큰(PAT)이 없습니다. "
                    "워드 변환기 화면에서 내 PAT을 등록해주세요.",
                    False,
                )
            else:
                try:
                    save_result = update_agenda_page(
                        page_url,
                        items,
                        deleted_orig_indexes=list(deleted_orig_indexes),
                        token=token,
                    )
                    message, message_ok = (
                        f"'{save_result['title']}' 안건 페이지를 수정했습니다.",
                        True,
                    )
                    # 저장 직후 최신 상태를 다시 불러와 보여준다(바로 또 고칠 수 있게).
                    loaded = load_agenda_page_for_editing(page_url, token=token)
                except Exception as e:
                    message, message_ok = f"안건 페이지 수정 실패: {e}", False

    return render_template_string(
        AGENDA_EDIT_PAGE_TEMPLATE,
        page_url=page_url,
        loaded=loaded,
        save_result=save_result,
        message=message,
        message_ok=message_ok,
        docx_export_configured=docx_export_configured(),
        current_user=auth.get_current_user(),
        active_nav="agenda_edit",
    )


@app.route("/confluence-to-docx", methods=["GET"])
def confluence_docx_page():
    if not docx_export_configured():
        return redirect("/")

    current_user = auth.get_current_user()
    pat_storage_available = confluence_credentials.is_configured()
    pat_registered = bool(
        current_user and pat_storage_available and confluence_credentials.status(current_user["user_id"])
    )

    return render_template_string(
        CONFLUENCE_DOCX_PAGE_TEMPLATE,
        current_user=current_user,
        confluence_pat_storage_available=pat_storage_available,
        confluence_pat_registered=pat_registered,
        docx_error=request.args.get("docx_error"),
        docx_message=request.args.get("docx_message"),
        mail_configured=is_mail_configured(),
        docx_export_configured=True,
        active_nav="docx",
    )


# 컨플루언스 조회가 사내망을 거치면 몇 초~몇십 초 걸릴 수 있는데, 평범한 폼
# POST는 끝날 때까지 화면이 그냥 멈춰 있는 것처럼 보여서(사용자 피드백: "중간
# 프로그레스를 알 수가 없어서 답답해") 백그라운드 스레드로 돌리고 화면이
# /status를 주기적으로 물어 지금 뭘 하고 있는지 보여주는 방식으로 바꿨다.
# 사내 소규모 팀 도구라 이 정도 메모리 상태(프로세스 안 dict)로 충분하고,
# 별도 작업 큐(Celery 등)를 들일 필요는 없다고 판단.
_conversion_jobs: dict = {}
_conversion_jobs_lock = threading.Lock()
_JOB_TTL = timedelta(minutes=15)


def _prune_old_jobs_locked() -> None:
    cutoff = datetime.utcnow() - _JOB_TTL
    for job_id in [jid for jid, job in _conversion_jobs.items() if job["created_at"] < cutoff]:
        _conversion_jobs.pop(job_id, None)


def _update_job(job_id: str, **fields) -> None:
    with _conversion_jobs_lock:
        job = _conversion_jobs.get(job_id)
        if job is not None:
            job.update(fields)


@app.route("/confluence-to-docx/start", methods=["POST"])
def start_confluence_to_docx():
    url = request.form.get("confluence_url", "").strip()
    if not url:
        return {"error": "컨플루언스 페이지 URL을 입력해주세요."}, 400

    current_user = auth.get_current_user()
    token = resolve_confluence_token(current_user["user_id"] if current_user else None)
    if not token:
        return {"error": "Confluence 개인 액세스 토큰(PAT)이 없습니다. 아래에서 내 PAT을 등록해주세요."}, 400

    job_id = uuid.uuid4().hex
    with _conversion_jobs_lock:
        _prune_old_jobs_locked()
        _conversion_jobs[job_id] = {
            "status": "running",
            "message": "시작하는 중...",
            "data": None,
            "filename": None,
            "created_at": datetime.utcnow(),
        }

    def run() -> None:
        try:
            data, filename = convert_confluence_url_to_docx(
                url, token=token, on_progress=lambda message: _update_job(job_id, message=message)
            )
            _update_job(job_id, status="done", message="완료", data=data, filename=filename)
        except DocxExportUnavailable as e:
            _update_job(job_id, status="error", message=str(e))
        except Exception as e:
            # 페이지를 못 찾음/권한 없음/네트워크 오류 등 docx_export.py가 던지는 오류.
            _update_job(job_id, status="error", message=f"변환 실패: {e}")

    threading.Thread(target=run, daemon=True).start()
    return {"job_id": job_id}


@app.route("/confluence-to-docx/status/<job_id>")
def confluence_to_docx_status(job_id):
    with _conversion_jobs_lock:
        job = _conversion_jobs.get(job_id)
    if job is None:
        return {"status": "error", "message": "작업을 찾을 수 없습니다(만료되었거나 서버가 재시작됐을 수 있음)."}, 404
    return {"status": job["status"], "message": job["message"]}


def _get_done_job(job_id: str) -> Optional[dict]:
    # 다운로드/미리보기/메일 세 가지가 전부 같은 job의 data를 쓸 수 있어야
    # 해서(어느 하나를 했다고 나머지가 못 쓰게 되면 안 됨) 받는다고 바로
    # 지우지 않는다 - 대신 _JOB_TTL(15분)이 지나면 다음 /start 호출에서
    # 치워진다.
    with _conversion_jobs_lock:
        job = _conversion_jobs.get(job_id)
        return dict(job) if job is not None and job["status"] == "done" else None


@app.route("/confluence-to-docx/download/<job_id>")
def confluence_to_docx_download(job_id):
    job = _get_done_job(job_id)
    if job is None:
        return redirect(
            f"/confluence-to-docx?docx_error={quote('파일을 찾을 수 없습니다(만료되었을 수 있음) - 다시 시도해주세요.')}"
        )

    return send_file(
        io.BytesIO(job["data"]),
        as_attachment=True,
        download_name=job["filename"],
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@app.route("/confluence-to-docx/preview/<job_id>")
def confluence_to_docx_preview(job_id):
    job = _get_done_job(job_id)
    if job is None:
        return redirect(
            f"/confluence-to-docx?docx_error={quote('파일을 찾을 수 없습니다(만료되었을 수 있음) - 다시 시도해주세요.')}"
        )

    return render_template_string(
        CONFLUENCE_DOCX_PREVIEW_TEMPLATE,
        filename=job["filename"],
        job_id=job_id,
        preview_html=docx_bytes_to_preview_html(job["data"]),
    )


@app.route("/confluence-to-docx/email/<job_id>", methods=["POST"])
def confluence_to_docx_email(job_id):
    job = _get_done_job(job_id)
    if job is None:
        return {"error": "파일을 찾을 수 없습니다(만료되었을 수 있음) - 다시 시도해주세요."}, 404

    to_email = (request.form.get("email_to") or "").strip()
    if not to_email:
        return {"error": "받을 사람 이메일을 입력해주세요."}, 400
    if not is_mail_configured():
        return {"error": "MAIL_API_TOKEN 등 메일 API 환경변수가 설정되지 않아 메일을 보낼 수 없습니다."}, 400

    try:
        send_report_email(
            to_email,
            subject=f"[Word 변환] {job['filename']}",
            body_html=f"<p>요청하신 Confluence 변환 결과(\"{html.escape(job['filename'])}\")를 첨부했습니다.</p>",
            attachments=[
                {
                    "filename": job["filename"],
                    "content": job["data"],
                    "content_type": (
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    ),
                }
            ],
        )
    except MailConfigError as e:
        return {"error": f"메일 발송 실패: {e}"}, 502

    return {"message": f"{to_email} 로 메일을 보냈습니다."}


@app.route("/confluence-pat", methods=["POST"])
def save_confluence_pat():
    current_user = auth.get_current_user()
    if current_user is None:
        return redirect(f"/confluence-to-docx?docx_error={quote('로그인이 필요합니다.')}")

    pat = request.form.get("confluence_pat", "").strip()
    if not pat:
        return redirect(f"/confluence-to-docx?docx_error={quote('PAT을 입력해주세요.')}")

    try:
        owner = verify_confluence_token(pat)
    except DocxExportUnavailable as e:
        return redirect(f"/confluence-to-docx?docx_error={quote(str(e))}")
    except Exception:
        # PAT이 틀렸거나(401/익명 응답), 서버 연결 자체가 안 되는 경우 -
        # 등록자가 적은 이름을 그대로 믿지 않고, Confluence가 확인해주지
        # 못한 토큰은 저장하지 않는다(토큰 도용 방지). 예외 메시지 한 줄만
        # 보여주면 "PAT 확인 실패: HTTPSConnectionPool(...)" 처럼 원인을
        # 특정하기 어려운 경우가 많아서, diagnose_connection()의 단계별
        # 결과(요청 URL, CA 경로, DEP_TICKET 설정 여부 등)를 그대로 보여준다
        # - 터미널 접속 없이도 화면에서 바로 원인을 좁힐 수 있게.
        detail = "\n".join(diagnose_confluence_connection(pat))
        message = "PAT 확인 실패:\n" + detail
        return redirect(f"/confluence-to-docx?docx_error={quote(message)}")

    try:
        confluence_credentials.set_pat(current_user["user_id"], pat)
    except confluence_credentials.CredentialStorageUnavailable as e:
        return redirect(f"/confluence-to-docx?docx_error={quote(str(e))}")

    return redirect(
        f"/confluence-to-docx?docx_message="
        f"{quote(f'내 Confluence PAT을 저장했습니다. (확인된 소유자: {owner})')}"
    )


@app.route("/confluence-pat/delete", methods=["POST"])
def delete_confluence_pat():
    current_user = auth.get_current_user()
    if current_user is None:
        return redirect(f"/confluence-to-docx?docx_error={quote('로그인이 필요합니다.')}")

    confluence_credentials.delete_pat(current_user["user_id"])
    return redirect(f"/confluence-to-docx?docx_message={quote('내 Confluence PAT을 삭제했습니다.')}")


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
    # 백그라운드 스레드(start_confluence_to_docx)로 돌리고 화면이 /status를
    # 주기적으로 물어 진행 상황을 보여준다 - 그 폴링 요청들과 변환 작업
    # 자체가 동시에 처리돼야 하고, 그동안 다른 요청(안건 소스 생성 등)도
    # 막히면 안 된다.
    app.run(host="0.0.0.0", port=port, threaded=True)


if __name__ == "__main__":
    main()
