"""문서 구조 정규화 — 제목을 단락 체계 안으로 접어 넣고, 표 주석을 표에 붙인다.

사내 보고서는 제목/본문이 따로 있는 것이 아니라 1. → □ → - 한 체계로 쓰인다.
반면 Confluence·Markdown 문서는 ##, ### 제목과 목록을 섞어 쓴다.
그 둘을 맞춰 주는 단계다. (profiles의 text.headings_as_levels 로 끈다.)

    ## 추진 배경        →  1. 추진 배경
    - 응답 지연 지속    →     □ 응답 지연 지속
      - 상세 내용       →        - 상세 내용

Confluence에서 내려받은 문서는 제목·항목에 이미 "1. 추진 배경", "□ 채용현황", "ㆍ 입사예정"처럼
말머리가 문자로 박혀 있는 경우가 흔하다(사내 관행). 그대로 접으면 프로파일이 매기는
말머리와 겹쳐 "1.<TAB>1. 추진 배경"처럼 두 번 나온다. 처리는 두 가지 중 하나다
(`profiles`의 `text.keep_leading_markers`):

- keep(기본, 2026-09-29 사용자 원칙 "이미 쓴 글머리 기호는 바꾸지 말 것"):
  원문 말머리를 `ListItem.marker`로 옮겨 그대로 쓰고 프로파일 말머리는 붙이지 않는다.
  제목 아래 일반 문단이면 그 말머리가 가리키는 단계(`numbering[].marker/aliases`)에 둔다.
- strip: 원문 말머리를 떼고 프로파일 말머리로 통일한다(뗀 것은 Change로 남긴다).
"""

from __future__ import annotations

import re

from ..ir import (DATE_LINE, Block, Callout, Document, Heading, ListItem, Paragraph, Run, Table,
                  invisible_codes, is_blank, plain)
from .stylize_ko import Change

HEADING_BASE = 2  # H1은 문서 제목이므로 H2가 첫 단계(1.)가 된다

# 수동 번호: "1. ", "1.추진", "1.1. ", "2) ", "(3) ".
# 끝의 (?!\d) 가 "1.5배 향상"처럼 숫자로 시작하는 제목을, 두 자리 제한이 "2026. 9. 1. 기준"
# 같은 날짜로 시작하는 문단을 번호로 오인하지 않게 한다.
_EXISTING_NUMBER = re.compile(r"^\s*(?:\(\d{1,2}\)|\d{1,2}(?:\.\d{1,2})*[.)])(?!\d)\s*")
# 한글 번호: "가. ", "나) ", "(다) " — "가.격" 같은 오인을 막으려고 뒤에 공백을 요구한다.
_HANGUL_ENUM = re.compile(r"^\s*(?:\([가나다라마바사아자차카타파하]\)|[가나다라마바사아자차카타파하][.)])\s+")


