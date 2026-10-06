"""개조식(명사형 종결) 변환.

규칙 표는 rules/endings.yaml 에 있고 이 파일은 적용기다.
표에 없는 어미는 한글 자모를 합성해 일반 규칙으로 처리한다.
  받침 없음 → ㅁ 받침 추가   (진행하 → 진행함, 확인되 → 확인됨)
  ㄹ 받침    → ㄻ 으로       (만들 → 만듦)
  그 밖      → '음' 덧붙임   (먹 → 먹음)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

RULES_DIR = Path(__file__).resolve().parents[3] / "rules"

_HANGUL_BASE = 0xAC00
_HANGUL_LAST = 0xD7A3
_JONG_M = 16   # ㅁ
_JONG_R = 8    # ㄹ
_JONG_RM = 10  # ㄻ

# 문장 경계. 숫자 뒤의 마침표는 날짜·항목번호("2026. 9. 1.")라 자르지 않는다.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])(?<![0-9][.!?])\s+")
_HAS_CONTENT = re.compile(r"[0-9A-Za-z가-힣]")


def _merge_punct_only_parts(parts) -> list[str]:
    """_SENTENCE_SPLIT은 글자 없이 문장부호만 있는 조각도 "문장"으로 센다 —
    예를 들어 항목이 ". 입사예정시기"처럼 마침표로 시작하면(Confluence 원문에
    "."을 말머리로 쓴 경우) [".", "입사예정시기"]로 쪼개져 빈 항목("□\t.")이
    따로 생긴다. 글자·숫자가 없는 조각은 독립된 문장으로 보지 않고 옆 조각에
    붙인다."""
    out: list[str] = []
    pending = ""
    for part in parts:
        if not _HAS_CONTENT.search(part):
            pending = f"{pending} {part}".strip() if pending else part
            continue
        out.append(f"{pending} {part}".strip() if pending else part)
        pending = ""
    if pending:
        if out:
            out[-1] = f"{out[-1]} {pending}".strip()
        else:
            out.append(pending)
    return out


@dataclass
class Change:
    before: str
    after: str
    rule: str


@dataclass
class EndingRules:
    suffix_rules: list[tuple[str, str]] = field(default_factory=list)
    nominalize: list[str] = field(default_factory=list)
    keep_as_is: list[str] = field(default_factory=list)
    replacements: list[tuple[str, str]] = field(default_factory=list)
    connectives: list[str] = field(default_factory=list)
    noun_endings: list[tuple[str, str]] = field(default_factory=list)
    drop_particles: list[str] = field(default_factory=list)
    adverb_to_noun: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path | None = None) -> EndingRules:
        path = path or RULES_DIR / "endings.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(
            suffix_rules=[(a, b) for a, b in data.get("suffix_rules", [])],
            nominalize=list(data.get("nominalize", [])),
            keep_as_is=list(data.get("keep_as_is", [])),
            replacements=[(a, b) for a, b in data.get("replacements", [])],
            connectives=list(data.get("connectives", [])),
            noun_endings=[(a, b) for a, b in data.get("noun_endings", [])],
            drop_particles=list(data.get("drop_particles", [])),
            adverb_to_noun=list(data.get("adverb_to_noun", [])),
        )


class Gaechosik:
    """문장 단위 개조식 변환기.

    noun_ending이 켜져 있으면 가능한 문장은 명사로 끝내고("인덱스 재설계"),
    안 되는 문장만 "~음/~함"으로 끝낸다. 명사 종결 문체에서는 마침표를 붙이지 않는다.
    """

    def __init__(self, rules: EndingRules | None = None, *, noun_ending: bool = False):
        self.rules = rules or EndingRules.load()
        self.noun_ending = noun_ending
        self.changes: list[Change] = []

    # ── 공개 API ────────────────────────────────────────────────────────

    def convert(self, text: str) -> str:
        """문단 텍스트 전체를 개조식으로."""
        if not text.strip():
            return text
        original = text
        for before, after in self.rules.replacements:
            if before in text:
                text = text.replace(before, after)

        sentences = _SENTENCE_SPLIT.split(text)
        converted = [self.convert_sentence(s) for s in sentences]
        result = " ".join(s for s in converted if s.strip()).strip()

        if result != original.strip():
            self.changes.append(Change(original.strip(), result, "개조식"))
        return result

    def convert_sentence(self, sentence: str) -> str:
        core = sentence.rstrip()
        trailing = sentence[len(core):]
        punct = ""
        while core and core[-1] in ".!?":
            punct = core[-1] + punct
            core = core[:-1]
        core = core.rstrip()
        if not core:
            return sentence

        if any(core.endswith(word) for word in self.rules.keep_as_is):
            return sentence

        if self.noun_ending:
            noun = self.noun_phrase(core)
            if noun is not None:
                return noun + trailing
            punct = ""  # 명사 종결 문체에서는 "~음/~함"으로 끝나도 마침표를 붙이지 않는다

        for suffix, replacement in self.rules.suffix_rules:
            if core.endswith(suffix):
                return core[: -len(suffix)] + replacement + punct + trailing

        for ending in self.rules.nominalize:
            if core.endswith(ending) and len(core) > len(ending):
                stem = core[: -len(ending)]
                return nominalize(stem) + punct + trailing

        return sentence

    def heading_noun_ending(self, text: str) -> str:
        """제목은 명사로 끝나야 한다("추진 배경임" → "추진 배경"). 본문과 달리
        명사로 줄이지 못해도 "~음/~함"을 새로 붙이지 않는다 — 원래 명사구가
        아니면 (드문 경우) 건드리지 않고 그대로 둔다."""
        if not self.noun_ending or not text.strip():
            return text
        core = text.rstrip()
        trailing = text[len(core):]
        if any(core.endswith(word) for word in self.rules.keep_as_is):
            return text
        noun = self.noun_phrase(core)
        if noun is None:
            return text
        if noun != core:
            self.changes.append(Change(text.strip(), noun, "제목 명사 종결"))
        return noun + trailing

    def noun_phrase(self, core: str) -> str | None:
        """문장을 명사로 끝낸 형태. 규칙에 안 맞으면 None.

        "응답 지연 문제가 지속적으로 발생하였습니다"
          → 끝말 '하였습니다'를 떼면 마지막 단어 '발생'(명사)
          → 바로 앞 부사 '지속적으로' → '지속', 그 앞 '문제가'의 조사 '가'를 뗌
          → "응답 지연 문제 지속 발생"
        """
        for suffix, tail in self.rules.noun_endings:
            if not core.endswith(suffix):
                continue
            stem = core[: -len(suffix)]
            words = stem.split(" ")
            if not stem or stem != stem.rstrip() or not _is_noun_word(words[-1]):
                return None
            return " ".join(self._tidy_before_noun(words)) + tail
        return None

    def _tidy_before_noun(self, words: list[str]) -> list[str]:
        words = list(words)
        i = len(words) - 2
        while i >= 0:
            word = words[i]
            adverb = next((a for a in self.rules.adverb_to_noun
                           if word.endswith(a) and len(word) > len(a) + 1), None)
            if adverb:
                words[i] = word[: -len(adverb)]
                i -= 1
                continue
            words[i] = self._drop_particle(word)
            break
        return words

    def _drop_particle(self, word: str) -> str:
        for particle in self.rules.drop_particles:
            if not word.endswith(particle):
                continue
            rest = word[: -len(particle)]
            if len(rest) < 2 or not _is_hangul(rest[-1]):
                return word
            needs_batchim = particle in ("을", "이", "은")
            if _has_batchim(rest[-1]) == needs_batchim:
                return rest
            return word
        return word

    def split_long(self, text: str, max_chars: int) -> list[str]:
        """긴 문장을 문장 부호와 연결어미에서 끊어 여러 항목으로."""
        parts = _merge_punct_only_parts([s for s in _SENTENCE_SPLIT.split(text) if s.strip()])
        if max_chars <= 0:
            return parts
        out: list[str] = []
        for part in parts:
            out.extend(self._split_by_connective(part, max_chars))
        return out

    def _split_by_connective(self, sentence: str, max_chars: int) -> list[str]:
        if len(sentence) <= max_chars:
            return [sentence]
        for connective in self.rules.connectives:
            marker = connective + ", "
            index = sentence.find(marker)
            if 0 < index < len(sentence) - len(marker):
                head = sentence[: index + len(connective)]
                tail = sentence[index + len(marker) :]
                self.changes.append(Change(sentence, f"{head} / {tail}", "문장 분리"))
                return [self.convert_sentence(head + "."),
                        *self._split_by_connective(tail, max_chars)]
        return [sentence]


# ── 자모 합성 ───────────────────────────────────────────────────────────


def _is_hangul(ch: str) -> bool:
    return _HANGUL_BASE <= ord(ch) <= _HANGUL_LAST


def _has_batchim(ch: str) -> bool:
    return (ord(ch) - _HANGUL_BASE) % 28 != 0


def _is_noun_word(word: str) -> bool:
    """명사로 끝낼 수 있는 단어인가 — 한글로 끝나는 2자 이상.

    한 글자('말했습니다'→'말', '못했습니다'→'못')는 명사가 아닐 가능성이 커서 뺀다.
    """
    return len(word) >= 2 and _is_hangul(word[-1])


def nominalize(stem: str) -> str:
    """어간에 명사형 어미를 붙인다."""
    stem = stem.rstrip()
    if not stem:
        return stem
    last = stem[-1]
    if not (_HANGUL_BASE <= ord(last) <= _HANGUL_LAST):
        return stem + "음"
    code = ord(last) - _HANGUL_BASE
    jong = code % 28
    if jong == 0:
        return stem[:-1] + chr(ord(last) + _JONG_M)
    if jong == _JONG_R:
        return stem[:-1] + chr(ord(last) - _JONG_R + _JONG_RM)
    return stem + "음"
