# vendor/document-parsing

`wcoffee77/document-parsing`(doc2report)의 소스를 pip 패키지가 아니라
여기에 통째로 복사해 둔 것이다.

## 왜 pip git 의존성이 아니라 vendor인가

처음에는 `requirements.txt`에 아래처럼 git 의존성으로 받았다:

```
doc2report @ git+https://github.com/wcoffee77/document-parsing.git@<커밋>
```

그런데 사내망에서 Docker 이미지를 빌드할 때 `pip install`이 내부적으로
실행하는 `git clone`이 막혀서(사내 방화벽/프록시가 `github.com`에 대한
git 프로토콜 접근을 허용하지 않음) 빌드 자체가 실패했다. 이 저장소를
pip로 "설치 가능한 패키지"로 받아야 할 이유가 없어서(업스트림 변경을
자동으로 추적할 필요가 없는, 가져다 쓰기만 하는 기능), 빌드 시점에
네트워크로 가져오는 대신 소스를 그대로 복사해 두는 쪽을 택했다.

## 왜 `src/doc2report`만이 아니라 `profiles/`, `rules/`도 같이 복사했는가

doc2report 코드 안에서 프로파일(`profiles/*.yaml`)과 문구 규칙
(`rules/*.yaml`)을 **그 저장소 루트 기준 상대 경로**로 찾는다
(`profile.py`의 `PROFILE_DIR = Path(__file__).resolve().parents[2] /
"profiles"`, `transform/stylize_ko.py`의 `RULES_DIR = ...parents[3] /
"rules"` 등 — 둘 다 "`src/doc2report/` 밑에서 몇 단계 위가 그 저장소
루트"라는 디렉터리 구조를 그대로 가정한다). 이 상대 경로 가정 때문에
`src/doc2report`만 따로 떼어 패키징하면(원본 `pyproject.toml`의
hatchling 빌드 설정이 실제로 이렇게 `src/doc2report`만 wheel에 담는다)
런타임에 `profiles`/`rules`를 못 찾아 깨진다 — 애초에 pip 설치로는
제대로 동작하지 않았을 코드라는 뜻이다. 그래서 이 디렉터리 밑에
`src/doc2report`, `profiles`, `rules`를 원본과 **똑같은 상대 위치**로
복사해 뒀고, `confluence_agenda/web/docx_export.py`가 import 전에
`vendor/document-parsing/src`를 `sys.path`에 추가해 그 구조를 그대로
재현한다. doc2report 코드 자체는 한 글자도 고치지 않았다.

## 가져온 범위

전체 레포 중 **런타임에 실제로 필요한 것만** 가져왔다:

- `src/doc2report/` — 패키지 전체(모듈 간 상호 참조가 있어서 통째로).
  단, 우리가 쓰는 경로(`pipeline.convert()` → Confluence 읽기 → .docx
  저장)는 `cli.py`/`account.py`/`doctor.py`/`web/`(doc2report 자신의
  CLI·웹서버)를 타지 않는다 — 참고로만 남겨 뒀을 뿐 이 앱에서는 안 쓴다.
- `profiles/`, `rules/` — 위에서 설명한 상대 경로 조회 대상.

가져오지 않은 것: `tests/`, `docs/`, `.github/`, `packaging/`, `scripts/`,
`tools/`, `uv.lock` 등 개발/배포용 파일(이 앱 런타임과 무관).

## 의존성

doc2report 자신의 런타임 의존성(python-docx, markdown-it-py, lxml,
fonttools, pydantic, PyYAML, httpx)은 이 저장소의 `requirements.txt`에
그대로 선언돼 있다 — 전부 보통의 PyPI 패키지라 git과 무관하게 설치된다.
`typer`(doc2report의 CLI 전용)와 `mdit-py-plugins`(소스에서 실제로
import되지 않음)는 우리가 쓰는 경로에 필요 없어서 넣지 않았다.

## 업데이트하려면

1. `wcoffee77/document-parsing`에서 원하는 커밋을 가져와
   `src/doc2report/`, `profiles/`, `rules/`를 여기 같은 이름으로
   덮어쓴다.
2. `pipeline.convert()`의 시그니처나 `ConvertResult.document.title` 같은
   우리가 쓰는 인터페이스가 바뀌었는지 `confluence_agenda/web/docx_export.py`와
   비교해 확인한다.
3. `python -m unittest discover -s tests`로 전체 테스트를 돌려 확인한다.

가져온 커밋: `ce706e5603209a149e6042b5ae296b9cc1d51d53`
(원본: https://github.com/wcoffee77/document-parsing)