def fold_headings_into_levels(doc: Document, markers: list[str] | None = None, *,
                              keep: bool = False,
                              marker_depths: dict[str, int] | None = None,
                              normalize: bool = False,
                              no_marker_openers: list[str] | None = None,
                              auto_markers: bool = True,
                              plain_level: int | None = None,
                              note_marks: list[str] | None = None,
                              ) -> tuple[Document, list[Change]]:
    """제목을 ListItem으로 바꾸고, 그 아래 목록의 깊이를 한 단계씩 민다."""
    blocks: list[Block] = []
    changes: list[Change] = []
    heading_depth = -1
    marker_re = _marker_pattern(markers or [])
    depths = marker_depths or {}
    openers = tuple(no_marker_openers or ())

    def _marked_depth(runs: list[Run]) -> int | None:
        found = _find_marker(runs, marker_re)
        if found is None:
            return None
        own = found[0]
        if own in depths:
            return depths[own]
        return 0 if own[:1].isdigit() else None  # "1." "1)" = 첫 단계, 그 밖("※", "가.")은 문단 그대로

    def item(depth: int, runs: list[Run], own: str | None = None, *, derived: bool = False,
             **extra) -> ListItem:
        if own is None:
            found = _find_marker(runs, marker_re)
            if found is not None:
                own, rest, label = found
                if keep:
                    runs = rest
                else:
                    changes.append(Change(plain(runs), plain(rest), label))
                    runs, own = rest, None
        if own is None and openers and plain(runs).lstrip().startswith(openers):
            own = ""  # 꺾쇠 표기는 말머리 없이 — 빈 문자열이면 렌더러가 프로파일 말머리를 안 붙인다
        elif own is None and derived and not auto_markers:
            own = ""  # 제목·문단에서 온 항목인데 원문 말머리가 없으면 새로 만들지 않는다
        elif not keep:
            own = None
        return ListItem(depth=depth, runs=runs, marker=own, **extra)

    base = 0  # 합친 문서의 절 제목(section_title) 아래에선 모든 단계가 한 칸씩 들어간다
    for block in doc.blocks:
        if isinstance(block, Heading) and block.page_title:
            heading_depth, base = -1, 0  # 새 쪽의 큰 제목 — 단계에 넣지 않고 그 쪽은 처음부터
            blocks.append(block)
        elif isinstance(block, Heading) and block.section_title:
            heading_depth, base = 0, 1
            blocks.append(item(0, block.runs, derived=True, from_heading=True))
        elif isinstance(block, Heading):
            depth = max(0, block.level - HEADING_BASE) + base
            heading_depth = depth
            blocks.append(item(depth, block.runs, derived=True, from_heading=True))
        elif isinstance(block, ListItem):
            blocks.append(item(heading_depth + 1 + block.depth, block.runs, block.marker,
                               ordered=block.ordered, number=block.number))
        elif isinstance(block, Paragraph) and heading_depth >= 0:
            # 제목 아래 본문 문단도 그 단계의 항목으로 붙인다. 원문 말머리를 살리는 경우엔
            # 그 말머리가 가리키는 단계("-"면 - 단계)로 둔다 — 제목보다 얕아지지는 않게.
            level = _marked_depth(block.runs)
            new = item(heading_depth + 1, block.runs, derived=True)
            if level is not None:
                new.depth = max(heading_depth + 1, level + base)
            blocks.append(new)
        elif isinstance(block, Paragraph) and (top := _marked_depth(block.runs)) is not None:
            # 제목이 나오기 전이라도 원문 말머리("1." "□" "-")가 있는 문단은 그 말머리의 단계로
            # 둔다 — 붙여넣은 글처럼 제목 없이 말머리로만 구분한 글도 들여쓰기가 맞도록.
            blocks.append(item(top, block.runs, derived=True))
        else:
            blocks.append(block)

    if normalize:
        items = [b for b in blocks if isinstance(b, ListItem)]
        shift = min((b.depth for b in items), default=0)
        for b in items:
            b.depth -= shift  # 접는 단계에서 새로 만든 객체라 바꿔도 원본 IR은 그대로다

    if auto_markers and plain_level is not None and not any(
            isinstance(b, (ListItem, Heading)) for b in blocks):
        # 제목도 말머리도 없는 메모 — 문단마다 말머리를 달아 개조식으로 구분한다. 날짜 줄·꺾쇠 표기·
        # ※ 참고는 문단 그대로 둔다.
        skip = tuple(openers) + tuple(note_marks or ())
        blocks = [ListItem(depth=plain_level, runs=b.runs)
                  if isinstance(b, Paragraph) and not DATE_LINE.match(plain(b.runs).strip())
                  and not plain(b.runs).lstrip().startswith(skip or ("\0",)) else b
                  for b in blocks]

    return Document(blocks=blocks, title=doc.title, source=doc.source), changes


