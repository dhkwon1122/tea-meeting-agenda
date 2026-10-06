"""글꼴 메트릭으로 문자열의 실제 폭을 재는 모듈.

Word가 어디서 줄을 바꿀지 계산하려면 '이 글자가 몇 EMU인가'를 알아야 한다.
설치된 TTF/TTC의 hmtx·cmap을 직접 읽고, 글꼴 파일을 못 찾으면 보수적인 근사표로 폴백한다.
(근사로 인한 오차는 프로파일의 tables.safety_margin이 흡수한다.)
"""

from __future__ import annotations

import functools
import os
import unicodedata
from pathlib import Path

from fontTools.ttLib import TTCollection, TTFont

# 자주 쓰는 한글/영문 글꼴의 파일명. 여기서 못 찾으면 폰트 폴더를 훑는다.
_KNOWN_FILES: dict[str, str] = {
    "맑은 고딕": "malgun.ttf",
    "malgun gothic": "malgun.ttf",
    "맑은 고딕 semilight": "malgunsl.ttf",
    "굴림": "gulim.ttc",
    "gulim": "gulim.ttc",
    "굴림체": "gulim.ttc",
    "gulimche": "gulim.ttc",
    "돋움": "dotum.ttc",
    "dotum": "dotum.ttc",
    "돋움체": "dotum.ttc",
    "dotumche": "dotum.ttc",
    "바탕": "batang.ttc",
    "batang": "batang.ttc",
    "바탕체": "batang.ttc",
    "batangche": "batang.ttc",
    "궁서": "gungsuh.ttc",
    "궁서체": "batang.ttc",
    "나눔고딕": "NanumGothic.ttf",
    "나눔명조": "NanumMyeongjo.ttf",
    "함초롬바탕": "HANBatang.ttf",
    "함초롬돋움": "HANDotum.ttf",
    "d2coding": "D2Coding.ttf",
    "consolas": "consola.ttf",
    "calibri": "calibri.ttf",
    "arial": "arial.ttf",
    "times new roman": "times.ttf",
    "malgun": "malgun.ttf",
}

# 글꼴 파일이 없을 때 쓰는 폭 근사(em 단위). 실제보다 약간 넉넉하게 잡는다.
_FALLBACK_WIDE = 1.0      # 한글·한자·전각
_FALLBACK_NARROW = 0.55   # 기본 라틴
_FALLBACK_TABLE = {
    " ": 0.28, ".": 0.28, ",": 0.28, ":": 0.28, ";": 0.28, "'": 0.22, '"': 0.36,
    "!": 0.28, "|": 0.26, "(": 0.33, ")": 0.33, "[": 0.33, "]": 0.33, "-": 0.34,
    "i": 0.26, "j": 0.26, "l": 0.26, "t": 0.34, "f": 0.32, "r": 0.36,
    "m": 0.85, "w": 0.75, "M": 0.85, "W": 0.88,
}


def _font_dirs() -> list[Path]:
    dirs = []
    win = os.environ.get("SystemRoot", r"C:\Windows")
    dirs.append(Path(win) / "Fonts")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    dirs += [Path("/usr/share/fonts"), Path("/Library/Fonts"),
             Path.home() / ".local/share/fonts"]
    return [d for d in dirs if d.is_dir()]


@functools.lru_cache(maxsize=1)
def _name_index() -> dict[str, Path]:
    """설치된 글꼴의 name 테이블을 훑어 만든 이름 → 파일 색인 (최초 1회, 캐시됨)."""
    index: dict[str, Path] = {}
    for d in _font_dirs():
        for path in list(d.glob("*.ttf")) + list(d.glob("*.ttc")) + list(d.glob("*.otf")):
            try:
                fonts = (TTCollection(path, lazy=True).fonts if path.suffix.lower() == ".ttc"
                         else [TTFont(path, lazy=True, fontNumber=0)])
                for font in fonts[:1]:
                    for rec in font["name"].names:
                        if rec.nameID in (1, 4, 16):
                            try:
                                key = rec.toUnicode().strip().lower()
                            except Exception:
                                continue
                            index.setdefault(key, path)
                    font.close()
            except Exception:
                continue
    return index


# 한 파일(.ttc) 안에 여러 글꼴이 들어 있다. 바탕과 바탕체는 폭이 다르므로 정확히 골라야 한다.
_FACE_ALIASES: dict[str, str] = {
    "바탕": "batang", "바탕체": "batangche",
    "굴림": "gulim", "굴림체": "gulimche",
    "돋움": "dotum", "돋움체": "dotumche",
    "궁서": "gungsuh", "궁서체": "gungsuhche",
}


def _face_names(name: str) -> set[str]:
    key = name.strip().lower()
    names = {key}
    if key in _FACE_ALIASES:
        names.add(_FACE_ALIASES[key])
    for korean, latin in _FACE_ALIASES.items():
        if key == latin:
            names.add(korean)
    return names


def _pick_face(fonts: list, name: str):
    """컬렉션에서 이름이 맞는 글꼴을 고른다. 못 찾으면 첫 번째."""
    wanted = _face_names(name)
    for font in fonts:
        try:
            records = {rec.toUnicode().strip().lower()
                       for rec in font["name"].names if rec.nameID in (1, 4, 6, 16)}
        except Exception:
            continue
        if records & wanted:
            return font
    return fonts[0]


