"""만든 .docx → PDF.

PDF를 직접 그리지 않고 **Word 문서를 변환**한다 — 표 폭·글꼴·말머리가 Word 결과와 똑같아야 하고,
레이아웃 엔진을 두 벌 유지할 이유가 없다.

1. Windows + MS Word: PowerShell로 Word COM을 띄워 ExportAsFixedFormat (사내 PC는 이 경로).
   사내 문서보안(DRM)이 걸린 .docx도 Word는 열 수 있다.
2. LibreOffice(soffice)가 있으면 그것으로 (Linux/Word 없는 PC).
둘 다 안 되면 RuntimeError — 웹 화면은 Word 결과만 남기고 이유를 보여 준다.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path

_TIMEOUT = 180


def docx_to_pdf(docx: str | Path, pdf: str | Path) -> Path:
    docx, pdf = Path(docx).resolve(), Path(pdf).resolve()
    errors: list[str] = []
    if platform.system() == "Windows":
        try:
            _with_word(docx, pdf)
            return pdf
        except Exception as exc:  # Word 미설치·COM 오류
            errors.append(f"Word: {exc}")
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice:
        try:
            _with_libreoffice(soffice, docx, pdf)
            return pdf
        except Exception as exc:
            errors.append(f"LibreOffice: {exc}")
    raise RuntimeError("PDF로 바꿀 프로그램이 없습니다(MS Word 또는 LibreOffice 필요). "
                       + " / ".join(errors))


def _with_word(docx: Path, pdf: Path) -> None:
    script = (
        "$ErrorActionPreference = 'Stop'; "
        "$w = New-Object -ComObject Word.Application; $w.Visible = $false; $w.DisplayAlerts = 0; "
        "try { "
        f"$d = $w.Documents.Open('{_ps(docx)}', $false, $true); "
        f"$d.ExportAsFixedFormat('{_ps(pdf)}', 17); $d.Close(0) "
        "} finally { $w.Quit() }"
    )
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, timeout=_TIMEOUT)
    if result.returncode != 0 or not pdf.exists():
        raise RuntimeError((result.stderr or result.stdout or "알 수 없는 오류").strip()[:300])


def _with_libreoffice(soffice: str, docx: Path, pdf: Path) -> None:
    outdir = pdf.parent / f".pdf-{pdf.stem}"
    outdir.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(outdir), str(docx)],
            capture_output=True, text=True, timeout=_TIMEOUT)
        produced = outdir / (docx.stem + ".pdf")
        if result.returncode != 0 or not produced.exists():
            raise RuntimeError((result.stderr or result.stdout or "알 수 없는 오류").strip()[:300])
        produced.replace(pdf)
    finally:
        shutil.rmtree(outdir, ignore_errors=True)


def _ps(path: Path) -> str:
    return str(path).replace("'", "''")


def pdf_engine() -> dict:
    """PDF로 바꿀 수 있는지 (웹 화면 표시용)."""
    if platform.system() == "Windows" and _word_installed():
        return {"available": True, "engine": "MS Word"}
    if shutil.which("soffice") or shutil.which("libreoffice"):
        return {"available": True, "engine": "LibreOffice"}
    return {"available": False, "engine": ""}


def _word_installed() -> bool:
    try:
        import winreg

        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Word.Application"))
        return True
    except OSError:
        return False
