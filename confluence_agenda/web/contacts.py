"""웹 UI 태그(칩)로 보여줄 자주 쓰는 수신자 목록을 불러온다.

이름/이메일은 개인정보이고 이 저장소는 public이라 소스에 박아 git에
커밋하지 않는다. 저장소 루트의 contacts.json(또는 CONTACTS_FILE
환경변수가 가리키는 파일)을 읽고, 파일이 없으면 프리셋 없이(그 외
이메일 자유 입력만) 동작한다. 형식은 contacts.example.json 참고.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Tuple

_DEFAULT_PATH = Path(__file__).resolve().parent.parent.parent / "contacts.json"


def load_preset_contacts() -> List[Tuple[str, str]]:
    path = Path(os.environ.get("CONTACTS_FILE") or _DEFAULT_PATH)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(raw, list):
        return []
    return [
        (entry["name"], entry["email"])
        for entry in raw
        if isinstance(entry, dict) and entry.get("name") and entry.get("email")
    ]
