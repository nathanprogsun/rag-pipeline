"""pptx extension adapter: ``parse_office`` 薄封装。

实现 8 项契约:

    8.1 落盘: ``/tmp/{nanoid}.{ext}`` — ``parse_office`` 内部完成
    8.2 解压: ``zipfile.ZipFile`` 过滤 ``ppt/(notesSlides|slides)/(notesSlide|slide)\\d+.xml``
    8.3 校验: 必须有 ``ppt/slides/slide\\d+.xml`` 否则 ``RAGError(READER_PARSE)``
    8.4 排序: 按文件名中数字升序, ``slide1 < slide2 < slide10``
    8.5 读 XML: encoding 优先, 失败降级 utf-8
    8.6 抽文本: ``a:p`` 段落 → 过滤无 ``a:t`` 子节点的 → ``a:t`` 文本拼接 → 段间 ``\\n``
    8.7 清理: ``os.unlink(tempfile_path)`` (parse_office 内 finally)
    8.8 返回: 纯文本 (slide 间 ``\\n``, 段间 ``\\n``)
    mime: ``application/vnd.openxmlformats-officedocument.presentationml.presentation``

设计:
    - 仅做 decode buffer → ``parse_office`` 抽文本 → 包 ``FormatReaderResult``。
    - 错误包装: ``parse_office`` 抛 RAGError 时, 重新包成 ``RAGError(READER_PARSE, 'python-zipfile')``。
    - **不**抽 pptx 内嵌图 (与 docx adapter 不同: docx 必须抽, pptx 忽略)。
    - ``format_text / images / extras`` 均留空/None。
    - ``async def`` 包装: pptx 解析不涉及异步 I/O, 内部 ``parse_office`` 同步调用即可;
      仅以 ``async`` 签名对齐 ``FormatAdapter`` Protocol, 让 ``dispatch`` 统一 ``await``
      无需反射分支。
"""

from __future__ import annotations

from rag.ingest.reader.extensions.base import wrap_parse_error
from rag.ingest.reader.parse_office import parse_office
from rag.ingest.reader.types import FormatReaderResult
from rag.ingest.types import DocMeta

# pptx mime (OOXML 官方命名)。
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


async def pptx_adapter(
    buffer: bytes,
    *,
    encoding: str = "utf-8",
    upload_file: object | None = None,  # noqa: ARG001 — pptx 不抽图, 保留签名
) -> FormatReaderResult:
    """bytes → FormatReaderResult: ``parse_office`` → ``raw_text`` (slide 间 ``\\n``)。

    Args:
        buffer: pptx 二进制内容。
        encoding: 文本编码 (主要给 XML decode 兜底用)。
        upload_file: 保留以对齐 ``FormatAdapter`` 协议, pptx 不抽图故忽略。

    Returns:
        ``FormatReaderResult { raw_text, format_text=None, meta, images=[], extras={} }``

    Raises:
        RAGError: ``code=READER_PARSE`` — ``parse_office`` 解析失败时包装。
    """
    try:
        # ``parse_office`` 是 sync, pptx 解析纯 CPU/disk, 无需 to_thread 卸载;
        # 这里只是 ``async def`` 薄包装以对齐 FormatAdapter 协议, 让 dispatch
        # 统一 ``await``。
        raw_text = parse_office(buffer, extension="pptx", encoding=encoding)
    except Exception as e:
        # ``parse_office`` 已抛 RAGError; 这里 ``raise ... from e`` 保留链路。
        # 用 wrap_parse_error 统一替换 suffix 为 'python-zipfile'。
        raise wrap_parse_error("<buffer:pptx>", e, "python-zipfile") from e

    # slide 间 '\n', 段间也 '\n'; 估算 paragraph_count 为非空行数 (与 docx adapter 对齐)。
    paragraph_count = sum(1 for line in raw_text.split("\n") if line.strip())

    return FormatReaderResult(
        raw_text=raw_text,
        format_text=None,
        meta=DocMeta(
            datasource="file",  # 占位, dispatch 覆盖
            mime=PPTX_MIME,
            encoding=encoding,
            size_bytes=len(buffer),
            paragraph_count=paragraph_count,
        ),
        images=[],
        extras={},
    )
