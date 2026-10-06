"""웹 화면의 변환 옵션 ↔ 프로파일.

두 축을 따로 고른다.
- **서식(preset)** — 출력 설정의 "보고서 / Confluence 변환 / 사용자 설정". 여백·글꼴·크기·줄간격·
  표 글자 사다리. `preset_order`가 있는 프로파일이 목록에 나오고(새 서식은 yaml 한 장 추가로),
  사용자 설정은 고른 서식을 출발점으로 값을 덮어쓴다(`with_overrides`).
- **규칙** — 말머리·문장 다듬기·표 제목 등 `text.*`. 자동 판단 또는 직접 선택.
최종 프로파일 = 서식 프로파일에 규칙(text)을 끼운 것.

화면의 체크박스 하나하나는 **프로파일 값 하나**다(text.*, tables.*). 그래서 옵션을 늘릴 때 변환
코드에 분기를 추가하지 않고 여기 목록에 한 줄만 넣으면 된다 — 값은 프로파일을 model_copy로
덮어쓸 뿐이다(프로파일 파일은 그대로). 체크박스 이름·설명도 여기가 원본이고 화면은 받아 그린다.

"자동 판단"은 읽어 들인 문서를 보고 두 가지를 따로 정한다.
- 정리 정도: 제목·말머리가 있는 문단 비율 → 원문 유지(confluence 규칙) / 새로 정리(default 규칙:
  말머리 생성 + 문장 다듬기, LLM은 허용했을 때만). Confluence 페이지는 항상 원문 유지(사용자 규칙).
- 분량: 글자 수·표 개수·입력 개수가 많은데 보고서 서식을 골랐으면 Confluence 변환 서식을 권한다
  (서식은 사용자가 고른 것이 이긴다 — 조용히 바꾸지 않는다).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as _date

from ..ir import Document, Heading, ListItem, Paragraph, Table, is_blank, iter_tables, plain
from ..profile import PROFILE_DIR, Profile, load_profile
from ..transform.structure import has_leading_marker
from ..units import emu_to_mm, fmt_pt

# (화면 키, 화면 이름, 설명, 바꾸는 프로파일 text 값들)
# 화면에 없는 규칙(제목을 1.→□→- 단계로 접기, 맨 바깥 단계 0cm, 1.·□ 문장 굵게, 표 위 【…】를 표 제목으로)은
# "규칙 기본값"으로 고른 프로파일의 값을 그대로 쓴다(2026-09-29 사용자: 따로 고를 필요 없음).
MARKER_TOGGLES = [
    ("keep_leading_markers", "원문 말머리 그대로 쓰기",
     "원문에 이미 친 1. □ - ㆍ ① ※ 등을 바꾸지 않음", ("keep_leading_markers",)),
    ("auto_markers", "말머리 없는 문단에 말머리 만들기",
     "정리 안 된 글을 새 보고서로 만들 때 켬. 끄면 들여쓰기만 맞춤", ("auto_markers",)),
]
POLISH_TOGGLES = [
    ("endings", "어미를 개조식 또는 명사로 끝내기",
     "~합니다 → ~함, 인덱스를 재설계하였습니다 → 인덱스 재설계", ("gaechosik", "noun_ending")),
    ("split_long_sentences", "긴 문장 나누기", "", ("split_long_sentences",)),
    ("merge_short_items", "짧은 항목 \"및\"으로 합치기", "같은 단계의 짧은 항목 둘씩",
     ("merge_short_items",)),
]
TABLE_TOGGLES: list = []
_TOGGLES = {key: fields for key, _, _, fields in MARKER_TOGGLES + POLISH_TOGGLES + TABLE_TOGGLES}
TABLE_CHOICES = {"align": [("right", "오른쪽"), ("center", "가운데"), ("left", "왼쪽")]}
FORMAT_KEYS = ("font", "size", "line_spacing", "title_size", "table_size", "body_scale", "table_scale")
MARGIN_SIDES = ("top", "bottom", "left", "right")
_HEAVY_PRESET = "confluence"  # 내용이 많을 때 권하는 서식

_STRUCTURED_RATIO = 0.3  # 제목·말머리가 있는 문단이 이 비율 이상이면 "정리된 문서"
_HEAVY_CHARS = 3000      # 본문 글자 수가 이 이상이면 "내용 많은 문서"
_HEAVY_TABLES = 3        # 표가 이 개수 이상이어도


@dataclass
class Decision:
    profile: Profile
    polish: str
    llm: bool = False
    reasons: list[str] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


def profile_names() -> list[str]:
    """읽을 수 있는 프로파일만 — profiles/에 망가진 yaml이 하나 있어도 화면 전체가 멈추지 않게.
    못 읽은 것은 profile_errors()로 화면에 알린다."""
    names = []
    for path in sorted(PROFILE_DIR.glob("*.yaml")):
        try:
            load_profile(path.stem)
            names.append(path.stem)
        except Exception:  # 오류 내용은 profile_errors()가 다시 읽어서 보여 준다
            continue
    return names


def profile_errors() -> list[str]:
    errors = []
    for path in sorted(PROFILE_DIR.glob("*.yaml")):
        try:
            load_profile(path.stem)
        except Exception as exc:
            errors.append(f"{path.name}: {str(exc).splitlines()[0][:200]}")
    return errors


def profile_info(name: str) -> dict:
    """화면이 서식 선택지·체크박스 기본값을 채우는 데 쓰는 프로파일 요약."""
    prof = load_profile(name)
    text = prof.text
    fmt = format_values(prof)
    return {
        "name": name,
        "label": prof.label or name,
        "preset_order": prof.preset_order,
        "description": prof.description or name,
        "summary": (f"{_margins(fmt)} · {fmt['font']} · "
                    f"제목 {fmt['title_size']} · 본문 {fmt['size']}{_scale(fmt['body_scale'])}"
                    f"·줄간격 {fmt['line_spacing']} · 표 {fmt['table_size']}{_scale(fmt['table_scale'])}"
                    + ("" if len(prof.table_font_ladder()) == 1 else "부터")),
        "text": {key: all(bool(getattr(text, f)) for f in fields) for key, fields in _TOGGLES.items()},
        "polish": "none" if text.polish == "none" else "rules",
        "llm": text.polish == "llm",
        "tables": {"allow_landscape": prof.tables.allow_landscape, "align": prof.tables.align},
        "format": fmt,
        "choices": {k: [str(v) for v in values] for k, values in prof.choices.model_dump().items()},
    }


def _ratio(value: float) -> str:
    text = f"{value:g}"
    return text if "." in text else text + ".0"  # 1 → "1.0" (선택지 표기와 맞춤)


def _margins(fmt: dict) -> str:
    t, b, l, r = (fmt[f"margin_{s}"] for s in MARGIN_SIDES)
    if t == b == l == r:
        return f"여백 {t}cm"
    return f"여백 위·아래 {_pair(t, b)}cm, 좌·우 {_pair(l, r)}cm"


def _scale(pct: str) -> str:
    return "" if pct == "100%" else f"(장평 {pct})"


def _pct(ratio: float) -> str:
    return f"{round(ratio * 100)}%"


def _pair(a: str, b: str) -> str:
    return a if a == b else f"{a}·{b}"


def format_values(prof: Profile) -> dict:
    body = prof.font("body")
    margin = prof.page.margin
    return {
        "font": body.east_asia or body.latin or "",
        "size": fmt_pt(body.size),
        "line_spacing": _ratio(body.line_spacing) if body.line_spacing else "",
        "title_size": fmt_pt(prof.font("title").size) if prof.has_font("title") else "",
        "table_size": fmt_pt(prof.table_font_ladder()[0]),
        "body_scale": _pct(body.char_scale or 1.0),
        "table_scale": _pct(prof.table_steps()[0][1]),
        **{f"margin_{side}": f"{emu_to_mm(getattr(margin, side)) / 10:g}"
           for side in MARGIN_SIDES},
    }


def presets() -> list[str]:
    """서식 선택 목록(preset_order 순). 사용자 설정("custom")은 화면이 따로 붙인다."""
    found = [(load_profile(n).preset_order, n) for n in profile_names()]
    return [n for order, n in sorted((o, n) for o, n in found if o is not None)]


def schema() -> dict:
    def rows(items):
        return [{"key": k, "label": label, "help": help_} for k, label, help_, _ in items]

    return {"markers": rows(MARKER_TOGGLES), "polish": rows(POLISH_TOGGLES),
            "tables": rows(TABLE_TOGGLES), "presets": presets(),
            "table_align": [{"value": v, "label": label} for v, label in TABLE_CHOICES["align"]],
            "auto": {"chars": _HEAVY_CHARS, "tables": _HEAVY_TABLES, "ratio": _STRUCTURED_RATIO}}


def format_profile(options: dict) -> Profile:
    """출력 설정의 서식 선택 → 서식 프로파일. 사용자 설정은 출발 서식에서 바꾼 값만 덮어쓴다."""
    preset = options.get("preset") or "default"
    if preset != "custom":
        if preset not in presets():
            raise ValueError(f"알 수 없는 서식: {preset}")
        return load_profile(preset)
    custom = options.get("custom") or {}
    base = custom.get("base") or "default"
    if base not in presets():
        raise ValueError(f"알 수 없는 출발 서식: {base}")
    prof = load_profile(base)
    current = format_values(prof)
    changed = {k: str(v).strip() for k, v in custom.items()
               if k in current and str(v).strip() and str(v).strip() != current[k]}
    overrides = {k: v for k, v in changed.items() if k in FORMAT_KEYS}
    for key in ("body_scale", "table_scale"):  # 장평 "95"처럼 %를 빼고 적어도 95%로
        value = overrides.get(key, "")
        if value and not value.endswith("%") and value.replace(".", "", 1).isdigit() and float(value) > 3:
            overrides[key] = value + "%"
    if any(f"margin_{side}" in changed for side in MARGIN_SIDES):
        overrides["margin"] = ",".join(
            f"{changed.get(f'margin_{side}', current[f'margin_{side}'])}cm" for side in MARGIN_SIDES)
    try:
        return prof.with_overrides(**overrides) if overrides else prof
    except ValueError as exc:
        raise ValueError(f"사용자 설정 값을 읽을 수 없습니다({exc}) — 크기는 12pt, 줄간격은 1.3, "
                         "여백은 cm 숫자로 적어 주세요") from exc


_ORIGINAL_FORMAT_KINDS = ("confluence", "docx")


def manual_rules(prof: Profile, options: dict) -> tuple[Profile, str, bool]:
    """직접 선택 모드: "규칙 기본값" 프로파일의 규칙(text)에 체크박스 값을 덮어써 서식 프로파일에 끼운다.
    문장 다듬기: polish = none | rules(파이썬 규칙), llm = LLM 맞춤법·어조 다듬기(따로 켬)."""
    base = options.get("rules_base")
    text = load_profile(base).text if base in presets() else prof.text
    text_updates = {field: bool(v) for key, v in (options.get("text") or {}).items()
                    for field in _TOGGLES.get(key, ())}
    table_opts = options.get("tables") or {}
    table_updates = {}
    if "allow_landscape" in table_opts:
        table_updates["allow_landscape"] = bool(table_opts["allow_landscape"])
    if table_opts.get("align") in dict(TABLE_CHOICES["align"]):
        table_updates["align"] = table_opts["align"]
    prof = prof.model_copy(update={"text": text.model_copy(update=text_updates),
                                   "tables": prof.tables.model_copy(update=table_updates)})
    polish = options.get("polish") or ("none" if text.polish == "none" else "rules")
    if polish not in ("none", "rules", "llm"):
        raise ValueError(f"알 수 없는 문장 다듬기 방식: {polish}")
    llm = bool(options.get("llm")) or polish == "llm"
    return prof, ("rules" if polish == "llm" else polish), llm


def build_profile(options: dict, docs: list[Document], kinds: list[str], *,
                  llm_ready: bool) -> tuple[Profile, str, bool, Decision | None]:
    """(최종 프로파일, 파이썬 규칙 polish none|rules, LLM 켬 여부, 자동 판단 내용)"""
    fmt = format_profile(options)
    if options.get("mode") == "manual":
        prof, polish, llm = manual_rules(fmt, options)
        return _original_bold(prof, kinds), polish, llm, None
    decision = auto_decide(docs, kinds, allow_llm=bool(options.get("allow_llm")),
                           llm_ready=llm_ready, preset=fmt)
    return _original_bold(decision.profile, kinds), decision.polish, decision.llm, decision


def _original_bold(prof: Profile, kinds: list[str]) -> Profile:
    """원문 서식이 있는 입력(Confluence·Word)이 있으면 "1.·□ 문장 전체 굵게"(보고서 규격)를 끈다 —
    원문에서 굵은 글씨만 굵게(2026-09-30 사용자: 규칙 기본값을 '보고서'로 두자 본문이 전부 굵어짐)."""
    if prof.text.level_bold and any(k in _ORIGINAL_FORMAT_KINDS for k in kinds):
        return prof.model_copy(update={"text": prof.text.model_copy(update={"level_bold": False})})
    return prof


def auto_decide(docs: list[Document], kinds: list[str], *, allow_llm: bool,
                llm_ready: bool, preset: Profile | None = None) -> Decision:
    """규칙만 정한다. 서식은 사용자가 고른 preset 그대로 — 내용이 많은데 보고서 서식이면
    Confluence 변환 서식을 **권하기만** 한다(조용히 바꾸지 않음)."""
    preset = preset or load_profile("default")
    base = load_profile("default")
    blocks = [b for d in docs for b in d.blocks
              if isinstance(b, (Heading, Paragraph, ListItem)) and not is_blank(plain(b.runs))]
    marked = sum(1 for b in blocks if not isinstance(b, Paragraph)
                 or has_leading_marker(b.runs, base.text.leading_markers))
    ratio = marked / len(blocks) if blocks else 1.0
    chars = sum(len(plain(b.runs)) for b in blocks)
    tables = sum(1 for d in docs for _ in iter_tables(d))

    reasons: list[str] = []
    if "confluence" in kinds:
        structured = True
        reasons.append("Confluence 페이지가 있어 원문 문장·말머리를 그대로 둠(문장 다듬기 안 함)")
    else:
        structured = ratio >= _STRUCTURED_RATIO
        if structured:
            reasons.append(f"제목·말머리가 있는 문단 {ratio:.0%} → 정리된 문서로 보고 원문 문장·말머리 유지")
        else:
            reasons.append(f"제목·말머리가 있는 문단 {ratio:.0%} → 정리 안 된 글로 보고 "
                           "말머리를 만들고 문장을 개조식으로 다듬음")

    heavy = chars >= _HEAVY_CHARS or tables >= _HEAVY_TABLES or len(docs) >= 2
    heavy_name = _HEAVY_PRESET if _HEAVY_PRESET in presets() else None
    if heavy and heavy_name and preset.name == "default":
        label = load_profile(heavy_name).label or heavy_name
        reasons.append(f"본문 {chars:,}자·표 {tables}개·입력 {len(docs)}개로 내용이 많음 — "
                       f"'{label}' 서식이 더 잘 맞을 수 있음(서식은 고른 그대로 둠)")

    text_profile = load_profile("confluence" if structured else "default")
    prof = preset.model_copy(update={"text": text_profile.text})
    polish = "none" if structured else "rules"
    llm = allow_llm and llm_ready
    if llm:
        reasons.append("LLM으로 맞춤법·띄어쓰기·어조·모호한 표현을 다듬음(문장 끝 형태는 그대로)")
    elif allow_llm:
        reasons.append("LLM이 설정되지 않아 LLM 다듬기는 건너뜀")
    return Decision(profile=prof, polish=polish, llm=llm, reasons=reasons,
                    summary={"structured": structured, "heavy": heavy, "ratio": round(ratio, 2),
                             "chars": chars, "tables": tables, "polish": polish})


def date_text(value: str | None, profile: Profile) -> str | None:
    """화면의 날짜 선택 → 제목 아래 날짜 줄. "" = 넣지 않음, "today", "2026-10-01"."""
    value = (value or "").strip()
    if not value:
        return None
    if value in ("today", "오늘"):
        return "today"
    try:
        chosen = _date.fromisoformat(value)
    except ValueError:
        return value  # 사용자가 직접 쓴 표기 그대로
    fmt = profile.text.date_format or "{y}. {m}. {d}"
    return fmt.format(y=chosen.year, m=chosen.month, d=chosen.day)