def _find_font_file(name: str) -> Path | None:
    key = name.strip().lower()
    filename = _KNOWN_FILES.get(key)
    if filename:
        for d in _font_dirs():
            p = d / filename
            if p.exists():
                return p
    return _name_index().get(key)


class FontMetrics:
    """글꼴 하나의 글자 폭(em 단위)을 제공. 글꼴 파일이 없으면 근사표로 동작."""

    def __init__(self, name: str):
        self.name = name
        self._cmap = None
        self._hmtx = None
        self._upem = 1000
        self._cache: dict[str, float] = {}
        self.loaded = False
        path = _find_font_file(name)
        if path is None:
            return
        try:
            if path.suffix.lower() == ".ttc":
                font = _pick_face(TTCollection(path, lazy=True).fonts, name)
            else:
                font = TTFont(path, lazy=True, fontNumber=0)
            self._cmap = font.getBestCmap()
            self._hmtx = font["hmtx"]
            self._upem = font["head"].unitsPerEm or 1000
            self._font = font
            self.loaded = True
        except Exception:
            self.loaded = False

    def char_em(self, ch: str) -> float:
        cached = self._cache.get(ch)
        if cached is not None:
            return cached
        em = self._measure(ch)
        self._cache[ch] = em
        return em

    def _measure(self, ch: str) -> float:
        if self.loaded and self._cmap is not None:
            glyph = self._cmap.get(ord(ch))
            if glyph is not None:
                try:
                    return self._hmtx[glyph][0] / self._upem
                except Exception:
                    pass
        return _fallback_em(ch)

    def text_em(self, text: str) -> float:
        return sum(self.char_em(c) for c in text)


def _fallback_em(ch: str) -> float:
    if is_wide(ch):
        return _FALLBACK_WIDE
    return _FALLBACK_TABLE.get(ch, _FALLBACK_NARROW)


def is_wide(ch: str) -> bool:
    """전각 취급할 글자(한글·한자·가나·전각기호)."""
    return unicodedata.east_asian_width(ch) in ("W", "F")


@functools.lru_cache(maxsize=64)
def get_metrics(name: str) -> FontMetrics:
    return FontMetrics(name)


class TextMeasurer:
    """한 서식(동아시아 글꼴 + 라틴 글꼴 + 크기)에서의 폭 계산기."""

    def __init__(self, east_asia: str, latin: str, size_emu: int, bold: bool = False,
                 scale: float = 1.0):
        self.ea = get_metrics(east_asia)
        self.latin = get_metrics(latin)
        self.size = size_emu
        # 굵은 글씨는 같은 크기라도 조금 넓다. 별도 파일을 찾지 않고 보정만 한다.
        # 장평(scale)은 글자 폭에만 곱해진다 (Word의 w:w와 같은 방식).
        self.bold_factor = (1.04 if bold else 1.0) * scale

    @property
    def font_available(self) -> bool:
        return self.ea.loaded and self.latin.loaded

    def char_width(self, ch: str) -> float:
        metrics = self.ea if is_wide(ch) else self.latin
        return metrics.char_em(ch) * self.size * self.bold_factor

    def width(self, text: str) -> float:
        """문자열을 한 줄로 썼을 때의 폭(EMU)."""
        return sum(self.char_width(c) for c in text)

    def longest_word_width(self, text: str) -> float:
        """줄바꿈이 가능한 최소 폭 — 가장 긴 '끊을 수 없는 덩어리'의 폭.

        한글은 글자 단위로 끊을 수 있으므로 어절 전체가 아니라 한 글자가 최소 단위다.
        라틴 단어는 중간에서 끊지 않는다.
        """
        best = 0.0
        chunk = 0.0
        for ch in text:
            if ch.isspace() or is_wide(ch):
                best = max(best, chunk, self.char_width(ch) if is_wide(ch) else 0.0)
                chunk = 0.0
            else:
                chunk += self.char_width(ch)
        return max(best, chunk)

    def wrap_count(self, text: str, max_width: float) -> int:
        """주어진 폭에서 몇 줄이 되는지. 표 행 높이 추정에 쓴다."""
        if max_width <= 0:
            return 1
        lines = 1
        used = 0.0
        pending = 0.0  # 아직 줄에 확정되지 않은 라틴 단어
        for ch in text:
            w = self.char_width(ch)
            if ch == "\n":
                lines += 1
                used = pending = 0.0
                continue
            if ch.isspace():
                used += pending + w
                pending = 0.0
                if used > max_width:
                    lines += 1
                    used = 0.0
                continue
            if is_wide(ch):
                used += pending
                pending = 0.0
                if used + w > max_width:
                    lines += 1
                    used = 0.0
                used += w
            else:
                pending += w
                if used + pending > max_width:
                    if used > 0:
                        lines += 1
                        used = 0.0
                    if pending > max_width:  # 한 단어가 줄보다 길면 강제로 끊긴다
                        lines += int(pending // max_width)
                        pending = pending % max_width
        return max(1, lines)
