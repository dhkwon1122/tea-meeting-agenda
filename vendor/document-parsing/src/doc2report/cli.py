"""명령줄 인터페이스.

    doc2report convert 보고서.md -o 보고서.docx
    doc2report convert https://.../pages/12345 -o 보고서.docx
    doc2report convert 보고서.md -o out.docx --watch --open   # 서식 맞추기 반복 루프
    doc2report profile show default
    doc2report profile diff default 사내.yaml --sample 보고서.md
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import typer

from .pipeline import auto_profile, convert as run_convert
from .profile import PROFILE_DIR, dump_profile, load_profile
from .units import emu_to_mm, emu_to_pt, fmt_pt

# Windows 기본 콘솔(cp949)에서 한글·기호가 깨지지 않도록.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

app = typer.Typer(add_completion=False, help="Confluence/Markdown → 사내 규격 보고서(.docx)")
profile_app = typer.Typer(help="서식 프로파일 다루기")
app.add_typer(profile_app, name="profile")



@app.command()
def convert(
    source: str = typer.Argument(..., help="입력 .md/.docx/.txt 파일, Confluence URL, 또는 '-'(표준입력)"),
    output: Path = typer.Option(None, "-o", "--output", help="출력 .docx 경로"),
    profile: str = typer.Option(None, "-p", "--profile",
                                help="프로파일 이름 또는 .yaml 경로 (생략 시 Confluence URL은 "
                                     "confluence, 그 밖은 default)"),
    ask: bool = typer.Option(False, "--ask", "-i", help="변환 전에 글꼴·크기·줄간격을 골라서 진행"),
    font: str = typer.Option(None, "--font", help="글꼴 (프로파일의 choices.font 참고)"),
    size: str = typer.Option(None, "--size", help="본문 글자 크기 (예: 14pt)"),
    line_spacing: str = typer.Option(None, "--line-spacing", help="줄 간격 (예: 1.45 또는 145%)"),
    title_size: str = typer.Option(None, "--title-size", help="제목 글자 크기 (예: 20pt)"),
    table_size: str = typer.Option(None, "--table-size", help="표 글자 크기 (예: 12pt)"),
    margin: str = typer.Option(None, "--margin", help="여백 '25mm' 또는 '위,아래,좌,우'"),
    date: str = typer.Option(None, "--date", help="제목 아래 날짜 ('today' 또는 '2026. 10. 1')"),
    polish: str = typer.Option(None, "--polish",
                               help="rules | llm | none (생략 시 프로파일의 text.polish)"),
    report: Path = typer.Option(None, "--report", help="변경 내역을 저장할 .md 경로"),
    save_profile: Path = typer.Option(None, "--save-profile", help="고른 서식을 .yaml로 저장"),
    open_after: bool = typer.Option(False, "--open", help="변환 후 결과 문서 열기"),
    watch: bool = typer.Option(False, "--watch", help="입력·프로파일이 바뀌면 자동 재변환"),
    no_linked: bool = typer.Option(False, "--no-linked",
                                   help="Confluence 본문에 연결된 페이지(페이지 포함·하위 페이지·첨부 Word)를 불러오지 않음"),
    follow_links: bool = typer.Option(False, "--follow-links",
                                      help="Confluence 본문 링크(다른 페이지의 책갈피 포함)가 가리키는 내용도 불러옴"),
) -> None:
    from .account import load_and_apply

    load_and_apply()  # 웹 화면에서 등록한 사용자의 토큰·LLM 설정(있으면 환경변수보다 우선)
    out = output or Path(_default_output(source))
    profile = profile or auto_profile(source)
    base = load_profile(profile)

    chosen = dict(font=font, size=size, line_spacing=line_spacing,
                  title_size=title_size, table_size=table_size, margin=margin)
    if ask:
        chosen = _ask_format(base, chosen)
    effective = base.with_overrides(**chosen)

    if save_profile:
        save_profile.write_text(dump_profile(effective), encoding="utf-8")
        typer.echo(f"서식 저장: {save_profile}")

    def once() -> None:
        result = run_convert(source, out, effective, polish=polish, date=date, linked=not no_linked,
                             follow_links=follow_links)
        typer.echo(f"[완료] {out}  (표 {len(result.layouts)}개, 문구 수정 {len(result.changes)}건)")
        for note in result.notes:
            typer.echo(f"  · {note}")
        if report:
            report.write_text(result.report(), encoding="utf-8")
            typer.echo(f"  리포트: {report}")

    once()
    if open_after:
        _open(out)
    if watch:
        _watch([source, _profile_path(profile)], once)


@app.command()
def check(
    source: str = typer.Argument(...),
    profile: str = typer.Option(None, "-p", "--profile"),
) -> None:
    """변환하지 않고 표가 어떻게 배치될지만 확인한다."""
    from .layout.measure import get_metrics

    prof = load_profile(profile or auto_profile(source))
    result = run_convert(source, None, prof)
    usable = prof.page.usable_width
    typer.echo(f"사용가능폭 {emu_to_mm(usable):.1f}mm")

    for key in ("body", "table"):
        spec = prof.font(key)
        metrics = get_metrics(spec.east_asia)
        state = "실제 메트릭" if metrics.loaded else "근사값(글꼴 파일 못 찾음)"
        typer.echo(f"{key} 글꼴 {spec.east_asia} {emu_to_pt(spec.size):g}pt — {state}")
    for index, layout in enumerate(result.layouts.values(), start=1):
        status = "초과!" if layout.total_width > usable + 1 else "OK"
        typer.echo(
            f"표 {index}: {len(layout.col_widths)}열, 폭 {emu_to_mm(layout.total_width):.1f}mm, "
            f"글자 {fmt_pt(layout.font_size)} [{status}]"
        )
        for note in layout.notes:
            typer.echo(f"    · {note}")


@app.command()
def doctor(
    save: bool = typer.Option(False, "--save", help="결과를 진단결과_날짜.txt로도 저장"),
    no_network: bool = typer.Option(False, "--no-network", help="Confluence·LLM 연결 점검은 건너뜀"),
) -> None:
    """설치·환경 진단 — 파이썬·패키지·Word 템플릿·글꼴·사용자 등록·프록시·Confluence·LLM을 점검하고
    막힌 곳마다 해결 방법을 알려 준다(토큰 값은 쓰지 않음)."""
    from datetime import datetime

    from .doctor import FAIL, report, run

    checks = run(network=not no_network)
    text = report(checks)
    typer.echo(text)
    if save:
        path = Path.cwd() / f"진단결과_{datetime.now():%Y%m%d_%H%M}.txt"
        path.write_text(text, encoding="utf-8-sig")  # 메모장에서 한글이 깨지지 않게 BOM
        typer.echo(f"\n저장: {path}")
    raise typer.Exit(1 if any(c.level == FAIL for c in checks) else 0)


@app.command()
def web(
    port: int = typer.Option(8765, "--port", help="포트 (이미 쓰고 있으면 다른 번호로)"),
    host: str = typer.Option("127.0.0.1", "--host",
                             help="기본은 이 PC에서만 접속. 다른 PC에 열려면 0.0.0.0 (토큰 노출 주의)"),
    output_dir: Path = typer.Option(None, "--output-dir",
                                    help="결과 저장 폴더 (기본: DOC2REPORT_OUTPUT_DIR 또는 out/webapp)"),
    no_browser: bool = typer.Option(False, "--no-browser", help="브라우저를 자동으로 열지 않음"),
) -> None:
    """웹 화면으로 변환 — 여러 Confluence 페이지·Word·붙여넣은 글을 한 문서로, docx/pdf/md 출력."""
    from .web.server import serve

    serve(host=host, port=port, output_dir=output_dir, open_browser=not no_browser)


@profile_app.command("show")
def profile_show(name: str = typer.Argument("default")) -> None:
    """프로파일을 해석된 값으로 출력한다 (단위 변환 결과 확인용)."""
    typer.echo(dump_profile(load_profile(name)))


@profile_app.command("list")
def profile_list() -> None:
    for path in sorted(PROFILE_DIR.glob("*.yaml")):
        typer.echo(f"{path.stem:20} {path}")


@profile_app.command("diff")
def profile_diff(
    left: str = typer.Argument(...),
    right: str = typer.Argument(...),
    sample: str = typer.Option(None, "--sample", help="같은 문서를 양쪽으로 변환해 결과 비교"),
) -> None:
    """두 프로파일의 차이와, 같은 문서에 적용했을 때의 결과 차이를 보여 준다."""
    a, b = load_profile(left), load_profile(right)
    left_lines = dump_profile(a).splitlines()
    right_lines = dump_profile(b).splitlines()
    import difflib

    for line in difflib.unified_diff(left_lines, right_lines, left, right, lineterm=""):
        typer.echo(line)

    if sample:
        typer.echo("\n[같은 문서 변환 결과]")
        for name, prof in ((left, a), (right, b)):
            result = run_convert(sample, None, prof)
            widths = [emu_to_mm(layout.total_width) for layout in result.layouts.values()]
            sizes = {fmt_pt(layout.font_size) for layout in result.layouts.values()}
            shrunk = sum(1 for layout in result.layouts.values() if layout.notes)
            typer.echo(
                f"  {name:20} 표 {len(widths)}개, 최대 폭 {max(widths, default=0):.1f}mm, "
                f"글자 크기 {sorted(sizes) or '-'}, 조정된 표 {shrunk}개"
            )


@profile_app.command("init")
def profile_init(
    from_docx: Path = typer.Option(..., "--from", help="규격을 뽑아낼 기존 보고서(.docx)"),
    output: Path = typer.Option(None, "-o", "--output", help="저장할 .yaml 경로"),
) -> None:
    """기존 사내 보고서에서 여백·글꼴·크기를 역추출해 프로파일 초안을 만든다."""
    from .profile_init import profile_from_docx

    text = profile_from_docx(from_docx)
    if output:
        output.write_text(text, encoding="utf-8")
        typer.echo(f"[완료] {output}")
    else:
        typer.echo(text)


# ── 변환 전 서식 선택 ───────────────────────────────────────────────────


def _ask_format(profile, preset: dict) -> dict:
    """변환 전에 서식을 물어본다. Enter만 누르면 프로파일 값을 그대로 쓴다.

    보여 줄 선택지는 프로파일의 choices 항목에서 온다 (코드에 박아 두지 않는다).
    """
    body = profile.font("body")
    choices = profile.choices
    typer.echo(f"[{profile.name}] 서식을 고르세요. Enter = 기본값 유지, 직접 입력도 됩니다.")

    chosen = dict(preset)
    chosen["font"] = preset["font"] or _choice(
        "글꼴", choices.font, body.east_asia)
    chosen["size"] = preset["size"] or _choice(
        "본문 글자 크기", choices.size, fmt_pt(body.size))
    chosen["line_spacing"] = preset["line_spacing"] or _choice(
        "줄 간격", choices.line_spacing, f"{body.line_spacing:g}")
    chosen["table_size"] = preset["table_size"] or _choice(
        "표 글자 크기", choices.table_size, fmt_pt(profile.font("table").size))
    typer.echo("")
    return chosen


def _choice(label: str, options: list[str], default: str) -> str | None:
    """번호로 고르거나 값을 직접 입력. 빈 입력이면 None(기본값 유지)."""
    shown = "  ".join(f"[{i}] {value}" for i, value in enumerate(options, start=1))
    answer = typer.prompt(f"  {label} (기본 {default})\n    {shown}\n  >",
                          default="", show_default=False).strip()
    if not answer:
        return None
    if answer.isdigit() and 1 <= int(answer) <= len(options):
        return options[int(answer) - 1]
    return answer


# ── 보조 ────────────────────────────────────────────────────────────────


def _default_output(source: str) -> str:
    if source.startswith(("http://", "https://")) or source == "-":
        return "report.docx"
    path = Path(source)
    if path.suffix.lower() == ".docx":  # 입력 Word 파일을 덮어쓰지 않게
        return str(path.with_name(path.stem + "_보고서.docx"))
    return str(path.with_suffix(".docx"))


def _profile_path(profile: str) -> str:
    path = Path(profile)
    return str(path if path.suffix else PROFILE_DIR / f"{profile}.yaml")


def _open(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception as exc:
        typer.echo(f"문서를 열지 못했습니다: {exc}")


def _watch(paths: list[str], action) -> None:
    """의존성 없이 mtime만 본다. 서식 맞추기 반복 루프용."""
    watched = [Path(p) for p in paths if p and not p.startswith(("http", "-"))]
    stamps = {p: _mtime(p) for p in watched}
    typer.echo("감시 중… (Ctrl+C로 종료)  " + ", ".join(str(p) for p in watched))
    try:
        while True:
            time.sleep(0.7)
            for path in watched:
                current = _mtime(path)
                if current != stamps[path]:
                    stamps[path] = current
                    typer.echo(f"\n[변경 감지] {path}")
                    try:
                        action()
                    except Exception as exc:
                        typer.echo(f"  ✖ {exc}")
    except KeyboardInterrupt:
        typer.echo("\n종료")


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return -1.0


if __name__ == "__main__":
    app()
