"""안건 입력값을 받아, 안건별로 어떤 매크로/앵커를 거쳐 서로 연결되는지
보여주는 박스-화살표 다이어그램(HTML/CSS)을 만든다.

외부 라이브러리(Mermaid 등) 없이 순수 HTML/CSS만 쓴다 - 사내망에서
외부 CDN이 막혀 있을 수 있어서다(mailer.py/Dockerfile과 같은 이유).
Confluence 소스나 메일 본문에는 들어가지 않는, 웹 UI 미리보기 전용
시각화다.
"""

from __future__ import annotations

import html
from typing import Sequence

from ..builder import AgendaItem, attachment_page_title


def _badge(kind: str, label: str) -> str:
    return f'<span class="diagram-badge diagram-badge--{kind}">{html.escape(label)}</span>'


def _item_diagram_html(index: int, item: AgendaItem) -> str:
    title = item.title
    page_title = attachment_page_title(index, title)

    return f"""
<div class="diagram-item">
  <div class="diagram-item-label">안건 {index}</div>

  <div class="diagram-box diagram-box--heading">
    <div class="diagram-box-title">① 제목 섹션</div>
    <div class="diagram-box-heading-text">{html.escape(f"{index}. {title}")}</div>
    <div class="diagram-badges">
      {_badge("anchor", f"anchor: 제목{index}")}
    </div>
  </div>

  <div class="diagram-arrow">
    <span class="diagram-arrow-label">링크 「(첨부{index})」 →</span>
    <span class="diagram-arrow-glyph">↓</span>
  </div>

  <div class="diagram-box diagram-box--attachment">
    <div class="diagram-box-title">② 첨부 섹션 (전체 안건 공용 섹션 중 이 안건 몫)</div>
    <div class="diagram-badges">
      {_badge("anchor", f"anchor: 첨부{index}")}
      {_badge("macro", f"ui-expand: 「{page_title}」")}
    </div>
    <div class="diagram-sub">
      {_badge("include", f"include → 하위 페이지 「{page_title}」")}
      {_badge("back", f"(돌아가기) 링크 → 제목{index}로 복귀")}
    </div>
  </div>
</div>
"""


def build_macro_diagram_html(items: Sequence[AgendaItem]) -> str:
    """안건 목록을 받아 안건별 다이어그램을 순서대로 이어붙인 HTML을 만든다."""
    return "\n".join(_item_diagram_html(idx, item) for idx, item in enumerate(items, start=1))
