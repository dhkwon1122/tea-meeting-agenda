"""doc2report — Confluence/Markdown 문서를 사내 규격 보고서(.docx)로 변환."""

from .pipeline import ConvertResult, convert
from .profile import Profile, load_profile

__version__ = "0.1.0"
__all__ = ["convert", "ConvertResult", "Profile", "load_profile"]
