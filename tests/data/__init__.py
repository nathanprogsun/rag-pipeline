"""tests/data: 预生成的测试 fixture (txt/md/html/csv/json/pdf/docx + xlsx/pptx)。"""

from __future__ import annotations

from pathlib import Path

DATA_DIR = Path(__file__).parent

# 10 个内置扩展各一份 sample
SAMPLE_TXT = DATA_DIR / "sample.txt"
SAMPLE_MD = DATA_DIR / "sample.md"
SAMPLE_HTML = DATA_DIR / "sample.html"
SAMPLE_HTM = DATA_DIR / "sample.htm"
SAMPLE_CSV = DATA_DIR / "sample.csv"
SAMPLE_JSON = DATA_DIR / "sample.json"
SAMPLE_PDF = DATA_DIR / "sample.pdf"
SAMPLE_DOCX = DATA_DIR / "sample.docx"
SAMPLE_XLSX = DATA_DIR / "sample.xlsx"
SAMPLE_PPTX = DATA_DIR / "sample.pptx"
SAMPLE_CHAT_EXPORT_MD = DATA_DIR / "sample_chat_export.md"