def clean_page_titles(doc: Document, patterns: list[str]) -> tuple[Document, list[Change]]:
    """쪽 제목(`Heading.page_title` — 불러온 연결 문서·입력마다 새 쪽의 제목) 앞의 번호표를 뗀다.
    Confluence에서 "(첨부 1) 세부 계획"처럼 페이지 제목에 붙여 둔 첨부 번호는 보고서에선 군더더기다
    (2026-09-30 사용자). 규칙은 text.page_title_strip."""
    if not patterns:
        return doc, []
    regexes = [re.compile(p) for p in patterns]
    changes: list[Change] = []
    blocks: list[Block] = []
    for block in doc.blocks:
        if isinstance(block, Heading) and block.page_title:
            before = plain(block.runs)
            after = before
            for rx in regexes:
                after = rx.sub("", after, count=1)
            after = after.strip()
            if after and after != before.strip():
                changes.append(Change(before, after, "쪽 제목 번호표 제거"))
                block = Heading(level=block.level, runs=[block.runs[0].copy_with(after)],
                                section_title=block.section_title, page_title=True)
        blocks.append(block)
    return Document(blocks=blocks, title=doc.title, source=doc.source), changes


def drop_blank_blocks(doc: Document) -> tuple[Document, list[Change]]:
    """글자가 하나도 없는 제목·문단·항목을 뺀다. 접으면 프로파일 말머리만 덜렁 찍힌 줄("□")이 되기
    때문이다(2026-09-29 사용자 보고 — 두 번). 공백뿐인 것은 조용히 빼고, **보이지 않는 글자가 든
    것**(제로폭 공백·한글 채움문자)은 원인을 알 수 있게 코드를 리포트에 남긴다."""
    blocks: list[Block] = []
    changes: list[Change] = []
    for block in doc.blocks:
        if isinstance(block, (Heading, Paragraph, ListItem)) and is_blank(plain(block.runs)):
            codes = invisible_codes(plain(block.runs))
            if codes:
                changes.append(Change(f"(보이지 않는 글자 {' '.join(codes)}만 있는 줄)", "(삭제)", "빈 항목 제거"))
            continue
        blocks.append(block)
    return Document(blocks=blocks, title=doc.title, source=doc.source), changes


def has_leading_marker(runs: list[Run], markers: list[str]) -> bool:
    """원문에 말머리("1." "□" "-" "가." 등)를 쳐 둔 문단인가 — 자동 판단에서 정리 정도를 잴 때."""
    return _find_marker(runs, _marker_pattern(markers)) is not None


def _marker_pattern(markers: list[str]) -> re.Pattern | None:
    """원문에 문자로 쳐 둔 말머리(profiles의 text.leading_markers)를 잡는 패턴.

    "-", "." 같은 ASCII 기호는 뒤에 공백이 와야만 말머리로 본다 — "-5%"(음수),
    ".5초"를 말머리로 오인하지 않게. "ㆍ", "□", "①" 같은 기호는 한국어 문서에서
    "ㆍ입사예정"처럼 붙여 쓰는 일이 많아 공백이 없어도 잡는다(같은 기호가 연달아
    오는 "○○팀" 같은 자리표시자는 제외).
    """
    if not markers:
        return None
    ordered = sorted(markers, key=len, reverse=True)
    spaced = [re.escape(m) for m in ordered if m.isascii()]
    glued = [f"{re.escape(m)}(?!{re.escape(m)})" for m in ordered if not m.isascii()]
    parts = []
    if glued:
        parts.append(rf"(?:{'|'.join(glued)})\s*")
    if spaced:
        parts.append(rf"(?:{'|'.join(spaced)})\s+")
    return re.compile(rf"^\s*(?:{'|'.join(parts)})")


def _find_marker(runs: list[Run], marker_re: re.Pattern | None
                 ) -> tuple[str, list[Run], str] | None:
    """(원문 말머리, 말머리를 뗀 runs, Change 규칙명). 없으면 None.

    runs[0]만 보지 않고 전체 텍스트를 이어 붙여 판단한다 — 앞에 빈 run이나
    말머리와 공백이 서로 다른 run에 걸쳐 있어도 놓치지 않기 위해서다.
    """
    if not runs:
        return None
    text = plain(runs)
    for pattern, label in ((_EXISTING_NUMBER, "제목 중복 번호 제거"),
                           (_HANGUL_ENUM, "제목 중복 번호 제거"),
                           (marker_re, "중복 말머리 제거")):
        match = pattern.match(text) if pattern is not None else None
        if match:
            if match.end() >= len(text.rstrip()):
                return None  # 말머리만 있는 항목("1.", "-")은 그대로 둔다
            return text[:match.end()].strip(), _drop_prefix(runs, match.end()), label
    return None


