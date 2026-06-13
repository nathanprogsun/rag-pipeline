r"""OOXML 解压 + XML 抽取: 纯文本读取 pptx。

:

 - 8.1 落盘: ``buffer`` 写到 ``/tmp/{nanoid}.{ext}``
 - 8.2 解压: 用 ``zipfile.ZipFile`` 过滤 ``ppt/(notesSlides|slides)/(notesSlide|slide)\d+.xml``
 - 8.3 校验: 无文件 / 无 ``ppt/slides/slide\d+.xml`` → 抛 ``RAGError(code=READER_PARSE)``
 - 8.4 排序: 按文件名中数字升序, slide1 < slide2 < slide10
 - 8.5 读 XML: encoding 优先, 失败降级 utf-8
 - 8.6 抽文本: ``a:p`` 段落 → 过滤无 ``a:t`` 子节点的 → ``a:t`` 文本拼接 → 段间 ``\\n``
 - 8.7 清理: ``os.unlink(tempfile_path)``
 - 8.8 返回: 纯文本 (slide 间 ``\\n``, 段间 ``\\n``)

实现要点:

 - **不**把 zip 解到磁盘, 直接 ``ZipFile.read(name)`` 在内存里读 bytes → XML,
 省 IO 也避免任务描述里"removed decompress dir"的副作用。
 - 只支持 ``extension == 'pptx'``; 其他 extension → ``RAGError(code=READER_PARSE)``。
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from rag.error_codes import ReaderErrorCode
from rag.exception import RAGError

logger = logging.getLogger(__name__)

# OOXML namespace (DrawingML): a:p / a:t 实际带命名空间。
A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
PARA_TAG = A_NS + "p"
TEXT_TAG = A_NS + "t"

DEFAULT_TMP_DIR = "/tmp"

# 匹配 ppt/(notesSlides|slides)/(notesSlide|slide)\d+.xml (8.2 全集)。
_ALL_FILES_RE = re.compile(r"^ppt/(notesSlides|slides)/(notesSlide|slide)\d+\.xml$")
# 匹配 ppt/slides/slide\d+.xml (8.3 必含子集)。
_SLIDES_RE = re.compile(r"^ppt/slides/slide\d+\.xml$")
# 提取文件名末尾数字 (8.4 自然排序)。
_DIGIT_RE = re.compile(r"\d+")


def parse_office(
    buffer: bytes,
    *,
    extension: str,
    encoding: str = "utf-8",
) -> str:
    """解压 .pptx (OOXML zip) → 抽 ``a:p`` 段落文本 → 返回纯文本。

    Args:
        buffer: 文件二进制内容。
        extension: 文件后缀 (无 ``.`` 前缀); 当前仅支持 ``"pptx"``。
        encoding: 首选文本编码, 解析失败降级 ``utf-8``。

    Returns:
        抽取出的纯文本: slide 间 ``\\n``, 段间 ``\\n``。

    Raises:
        RAGError: ``code=READER_PARSE`` — 非 pptx extension / 无 slide 文件 / XML 解析失败。
    """
    # 8.3 default 拒绝: 仅 pptx。
    if extension.lower() != "pptx":
        raise RAGError(
            code=ReaderErrorCode.PARSE,
            message=f"解析 PPT 失败: 不支持的 extension '{extension}', 仅支持 'pptx'",
        )

    # 8.1 落盘: mkstemp 拿 fd + path, 写 buffer, 立刻关闭 fd (后续用 path)。
    # tempfile.mkstemp 返回 (fd, path); 必须 os.close(fd) 释放句柄, 否则:
    # 1. 进程持有 fd, mkstemp 的清理策略不会自动回收
    # 2. Windows 上 fd 持有会锁住文件, 后续 ZipFile 无法打开
    tmp_dir = DEFAULT_TMP_DIR
    os.makedirs(tmp_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(suffix="." + extension, dir=tmp_dir)
    try:
        _write_buffer_to_fd(fd, tmp_path, buffer)
    except Exception:
        _close_fd_quietly(fd)
        _safe_unlink(tmp_path)
        raise

    try:
        return _extract_pptx_text(tmp_path, encoding)
    finally:
        # 8.7 清理: 移除临时文件 (无论成功失败, finally 保证不泄漏)。
        _safe_unlink(tmp_path)


# ─────────────────────────────────────────────────────────────────────────────
# 内部实现
# ─────────────────────────────────────────────────────────────────────────────


def _safe_unlink(path: str) -> None:
    """静默删除临时文件: 任务描述推荐 ``.catch(() => {})`` 风格的清理。"""
    try:
        os.unlink(path)
    except OSError as e:
        logger.debug("parse_office: temp file cleanup failed path=%s err=%s", path, e)


def _write_buffer_to_fd(fd: int, path: str, buffer: bytes) -> None:
    """8.1 写 buffer: 正常路径 ``os.write``, 失败则 ``utf-8`` fallback (对齐 parseOffice.ts)。"""
    try:
        try:
            os.write(fd, buffer)
        except UnicodeEncodeError:
            # buffer 不是 str 时 os.write 不会抛 UnicodeEncodeError; 此分支保留仅为对齐契约。
            with open(path, "wb") as fh:
                fh.write(buffer)
    finally:
        # 关闭 fd 是关键: 后续用 path, 不能依赖 mkstemp 自动释放。
        try:
            os.close(fd)
        except OSError:
            pass


def _close_fd_quietly(fd: int) -> None:
    """异常路径上 close fd; 不抛错掩盖原异常。"""
    try:
        os.close(fd)
    except OSError:
        pass


def _extract_pptx_text(tmp_path: str, encoding: str) -> str:
    """从 .pptx zip 抽取文本。8.2-8.6 全部逻辑在这里。"""
    try:
        # 8.2 解压: 不写盘, 直接 ZipFile 内存读。
        return _extract_pptx_text_inner(tmp_path, encoding)
    except zipfile.BadZipFile as e:
        # buffer 不是合法 zip → 与"无 slide 文件"同样视为解析失败。
        raise RAGError(
            code=ReaderErrorCode.PARSE,
            message=f"解析 PPT 失败: 非合法 zip 文件: {e}",
        ) from e


def _extract_pptx_text_inner(tmp_path: str, encoding: str) -> str:
    """实际 ZIP 解压 + 排序 + XML 抽取。"""
    with zipfile.ZipFile(tmp_path, "r") as zf:
        # namelist() 全名列表, 过滤出目标 xml。
        all_names = [n for n in zf.namelist() if _ALL_FILES_RE.match(n)]

        # 8.3 校验: 必须至少有一个 slide\d+.xml (notesSlides 单独不算)。
        if not all_names or not any(_SLIDES_RE.match(n) for n in all_names):
            raise RAGError(
                code=ReaderErrorCode.PARSE,
                message="解析 PPT 失败: 未找到 ppt/slides/slide*.xml",
            )

        # 8.4 排序: 按文件名中数字升序, slide1 < slide2 < slide10。
        sorted_names = sorted(all_names, key=_slide_number_key)

        # 8.5 读 XML: 优先 encoding, 失败降级 utf-8。
        xml_blobs = [_read_member(zf, name, encoding) for name in sorted_names]

        # 8.6 抽文本: 每张 slide 输出一个字符串 (段间 \n), slide 间再 \n 拼接。
        slide_texts: list[str] = []
        for blob in xml_blobs:
            slide_text = _extract_slide_text(blob)
            if slide_text:
                slide_texts.append(slide_text)
        return "\n".join(slide_texts)


def _slide_number_key(name: str) -> tuple[int, str]:
    """排序键: (数字, 原名) — 数字相同时保留 zip 内顺序作为稳定排序。

    用 ``parseInt`` 单数字键, 但当数字相同 (例如 slide1.xml + notesSlide1.xml)
    时 sort 是稳定的, 我们用元组保证跨实现也稳定。
    """
    match = _DIGIT_RE.search(name)
    num = int(match.group()) if match else 0
    return (num, name)


def _read_member(zf: zipfile.ZipFile, name: str, encoding: str) -> bytes:
    """8.5: encoding 优先, decode 失败降级 utf-8。返回原始 bytes (留给 ET.fromstring)。"""
    raw = zf.read(name)
    # encoding 优先尝试解码, 失败则视为字节流直接交给 ET (ET 会按 XML 声明或默认 utf-8 解码)。
    try:
        raw.decode(encoding)
        return raw
    except (UnicodeDecodeError, LookupError):
        if encoding.lower().replace("-", "") != "utf8":
            logger.debug(
                "parse_office: encoding fallback utf-8 for %s (was %s)", name, encoding
            )
        return raw


def _extract_slide_text(xml_bytes: bytes) -> str:
    """8.6: 从单个 slide/notesSlide XML bytes 抽取 ``a:p`` 段文本 (段间 ``\\n``)。

    行为:
    - ``getElementsByTagName('a:p')`` 拿所有 ``a:p`` (含嵌套, ET.fromstring 后直接迭代)
    - 过滤无 ``a:t`` 子节点的段落
    - 每段: ``a:t`` 文本 join('') (a:t 的 childNodes[0].nodeValue 即 a:t.text)
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise RAGError(
            code=ReaderErrorCode.PARSE,
            message=f"解析 PPT 失败: XML 解析错误: {e}",
        ) from e

    para_texts: list[str] = []
    # iter('a:p') 等价于遍历所有 a:p; ElementTree 不支持 getElementsByTagName 的扁平递归,
    # 但 a:p 元素在 OOXML 中不嵌套 (段不嵌段), 直接 root.iter() 已足够。
    for para in root.iter(PARA_TAG):
        # 8.6 过滤: 无 a:t 子节点 → 跳过。
        text_nodes = list(para.iter(TEXT_TAG))
        if not text_nodes:
            continue
        # 8.6 段内拼接: a:t 文本 join。Element 的 .text 即第一个文本子节点内容;
        # 当 a:t 含其他元素子节点时 (.tail 不适用), 用 .iter() 收集所有 TEXT_TAG 文本。
        chunk = "".join((t.text or "") for t in text_nodes)
        if chunk:
            para_texts.append(chunk)
    return "\n".join(para_texts)
