"""`python -m doc2report …` — 설치형 묶음(runtime\\python.exe)은 명령 파일(doc2report.exe) 없이 이렇게 실행한다."""

from .cli import app

app(prog_name="doc2report")