def _drop_prefix(runs: list[Run], count: int) -> list[Run]:
    """runs 맨 앞에서 글자 count개를 뗀다 — 여러 run에 걸쳐 있어도 맞게 처리한다."""
    out: list[Run] = []
    remaining = count
    for run in runs:
        if remaining <= 0:
            out.append(run)
        elif remaining >= len(run.text):
            remaining -= len(run.text)
        else:
            out.append(run.copy_with(run.text[remaining:]))
            remaining = 0
    return out


# ── 표 제목 ─────────────────────────────────────────────────────────────

_BRACKET_PAIRS = [("[", "]"), ("［", "］"), ("【", "】"), ("〔", "〕"), ("〈", "〉"), ("《", "》")]


def attach_table_captions(doc: Document) -> tuple[Document, list[Change]]:
    """표 바로 위, "【사업현황】"처럼 꺾쇠로 감싼 문단을 Table.caption으로 옮긴다.

    옮기지 않으면 제목 접기에서 그 단계의 ListItem이 되어 "- 【사업현황】"처럼
    프로파일 말머리(-)가 또 붙는다 — 표 제목이지 항목이 아니므로 attach_table_notes와
    같은 이유로 접기 **전에** 빼 둔다(표 뒤 대신 표 앞이라는 점만 다르다).
    꺾쇠 자체는 원문 그대로 남긴다 — 뗄 건 그 앞에 붙던 "-" 뿐이다
    (2026-09-29 사용자 요청: "꺾쇠는 원형 유지, 앞의 '-'만 제외"). 원문 문단에
    정렬이 있었으면(`Paragraph.align`) 그것도 같이 옮긴다 — 없으면 렌더러가
    프로파일의 caption 기본 정렬을 쓴다.
    """
    blocks: list[Block] = []
    changes: list[Change] = []
    for block in doc.blocks:
        if isinstance(block, Table) and blocks and isinstance(blocks[-1], Paragraph) and not block.caption:
            para = blocks[-1]
            text = plain(para.runs).strip()
            if _is_bracket_caption(text):
                blocks.pop()
                block.caption = text
                block.caption_align = para.align
                changes.append(Change(text, text, "표 제목(말머리 제외)"))
        blocks.append(block)
    return Document(blocks=blocks, title=doc.title, source=doc.source), changes


def _is_bracket_caption(text: str) -> bool:
    return any(text.startswith(open_c) and text.endswith(close_c)
              and len(text) > len(open_c) + len(close_c)
              for open_c, close_c in _BRACKET_PAIRS)


# ── 표 주석 ─────────────────────────────────────────────────────────────


def attach_table_notes(doc: Document, markers: list[str],
                       note_marker: str | None) -> tuple[Document, list[Change]]:
    """표 바로 뒤의 주석 문단을 표에 붙인다.

    - 인용문(`> 측정 기준은 …`)은 Markdown·Confluence에서 표 설명을 다는 흔한 방식이다.
    - `*`, `※` 등 markers 로 시작하는 문단도 주석으로 본다.
    본문 흐름에서 빼 두지 않으면 제목 접기에서 □ 항목이 되어 버리고, 표와 주석 사이에
    '표 뒤 간격'이 끼어 주석이 표에서 떨어져 보인다.
    """
    blocks: list[Block] = []
    changes: list[Change] = []
    current: Table | None = None

    for block in doc.blocks:
        if current is not None:
            notes = _as_notes(block, markers)
            if notes is not None:
                for runs in notes:
                    current.notes.append(runs)
                    after = f"{note_marker} {plain(runs)}" if note_marker else plain(runs)
                    changes.append(Change(_original_text(block), after, "표 주석"))
                continue
        current = block if isinstance(block, Table) else None
        blocks.append(block)

    return Document(blocks=blocks, title=doc.title, source=doc.source), changes


