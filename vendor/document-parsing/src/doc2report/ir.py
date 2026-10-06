"""중간표현(IR) — 파이프라인의 모든 단계가 주고받는 유일한 계약.

입력 형식이 늘어나면 parsers/ 만 추가하고, 출력이 늘어나면 render/ 만 추가한다.
IR 자체에는 서식(pt, mm, 글꼴명)이 절대 들어가지 않는다. 서식은 프로파일의 몫이다.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field


# ── 인라인 ──────────────────────────────────────────────────────────────


@dataclass
class Run:
    """서식이 동일한 텍스트 조각."""

    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False
    href: str | None = None

    def copy_with(self, text: str) -> Run:
        return Run(text=text, bold=self.bold, italic=self.italic, code=self.code, href=self.href)


# 화면에 아무것도 안 그려지는데 str.strip()으로는 안 걸러지는 글자 — 한국어 문서(Confluence·메신저에서
# 복사)에서는 "빈 줄"을 이런 글자로 채워 두는 일이 흔하다: 한글 채움문자(U+3164)·점자 빈칸 등.
_INVISIBLE = frozenset("\u3164\u115f\u1160\u2800\u17b4\u17b5\u034f")


def _is_invisible(ch: str) -> bool:
    return ch.isspace() or ch in _INVISIBLE or unicodedata.category(ch) in ("Cf", "Cc", "Zs", "Zl", "Zp")


def is_blank(text: str) -> bool:
    """공백이거나 보이지 않는 글자(제로폭 공백 U+200B, 한글 채움문자 U+3164 등)뿐인가."""
    return all(_is_invisible(ch) for ch in text)


def invisible_codes(text: str) -> list[str]:
    """text에 든, 일반 공백이 아닌 보이지 않는 글자의 코드("U+200B") — 리포트에 원인을 보이려고."""
    return sorted({f"U+{ord(ch):04X}" for ch in text if _is_invisible(ch) and not ch.isspace()})


# "2026. 10. 1" / "2026.10.1." 처럼 날짜만 있는 줄 (제목 아래 날짜 표기 판별용)
DATE_LINE = re.compile(r"^\d{4}\s*\.\s*\d{1,2}\s*\.\s*\d{1,2}\s*\.?$")


def plain(runs: list[Run]) -> str:
    """런 목록의 순수 텍스트."""
    return "".join(r.text for r in runs)


# ── 블록 ────────────────────────────────────────────────────────────────


@dataclass
class Heading:
    level: int  # 1-6
    runs: list[Run] = field(default_factory=list)
    section_title: bool = False  # 여러 입력을 합칠 때 넣는 각 입력의 제목 — 그 아래 내용은 한 단계씩 들어간다
    page_title: bool = False     # 입력마다 새 쪽으로 합칠 때 그 쪽의 큰 제목(문서 제목 서식)


@dataclass
class Paragraph:
    runs: list[Run] = field(default_factory=list)
    align: str | None = None  # left | center | right — 원문에 명시된 정렬만 (예: Confluence의
                              # style="text-align:..."). 없으면 프로파일 기본값을 따른다.


@dataclass
class ListItem:
    """목록 항목 하나. depth는 0부터 시작하며 프로파일의 numbering 배열 인덱스가 된다."""

    depth: int
    runs: list[Run] = field(default_factory=list)
    ordered: bool = False
    number: int | None = None  # ordered일 때 원본 번호
    marker: str | None = None  # 원문에 문자로 쳐 둔 말머리("ㆍ", "①", "1.") — 있으면 그대로 쓴다
    from_heading: bool = False  # 제목을 접어 만든 항목 (단계 굵게를 끈 프로파일에서도 굵게 둔다)


@dataclass
class CodeBlock:
    text: str
    lang: str | None = None


@dataclass
class Image:
    src: str  # 로컬 경로 또는 URL
    caption: str | None = None
    width_px: int | None = None
    height_px: int | None = None


@dataclass
class Cell:
    blocks: list["Block"] = field(default_factory=list)
    colspan: int = 1
    rowspan: int = 1
    is_header: bool = False
    align: str | None = None  # left | center | right


@dataclass
class Row:
    cells: list[Cell] = field(default_factory=list)


@dataclass
class Table:
    rows: list[Row] = field(default_factory=list)
    header_rows: int = 1
    caption: str | None = None
    caption_align: str | None = None  # 원문 문단의 정렬을 그대로 (없으면 프로파일 기본값)
    notes: list[list[Run]] = field(default_factory=list)  # 표 바로 아래 주석 (기호는 뗀 본문)

    @property
    def col_count(self) -> int:
        return max((sum(c.colspan for c in r.cells) for r in self.rows), default=0)


@dataclass
class Callout:
    """Confluence의 info/note/warning 패널, Markdown의 인용문."""

    kind: str  # info | note | warning | quote
    blocks: list["Block"] = field(default_factory=list)


@dataclass
class HorizontalRule:
    pass


@dataclass
class PageBreak:
    pass


Block = (
    Heading | Paragraph | ListItem | CodeBlock | Image | Table | Callout | HorizontalRule | PageBreak
)


@dataclass
class Document:
    blocks: list[Block] = field(default_factory=list)
    title: str | None = None
    source: str | None = None  # 원본 경로/URL — 로그·머리말용


def resolve_image_paths(blocks: list, base) -> None:
    """상대 경로 이미지를 base 폴더 기준 절대 경로로(표 셀·패널 안쪽까지)."""
    from pathlib import Path

    for block in blocks:
        if isinstance(block, Image) and not block.src.startswith(("http://", "https://")):
            path = Path(block.src)
            if not path.is_absolute():
                block.src = str((Path(base) / path).resolve())
        inner = getattr(block, "blocks", None)
        if inner:
            resolve_image_paths(inner, base)
        for row in getattr(block, "rows", None) or []:
            for cell in row.cells:
                resolve_image_paths(cell.blocks, base)


def iter_tables(doc: Document):
    """중첩(Callout, 표 셀) 안쪽까지 포함해 모든 Table을 순회."""

    def walk(blocks):
        for b in blocks:
            if isinstance(b, Table):
                yield b
                for row in b.rows:
                    for cell in row.cells:
                        yield from walk(cell.blocks)
            elif isinstance(b, Callout):
                yield from walk(b.blocks)

    yield from walk(doc.blocks)
