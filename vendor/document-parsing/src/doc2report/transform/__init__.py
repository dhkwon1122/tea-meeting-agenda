"""텍스트 정규화 단계 — IR을 받아 문구를 다듬은 IR을 돌려준다.

무엇을 바꿀지는 rules/*.yaml 과 프로파일의 text 항목이 정하고,
여기서는 '어디에' 적용할지(제목·본문·목록·표)만 판단한다.
코드 블록과 표 머리행은 기본적으로 건드리지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ..ir import (
    Block,
    Callout,
    CodeBlock,
    Document,
    Heading,
    ListItem,
    Paragraph,
    Run,
    Table,
    plain,
)
from ..profile import Profile
from .notation import Notation, NotationRules
from .stylize_ko import Change, Gaechosik

__all__ = ["apply_text_rules", "TransformResult", "Change", "Gaechosik", "Notation"]


@dataclass
class TransformResult:
    document: Document
    changes: list[Change] = field(default_factory=list)


def apply_text_rules(doc: Document, profile: Profile) -> TransformResult:
    engine = _Engine(profile)
    blocks = engine.blocks(doc.blocks)
    title = engine.heading_text(doc.title) if doc.title else None
    return TransformResult(
        document=Document(blocks=blocks, title=title, source=doc.source),
        changes=engine.changes,
    )


class _Engine:
    def __init__(self, profile: Profile):
        self.profile = profile
        rules = profile.text
        self.use_notation = "notation" in rules.rules or not rules.rules
        self.use_endings = rules.gaechosik and ("endings" in rules.rules or not rules.rules)
        self.gaechosik = (Gaechosik(noun_ending=rules.noun_ending)
                          if self.use_endings or rules.split_long_sentences else None)
        self.notation = Notation(_notation_rules(profile)) if self.use_notation else None

    @property
    def changes(self) -> list[Change]:
        out: list[Change] = []
        if self.notation:
            out.extend(self.notation.changes)
        if self.gaechosik:
            out.extend(self.gaechosik.changes)
        return out

    # ── 블록 순회 ───────────────────────────────────────────────────────

    def blocks(self, blocks: list[Block], *, in_table: bool = False) -> list[Block]:
        out: list[Block] = []
        for block in blocks:
            out.extend(self.block(block, in_table=in_table))
        return out

    def block(self, block: Block, *, in_table: bool) -> list[Block]:
        if isinstance(block, CodeBlock):
            return [block]

        if isinstance(block, Heading):
            # 제목은 개조식(~함/~음) 대상이 아니라 표기 정리 + 명사 종결만 한다.
            return [Heading(level=block.level, runs=self.heading_runs(block.runs),
                            section_title=block.section_title, page_title=block.page_title)]

        if isinstance(block, (Paragraph, ListItem)):
            return self.sentence_block(block, in_table=in_table)

        if isinstance(block, Callout):
            return [Callout(kind=block.kind, blocks=self.blocks(block.blocks, in_table=in_table))]

        if isinstance(block, Table):
            for row in block.rows:
                for cell in row.cells:
                    keep = cell.is_header or self.profile.text.keep_original_in_tables
                    cell.blocks = self.blocks(cell.blocks, in_table=keep)
            return [block]

        return [block]

    def sentence_block(self, block: Paragraph | ListItem, *, in_table: bool) -> list[Block]:
        endings = self.use_endings and not in_table
        split = (self.profile.text.split_long_sentences and not in_table
                 and self.gaechosik is not None)

        if split and _uniform(block.runs):
            text = self.text_only(plain(block.runs), endings=False)
            parts = self.gaechosik.split_long(text, self.profile.text.max_sentence_chars)
            if endings:
                parts = [self.gaechosik.convert(p) for p in parts]
            parts = [p for p in parts if p.strip()]
            if len(parts) > 1:
                template = block.runs[0] if block.runs else Run("")
                return [_rebuild(block, [template.copy_with(p)]) for p in parts]
            if parts:
                template = block.runs[0] if block.runs else Run("")
                return [_rebuild(block, [template.copy_with(parts[0])])]
            return []

        return [_rebuild(block, self.runs(block.runs, endings=endings))]

    # ── 런 단위 적용 ────────────────────────────────────────────────────

    def runs(self, runs: list[Run], *, endings: bool) -> list[Run]:
        if not runs:
            return runs
        if _uniform(runs):
            merged = plain(runs)
            converted = self.text_only(merged, endings=endings)
            return [runs[0].copy_with(converted)] if converted else []
        out: list[Run] = []
        for index, run in enumerate(runs):
            if run.code:  # 코드 인라인은 그대로 둔다
                out.append(run)
                continue
            last = index == len(runs) - 1
            out.append(run.copy_with(self.text_only(run.text, endings=endings and last,
                                                    strip=False)))
        return [r for r in out if r.text]

    def heading_runs(self, runs: list[Run]) -> list[Run]:
        if not runs or not _uniform(runs):
            return runs  # 서식이 섞인 제목(굵게+링크 등)은 명사 종결 판단이 애매해 건드리지 않는다
        converted = self.heading_text(plain(runs))
        return [runs[0].copy_with(converted)] if converted else []

    def heading_text(self, text: str) -> str:
        body = self.notation.apply(text) if self.notation else text
        if self.gaechosik:
            body = self.gaechosik.heading_noun_ending(body)
        return body

    def text_only(self, text: str, *, endings: bool = False, strip: bool = True) -> str:
        if text is None:
            return text
        leading = text[: len(text) - len(text.lstrip())] if not strip else ""
        trailing = text[len(text.rstrip()):] if not strip else ""
        body = text.strip()
        if self.notation:
            body = self.notation.apply(body)
        if endings and self.gaechosik:
            body = self.gaechosik.convert(body)
        return leading + body + trailing


def _notation_rules(profile: Profile) -> NotationRules:
    """날짜 형식은 프로파일이 정한 것이 있으면 그것을 따른다."""
    rules = NotationRules.load()
    if profile.text.date_format:
        rules = replace(rules, date_format=profile.text.date_format)
    return rules


def _uniform(runs: list[Run]) -> bool:
    if len(runs) <= 1:
        return True
    first = (runs[0].bold, runs[0].italic, runs[0].code, runs[0].href)
    return all((r.bold, r.italic, r.code, r.href) == first for r in runs)


def _rebuild(block: Paragraph | ListItem, runs: list[Run]) -> Block:
    if isinstance(block, ListItem):
        return ListItem(depth=block.depth, runs=runs, ordered=block.ordered,
                        number=block.number, marker=block.marker,
                        from_heading=block.from_heading)
    return Paragraph(runs=runs, align=block.align)
