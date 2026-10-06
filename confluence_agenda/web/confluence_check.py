"""Confluence 연결이 안 될 때 어느 단계에서 막히는지 확인하는 진단 스크립트.

    python -m confluence_agenda.web.confluence_check [토큰]

토큰을 안 주면 CONFLUENCE_API_TOKEN 환경변수를 쓴다. 이 앱을 띄운 것과
같은 환경(.env 포함, Docker라면 컨테이너 안)에서 실행해야 의미가 있다 -
auth_check.py와 같은 역할로, docx_export.diagnose_connection()이 실제
단계별 확인을 한다.
"""

from __future__ import annotations

import sys
from typing import List, Optional

from . import docx_export


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) > 1:
        print("사용법: python -m confluence_agenda.web.confluence_check [토큰]")
        return 2

    token = argv[0] if argv else None
    for line in docx_export.diagnose_connection(token):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
