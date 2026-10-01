"""로그인이 안 될 때 어느 단계에서 막히는지 확인하는 진단 스크립트.

    python -m confluence_agenda.web.auth_check <user_id> <password>

이 앱을 띄운 것과 같은 환경(.env/DATABASE_URL 포함)에서 실행해야 의미가
있다. authenticate()와 같은 경로를 단계별로 밟아보고, 각 단계의 성공/실패를
사람이 읽을 수 있게 출력한다 - 로그인 화면은 보안상 실패 사유를 전부
"아이디 또는 비밀번호가 올바르지 않습니다"로 뭉뚱그리므로, 실제 원인 추적은
이 스크립트로 한다.
"""

from __future__ import annotations

import sys
from typing import List, Optional

from . import auth


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print("사용법: python -m confluence_agenda.web.auth_check <user_id> <password>")
        return 2

    user_id, password = argv
    for line in auth.diagnose_login(user_id, password):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
