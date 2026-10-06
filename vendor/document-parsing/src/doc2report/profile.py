"""서식 프로파일 — 이 프로그램에서 '서식'이라는 개념이 존재하는 유일한 장소.

여백 mm, 글자 크기 pt, 글꼴명, 블릿 문자는 전부 profiles/*.yaml 에만 있다.
렌더러는 여기서 받은 값을 적용할 뿐 자기 기본값을 갖지 않는다.
(부분 지정된 요소 서식을 body 기준으로 채워 주는 것도 렌더러가 아니라 이 모듈의 일이다.)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .units import PAGE_SIZES, emu_to_mm, emu_to_pt, parse_length, parse_ratio

PROFILE_DIR = Path(__file__).resolve().parents[2] / "profiles"


class _Base(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class FontSpec(_Base):
    """문단/런 서식. None은 '지정 안 함' = 상속받는다.

    기본 상속 대상은 body이고, inherit로 다른 서식을 지정할 수 있다.
    (예: table_header는 table을 물려받아야 줄간격·크기를 두 번 적지 않는다.)
    """

    inherit: str | None = None
    east_asia: str | None = Field(None, alias="eastAsia")
    latin: str | None = Field(None, alias="ascii")
    size: int | None = None  # EMU
    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None
    color: str | None = None
    line_spacing: float | None = None
    space_before: int | None = None
    space_after: int | None = None
    align: str | None = None  # left | center | right | both | distribute
    indent: int | None = None
    first_line_indent: int | None = None
    keep_next: bool | None = None
    page_break_before: bool | None = None
    char_scale: float | None = None  # 장평 (1.0 = 100%)

    @field_validator("size", "space_before", "space_after", mode="before")
    @classmethod
    def _pt_length(cls, v: Any) -> Any:
        return None if v is None else parse_length(v, default_unit="pt")

    @field_validator("indent", "first_line_indent", mode="before")
    @classmethod
    def _mm_length(cls, v: Any) -> Any:
        return None if v is None else parse_length(v, default_unit="mm")

    @field_validator("line_spacing", "char_scale", mode="before")
    @classmethod
    def _ratio(cls, v: Any) -> Any:
        return None if v is None else parse_ratio(v)

    def merged_over(self, base: FontSpec) -> FontSpec:
        """base 위에 self를 덮어쓴 완전한 서식.

        값이 이미 EMU로 해석된 뒤이므로 model_construct로 검증을 건너뛴다.
        (다시 검증하면 "11pt"용 변환이 139700에 한 번 더 적용된다.)
        """
        data = base.model_dump()
        data.update({k: v for k, v in self.model_dump().items() if v is not None})
        return FontSpec.model_construct(**data)

    def resized(self, size: int) -> FontSpec:
        return self.model_copy(update={"size": size})


class Margin(_Base):
    top: int
    bottom: int
    left: int
    right: int
    header: int | None = None
    footer: int | None = None
    gutter: int | None = None

    @field_validator("*", mode="before")
    @classmethod
    def _mm(cls, v: Any) -> Any:
        return None if v is None else parse_length(v, default_unit="mm")


class Page(_Base):
    size: str = "A4"
    orientation: str = "portrait"
    width: int | None = None
    height: int | None = None
    margin: Margin

    @field_validator("width", "height", mode="before")
    @classmethod
    def _mm(cls, v: Any) -> Any:
        return None if v is None else parse_length(v, default_unit="mm")

    @model_validator(mode="after")
    def _resolve_size(self) -> Page:
        if self.width is None or self.height is None:
            key = self.size.upper()
            if key not in PAGE_SIZES:
                raise ValueError(f"알 수 없는 용지 크기: {self.size} (width/height를 직접 지정하세요)")
            w, h = PAGE_SIZES[key]
            object.__setattr__(self, "width", parse_length(w))
            object.__setattr__(self, "height", parse_length(h))
        if self.orientation.lower() in ("landscape", "가로") and self.width < self.height:
            object.__setattr__(self, "width", self.height)
            object.__setattr__(self, "height", parse_length(PAGE_SIZES[self.size.upper()][0]))
        return self

    @property
    def usable_width(self) -> int:
        """본문이 쓸 수 있는 가로 폭(EMU). 표 맞춤 계산의 기준선."""
        return self.width - self.margin.left - self.margin.right - (self.margin.gutter or 0)

    @property
    def usable_height(self) -> int:
        return self.height - self.margin.top - self.margin.bottom

    def landscape(self) -> Page:
        """같은 여백으로 가로 방향 전환한 페이지."""
        return self.model_copy(update={"width": self.height, "height": self.width,
                                       "orientation": "landscape"})


class NumberingLevel(_Base):
    """목록 깊이 하나의 말머리 규격.

    marker에 {n} {hangul} {alpha} {roman} {circled} 자리표시자를 쓰면 번호가 매겨진다.
    예: "□", "○", "{n}.", "{hangul}.", "({n})"
    bold는 말머리뿐 아니라 그 항목 문장 전체에 적용된다.
    space_after_level_change는 다음 항목의 단계가 바뀔 때만 주는 간격이다
    (같은 단계가 이어질 때는 space_after를 쓴다).
    """

    marker: str = ""
    ordered_marker: str | None = None  # 번호 있는 목록일 때만 쓰는 형식
    aliases: list[str] = Field(default_factory=list)
    # 원문에서 이 단계를 뜻하는 다른 문자(예: "□" 단계의 "■", "·" 단계의 "ㆍ"). 원문 말머리를
    # 그대로 쓸 때(text.keep_leading_markers) 그 문단을 몇 단계에 둘지 이걸로 정한다.
    indent: int = 0
    hanging: int | None = None
    space_before: int | None = None  # 이 단계가 새로 시작될 때 앞에 주는 간격
    space_after: int | None = None
    space_after_level_change: int | None = None
    space_after_level_change_max: int | None = None  # 지면에 여유가 있을 때까지 늘릴 값
    bold: bool | None = None
    size: int | None = None

    @field_validator("indent", "hanging", mode="before")
    @classmethod
    def _mm(cls, v: Any) -> Any:
        return None if v is None else parse_length(v, default_unit="mm")

    @field_validator("space_before", "space_after", "space_after_level_change",
                     "space_after_level_change_max", "size", mode="before")
    @classmethod
    def _pt(cls, v: Any) -> Any:
        return None if v is None else parse_length(v, default_unit="pt")

    def level_change_space(self, relaxed: bool) -> int | None:
        """단계가 바뀌는 자리의 간격. 지면에 여유가 있으면 넉넉한 값을 쓴다."""
        if relaxed and self.space_after_level_change_max is not None:
            return self.space_after_level_change_max
        if self.space_after_level_change is not None:
            return self.space_after_level_change
        return self.space_after


class TableRules(_Base):
    header_shading: str | None = None
    border_width: int | None = None
    border_color: str = "000000"
    cell_margin_x: int = 0
    cell_margin_y: int = 0
    cell_margin_x_min: int | None = None
    font_ladder: list[int] = Field(default_factory=list)
    char_scale_ladder: list[float] = Field(default_factory=list)  # 장평 후보 (1.0 = 100%)
    max_cell_lines: int = 0  # 한 셀이 이 줄 수를 넘으면 글자 크기를 낮춰 본다 (0=제한 없음)
    max_header_lines: int = 0  # 머리행이 이 줄 수를 넘으면 머리 문구를 축약한다 (0=축약 안 함)
    max_font_spread: int | None = None
    # 셀 하나만 유난히 작아지면(예: 표는 12pt인데 바쁜 셀만 9pt) 어색해 보인다.
    # 표 전체 크기와 가장 작은 셀 크기의 차이가 이 값을 넘으면 표 전체를 그만큼
    # 낮춘다(2026-09-29 사용자 요청 — "12pt·9pt는 불균형, 11pt·9pt가 낫다").
    note_markers: list[str] = Field(default_factory=list)  # 표 바로 아래 이 기호로 시작하면 주석
    note_marker: str | None = None  # 주석을 쓸 때 앞에 붙일 기호
    cell_list_markers: list[str] = Field(default_factory=list)
    # 표 셀 안 목록 항목의 말머리(깊이순, 원문에 말머리가 있으면 그것을 쓴다)
    safety_margin: float = 0.0
    repeat_header: bool = True
    keep_row_together: bool = True
    allow_landscape: bool = False
    align: str = "center"
    valign: str = "center"
    width_ratio: float = 1.0  # 사용가능폭 대비 표 목표 폭
    space_after: int | None = None  # 표 바로 다음 문단에 최소한 확보할 앞 간격
    row_height: int | None = None  # 행 최소 높이
    row_height_relaxed: int | None = None  # 지면에 여유가 있을 때의 행 최소 높이

    @field_validator("border_width", "cell_margin_x", "cell_margin_y",
                     "cell_margin_x_min", "space_after", "max_font_spread", mode="before")
    @classmethod
    def _len(cls, v: Any) -> Any:
        return None if v is None else parse_length(v, default_unit="pt")

    @field_validator("row_height", "row_height_relaxed", mode="before")
    @classmethod
    def _mm(cls, v: Any) -> Any:
        return None if v is None else parse_length(v, default_unit="mm")

    def min_row_height(self, relaxed: bool) -> int | None:
        if relaxed and self.row_height_relaxed is not None:
            return self.row_height_relaxed
        return self.row_height

    @field_validator("font_ladder", mode="before")
    @classmethod
    def _ladder(cls, v: Any) -> Any:
        if v is None:
            return []
        return [parse_length(x, default_unit="pt") for x in v]

    @field_validator("char_scale_ladder", mode="before")
    @classmethod
    def _scales(cls, v: Any) -> Any:
        return [] if v is None else [parse_ratio(x) for x in v]

    @field_validator("safety_margin", "width_ratio", mode="before")
    @classmethod
    def _ratio(cls, v: Any) -> Any:
        return parse_ratio(v)


class TextRules(_Base):
    gaechosik: bool = False  # 개조식(명사형 종결) 변환 여부
    noun_ending: bool = False  # 가능하면 "~함"이 아니라 명사로 끝낸다 ("재설계하였음" → "재설계")
    split_long_sentences: bool = False
    max_sentence_chars: int = 0
    merge_short_items: bool = False  # 짧은 항목끼리 "및"으로 합치기 (max_sentence_chars 기준)
    rules: list[str] = Field(default_factory=list)
    keep_original_in_tables: bool = True
    date_format: str = ""  # 비우면 rules/notation.yaml 의 형식을 쓴다
    headings_as_levels: bool = False  # ##/### 제목을 1./□ 단락 체계로 접어 넣을지
    normalize_levels: bool = False
    # 접은 뒤 가장 얕은 단계를 0으로 당긴다. 문서가 ###(h3)부터 시작하면 첫 문장이 □ 단계(0.4cm)로
    # 들여쓰여 나오기 때문 — 첫 문장은 0cm, 그 아래가 0.4cm, 그 아래가 0.8cm여야 한다.
    leading_markers: list[str] = Field(default_factory=list)
    # 원문에 이미 문자로 쳐 둔 말머리("□ ", "- ", "ㆍ", "①", "※"). "1." "1)" "(1)" "가."
    # 같은 번호는 목록에 안 적어도 알아본다. 이게 있는 제목·항목에 프로파일이 또
    # 말머리를 붙이면 "ㅁ□"/"- -"처럼 겹친다.
    keep_leading_markers: bool = False
    # true: 원문 말머리를 그대로 쓰고 프로파일 말머리를 붙이지 않는다(2026-09-29 사용자
    #       원칙 — "이미 쓴 글머리 기호는 바꾸지 말 것").
    # false: 원문 말머리를 떼고 프로파일 말머리로 통일한다.
    polish: str | None = None
    # 문구 다듬기 기본값(rules | llm | none). CLI --polish를 주면 그쪽이 이긴다.
    note_marks: list[str] = Field(default_factory=list)
    note_size_delta: int | None = None
    # 참고사항 표시(※ 등)로 시작하는 문단·항목은 본문보다 이만큼 작게 쓴다(2026-09-29 사용자:
    # "당구장 표시는 참고사항이니 항상 본문보다 2pt 작게"). 표 바로 아래 주석은 별개(fonts.table_note).
    level_bold: bool = True
    # true: numbering[].bold가 그 단계 문장 **전체**를 굵게 한다(사내 규격의 "1.·□ 문장은 굵은체").
    # false: 단계 굵게를 안 쓴다 — 굵은 글씨는 원문에서 굵었던 것과 제목에서 온 항목만
    #        (2026-09-29 사용자: Confluence에서 굵지 않던 글씨까지 굵게 나옴).
    table_captions: bool = False
    # 표 바로 위에 "[사업현황]"처럼 꺾쇠로 감싼 문단이 있으면 Table.caption으로 옮긴다.
    # 안 옮기면 제목 접기에서 ListItem이 되어 "- [사업현황]"처럼 말머리가 붙는다 —
    # 원래 표 제목이지 항목이 아니다.
    auto_markers: bool = True
    # true: 원문에 말머리가 없는 제목·문단에 프로파일 말머리(1. □ -)를 붙인다 — 정리 안 된 글을
    #       정형 보고서로 새로 만들 때.
    # false: 원문에 말머리가 없으면 안 붙인다(단계별 들여쓰기만 유지) — 이미 말머리를 구분해 쓴
    #        문서용(2026-09-29 사용자: Confluence). 진짜 목록(<ul>/<ol>, -, 1.)의 항목은 대상이 아니다.
    plain_paragraph_level: int | None = None
    # 제목도 말머리도 전혀 없는 글(정리 안 된 메모)에서 auto_markers가 켜져 있으면 각 문단을 이 단계
    # (numbering 인덱스, 1 = □)의 항목으로 만든다. 비우면 안 만든다.
    no_marker_openers: list[str] = Field(default_factory=list)
    # 이 꺾쇠·괄호로 시작하는 제목·문단·항목에는 프로파일 말머리(□, - 등)를 붙이지 않는다
    # (2026-09-29 사용자: "【사업현황】" 앞에 □가 붙음 — 모든 문서 공통). 문서 어디서나 적용.
    page_title_strip: list[str] = Field(default_factory=list)
    # 쪽 제목(불러온 연결 문서·입력마다 새 쪽)의 앞에서 떼어 낼 정규식 — "(첨부 1) 세부 계획" → "세부 계획"
    # (2026-09-30 사용자). 떼고 나면 빈 제목이 되는 경우는 그대로 둔다.
    note_indent: int | None = None
    # ※ 참고사항 문단은 바로 윗줄 문단의 들여쓰기보다 이만큼 더 들여쓴다(2026-09-29 사용자: +0.4cm).

    @field_validator("note_size_delta", mode="before")
    @classmethod
    def _delta(cls, v: Any) -> Any:
        return None if v is None else parse_length(v, default_unit="pt")

    @field_validator("note_indent", mode="before")
    @classmethod
    def _indent(cls, v: Any) -> Any:
        return None if v is None else parse_length(v, default_unit="mm")


class Choices(_Base):
    """--ask 로 물어볼 때 보여 줄 선택지. 코드가 아니라 프로파일이 정한다."""

    font: list[str] = Field(default_factory=list)
    size: list[str] = Field(default_factory=list)
    line_spacing: list[str] = Field(default_factory=list)
    title_size: list[str] = Field(default_factory=list)
    table_size: list[str] = Field(default_factory=list)
    body_scale: list[str] = Field(default_factory=list)   # 본문 장평 선택지 ("95%")
    table_scale: list[str] = Field(default_factory=list)  # 표 장평 선택지


class Profile(_Base):
    name: str = "default"
    description: str | None = None
    label: str | None = None         # 웹 화면 서식 선택에 보이는 이름("보고서", "Confluence 변환")
    preset_order: int | None = None  # 있으면 웹 화면의 서식(preset) 목록에 이 순서로 나온다
    template: str | None = None  # 사내 template.docx 경로 (styles.xml 승계)
    page: Page
    fonts: dict[str, FontSpec]
    numbering: list[NumberingLevel] = Field(default_factory=list)
    tables: TableRules = Field(default_factory=TableRules)
    text: TextRules = Field(default_factory=TextRules)
    choices: Choices = Field(default_factory=Choices)

    @model_validator(mode="after")
    def _require_body(self) -> Profile:
        if "body" not in self.fonts:
            raise ValueError("프로파일에 fonts.body 가 반드시 있어야 합니다 (다른 서식의 상속 기준).")
        body = self.fonts["body"]
        missing = [f for f in ("east_asia", "latin", "size") if getattr(body, f) is None]
        if missing:
            raise ValueError(f"fonts.body 에 다음 항목이 빠졌습니다: {missing}")
        return self

    def font(self, key: str) -> FontSpec:
        """요소용 완전한 서식. 지정되지 않은 항목은 inherit(기본 body)에서 상속."""
        body = self.fonts["body"]
        if key == "body" or key not in self.fonts:
            return body

        chain: list[FontSpec] = []
        seen: set[str] = set()
        current: str | None = key
        while current and current != "body" and current in self.fonts and current not in seen:
            seen.add(current)
            spec = self.fonts[current]
            chain.append(spec)
            current = spec.inherit

        resolved = body
        for spec in reversed(chain):
            resolved = spec.merged_over(resolved)
        return resolved

    def has_font(self, key: str) -> bool:
        return key in self.fonts

    def numbering_level(self, depth: int) -> NumberingLevel:
        """깊이가 프로파일에 정의된 단계보다 깊으면 마지막 단계를 재사용한다."""
        if not self.numbering:
            return NumberingLevel()
        return self.numbering[min(depth, len(self.numbering) - 1)]

    def marker_depths(self) -> dict[str, int]:
        """말머리 문자 → 단계. 번호 자리표시자({n} 등)가 있는 단계는 문자로 못 가리키므로 뺀다."""
        depths: dict[str, int] = {}
        for depth, level in enumerate(self.numbering):
            for marker in [level.marker, *level.aliases]:
                if marker and "{" not in marker:
                    depths.setdefault(marker, depth)
        return depths

    def with_overrides(
        self,
        *,
        font: str | None = None,
        size: str | None = None,
        line_spacing: str | float | None = None,
        title_size: str | None = None,
        table_size: str | None = None,
        margin: str | None = None,
        body_scale: str | float | None = None,
        table_scale: str | float | None = None,
    ) -> Profile:
        """변환 직전에 사용자가 고른 값을 프로파일 위에 덮어쓴다.

        프로파일 파일은 그대로 두고 이번 변환에만 적용된다
        (계속 쓰고 싶으면 CLI의 --save-profile 로 저장한다).
        """
        fonts = dict(self.fonts)

        if font:
            for key, spec in fonts.items():
                if key == "code":  # 코드는 고정폭이어야 해서 함께 바꾸지 않는다
                    continue
                fonts[key] = spec.model_copy(update={"east_asia": font, "latin": font})

        if size:
            fonts["body"] = fonts["body"].model_copy(
                update={"size": parse_length(size, default_unit="pt")})
        if line_spacing is not None:
            fonts["body"] = fonts["body"].model_copy(
                update={"line_spacing": parse_ratio(line_spacing)})
        if title_size and "title" in fonts:
            fonts["title"] = fonts["title"].model_copy(
                update={"size": parse_length(title_size, default_unit="pt")})

        if body_scale:  # 본문 장평 — 말머리 항목·일반 문단이 body 서식을 쓴다
            fonts["body"] = fonts["body"].model_copy(update={"char_scale": parse_ratio(body_scale)})

        tables = self.tables
        if table_scale:  # 표 장평은 한 값으로 고정(더 좁히지 않음)
            tables = tables.model_copy(update={"char_scale_ladder": [parse_ratio(table_scale)]})
        if table_size:
            value = parse_length(table_size, default_unit="pt")
            for key in ("table", "table_header"):
                if key in fonts:
                    fonts[key] = fonts[key].model_copy(update={"size": value})
            ladder = [step for step in self.tables.font_ladder if step < value]
            tables = tables.model_copy(update={"font_ladder": [value] + ladder})

        page = self.page
        if margin:
            values = [parse_length(part.strip(), default_unit="mm")
                      for part in str(margin).split(",")]
            if len(values) == 1:
                values *= 4
            if len(values) != 4:
                raise ValueError("여백은 '25mm' 또는 '위,아래,좌,우' 형식으로 주세요")
            top, bottom, left, right = values
            page = page.model_copy(update={"margin": page.margin.model_copy(
                update={"top": top, "bottom": bottom, "left": left, "right": right})})

        return self.model_copy(update={"fonts": fonts, "tables": tables, "page": page})

    def table_font_ladder(self) -> list[int]:
        """표 글자 크기 축소 사다리. 비어 있으면 표 서식 크기 하나만."""
        return self.tables.font_ladder or [self.font("table").size]

    def table_steps(self) -> list[tuple[int, float]]:
        """(글자 크기, 장평) 후보를 글자 폭이 덜 줄어드는 순서로.

        12pt·90%(폭 10.8)가 11pt·100%(폭 11)보다 좁듯이 (예시) 크기와 장평을 따로
        내리지 않고 실제 글자 폭(크기 × 장평)으로 줄 세운다 — 필요한 만큼만 줄이기 위해.
        """
        scales = self.tables.char_scale_ladder or [self.font("table").char_scale or 1.0]
        steps = {(size, scale) for size in self.table_font_ladder() for scale in scales}
        return sorted(steps, key=lambda s: (-s[0] * s[1], -s[0]))


def load_profile(name_or_path: str | Path) -> Profile:
    """이름('default') 또는 경로('./my.yaml') 로 프로파일을 읽는다."""
    path = _profile_file(name_or_path)
    data = _apply_numbering_all(_read_profile_data(path, seen=set()))
    profile = Profile.model_validate(data)
    if profile.template:
        tpl = Path(profile.template)
        if not tpl.is_absolute():
            tpl = path.parent / tpl
        profile = profile.model_copy(update={"template": str(tpl)})
    return profile


def _profile_file(name_or_path: str | Path) -> Path:
    path = Path(name_or_path)
    if not path.suffix:
        path = PROFILE_DIR / f"{name_or_path}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"프로파일을 찾을 수 없음: {path}")
    return path


def _read_profile_data(path: Path, seen: set[Path]) -> dict:
    """`extends: default`가 있으면 부모 YAML 위에 덮어쓴다. 검증(단위 변환) **전의**
    원본 dict끼리 합쳐야 한다 — 검증된 모델을 합치면 EMU 값이 다시 변환된다(겪은 함정)."""
    resolved = path.resolve()
    if resolved in seen:
        raise ValueError(f"프로파일 extends가 순환함: {path}")
    seen.add(resolved)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    parent = data.pop("extends", None)
    if parent is None:
        return data
    parent_path = _profile_file(parent if Path(parent).suffix == "" else path.parent / parent)
    return _deep_merge(_read_profile_data(parent_path, seen), data)


def _apply_numbering_all(data: dict) -> dict:
    """`numbering_all:`에 적은 값을 모든 단계에 덮어쓴다 — numbering 목록은 통째로 덮어써지므로
    (단계가 꼬이지 않게) 상속한 프로파일이 "간격만 전부 0"처럼 일부만 바꾸려면 이게 필요하다.
    합친 뒤·검증 전의 dict에서 한다(겪은 함정: 검증된 값을 다시 검증하면 단위가 두 번 변환됨)."""
    common = data.pop("numbering_all", None)
    if common:
        data["numbering"] = [{**level, **common} for level in data.get("numbering") or []]
    return data


def _deep_merge(base: dict, override: dict) -> dict:
    """dict는 키별로 합치고, 목록·값은 통째로 덮어쓴다(numbering 목록을 섞으면 단계가 꼬인다)."""
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def dump_profile(profile: Profile) -> str:
    """프로파일을 사람이 읽고 다시 불러올 수 있는 YAML로.

    내부적으로는 EMU로 들고 있으므로 pt/mm/% 로 되돌려 쓴다.
    (EMU 숫자를 그대로 쓰면 다시 읽을 때 단위 변환이 한 번 더 걸려 망가진다.)
    """
    data: dict[str, Any] = {"name": profile.name}
    if profile.description:
        data["description"] = profile.description
    if profile.label:
        data["label"] = profile.label
    if profile.preset_order is not None:
        data["preset_order"] = profile.preset_order
    if profile.template:
        data["template"] = profile.template

    margin = {k: _mm(v) for k, v in profile.page.margin.model_dump().items() if v is not None}
    data["page"] = {"size": profile.page.size, "orientation": profile.page.orientation,
                    "margin": margin}
    data["fonts"] = {key: _dump_font(spec) for key, spec in profile.fonts.items()}
    data["numbering"] = [_strip(_dump_level(level)) for level in profile.numbering]
    data["tables"] = _dump_tables(profile.tables)
    text = profile.text.model_dump()
    if text["note_indent"] is not None:
        text["note_indent"] = _mm(text["note_indent"])
    text["note_size_delta"] = _pt(text["note_size_delta"])  # EMU 그대로 쓰면 다시 읽을 때 또 변환된다
    data["text"] = _strip(text)
    choices = _strip(profile.choices.model_dump())
    if choices:
        data["choices"] = choices
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100)


def _pt(value: int | None) -> str | None:
    return None if value is None else f"{emu_to_pt(value):.10g}pt"


def _mm(value: int | None) -> str | None:
    return None if value is None else f"{emu_to_mm(value):.10g}mm"


def _pct(value: float | None) -> str | None:
    return None if value is None else f"{value * 100:.10g}%"


def _strip(data: dict) -> dict:
    return {k: v for k, v in data.items() if v is not None and v != [] and v != {}}


def _dump_font(spec: FontSpec) -> dict:
    data = spec.model_dump(by_alias=True)
    for key in ("size", "space_before", "space_after"):
        data[key] = _pt(data[key])
    for key in ("indent", "first_line_indent"):
        data[key] = _mm(data[key])
    data["line_spacing"] = _pct(data["line_spacing"])
    data["char_scale"] = _pct(data["char_scale"])
    return _strip(data)


def _dump_level(level: NumberingLevel) -> dict:
    data = level.model_dump()
    for key in ("indent", "hanging"):
        data[key] = _mm(data[key])
    for key in ("space_before", "space_after", "space_after_level_change",
                "space_after_level_change_max", "size"):
        data[key] = _pt(data[key])
    return data


def _dump_tables(rules: TableRules) -> dict:
    data = rules.model_dump()
    for key in ("border_width", "cell_margin_y", "space_after", "max_font_spread"):
        data[key] = _pt(data[key])
    for key in ("cell_margin_x", "cell_margin_x_min", "row_height", "row_height_relaxed"):
        data[key] = _mm(data[key])
    data["font_ladder"] = [_pt(step) for step in rules.font_ladder]
    data["char_scale_ladder"] = [_pct(step) for step in rules.char_scale_ladder]
    data["safety_margin"] = _pct(rules.safety_margin)
    return _strip(data)
