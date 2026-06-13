"""Reader extensions: 8 个 format adapter。

支持的格式: txt / md (text_adapter), html (html_adapter), pdf, docx, pptx, csv, xlsx。
md 与 htm 是 alias。
不支持 json。
"""

from __future__ import annotations

from rag.ingest.reader.extensions.base import UploadedFileResult, UploadFileHandler
from rag.ingest.reader.extensions.csv import csv_adapter
from rag.ingest.reader.extensions.docx import docx_adapter
from rag.ingest.reader.extensions.html import html_adapter
from rag.ingest.reader.extensions.pdf import pdf_adapter
from rag.ingest.reader.extensions.pptx import pptx_adapter
from rag.ingest.reader.extensions.text import text_adapter
from rag.ingest.reader.extensions.xlsx import CUSTOM_SPLIT_SIGN, xlsx_adapter