def _as_notes(block: Block, markers: list[str]) -> list[list[Run]] | None:
    if isinstance(block, Callout) and block.kind == "quote":
        if not block.blocks or not all(isinstance(b, Paragraph) for b in block.blocks):
            return None
        return [_strip_marker(b.runs, markers) for b in block.blocks]
    if isinstance(block, Paragraph) and _marker_of(plain(block.runs), markers):
        return [_strip_marker(block.runs, markers)]
    return None


def _marker_of(text: str, markers: list[str]) -> str | None:
    stripped = text.lstrip()
    for marker in sorted(markers, key=len, reverse=True):
        if stripped.startswith(marker):
            return marker
    return None


def _strip_marker(runs: list[Run], markers: list[str]) -> list[Run]:
    if not runs:
        return runs
    marker = _marker_of(runs[0].text, markers)
    if not marker:
        return runs
    text = runs[0].text.lstrip()[len(marker):].lstrip()
    head = runs[0].copy_with(text)
    return [head, *runs[1:]] if text else list(runs[1:])


def _original_text(block: Block) -> str:
    if isinstance(block, Callout):
        return " / ".join(plain(b.runs) for b in block.blocks if isinstance(b, Paragraph))
    return plain(getattr(block, "runs", []))


# ── 짧은 항목 병합 ──────────────────────────────────────────────────────


def merge_short_list_items(doc: Document, max_chars: int) -> tuple[Document, list[Change]]:
    """같은 단계의 짧은 항목이 연달아 나오면 "및"으로 **둘씩** 짝지어 합친다
    (사용자 요청, 2026-09-28). "10월 중 2차 성능 시험 실시" / "미흡 사항 4분기
    과제로 이관"처럼 둘 다 짧으면 굳이 줄을 나눌 필요가 없다. 셋 이상을 한 항목에
    이어 붙이지는 않는다 — "A 및 B 및 C"는 "및"이 반복돼 어색하고, 개조식의
    핵심인 항목별 스캔 가독성도 해친다. 합친 뒤에도 다음 항목이 짧으면 그 항목은
    또 다른 항목과 새로 짝짓는다(방금 합친 결과와 다시 합치지 않는다).

    fold_headings_into_levels보다 **먼저** 돌아야 한다 — 그 전에는 제목이
    아직 Heading 블록이라 ListItem과 섞이지 않는다. 접은 뒤에는 제목도
    ListItem이 되어 버려서, 짧은 소제목이 그 아래 짧은 항목과 잘못 합쳐질 수 있다.
    """
    if max_chars <= 0:
        return doc, []

    blocks: list[Block] = []
    changes: list[Change] = []
    src = doc.blocks
    i = 0
    while i < len(src):
        block = src[i]
        next_item = src[i + 1] if i + 1 < len(src) else None
        can_pair = (isinstance(block, ListItem) and isinstance(next_item, ListItem)
                   and next_item.depth == block.depth
                   and _merged_length([block], next_item) <= max_chars)
        group = [block, next_item] if can_pair else [block]
        j = i + len(group)

        if len(group) > 1:
            merged = _merge_items(group)
            changes.append(Change(" / ".join(plain(g.runs) for g in group),
                                  plain(merged.runs), "항목 병합"))
            blocks.append(merged)
        else:
            blocks.append(block)
        i = j

    return Document(blocks=blocks, title=doc.title, source=doc.source), changes


def _merged_length(group: list[ListItem], candidate: ListItem) -> int:
    texts = [plain(g.runs) for g in group] + [plain(candidate.runs)]
    return len(" 및 ".join(texts))


def _merge_items(group: list[ListItem]) -> ListItem:
    first = group[0]
    template = first.runs[0] if first.runs else Run("")
    merged_text = " 및 ".join(plain(g.runs) for g in group)
    return ListItem(depth=first.depth, runs=[template.copy_with(merged_text)],
                    ordered=first.ordered, number=first.number, marker=first.marker,
                    from_heading=first.from_heading)
