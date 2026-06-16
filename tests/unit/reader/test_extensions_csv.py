"""``extensions.csv.csv_adapter`` 单元测试 (Section 4 契约)。

覆盖 (Section 11 验收):
  - basic: 简单 3x3 表
  - chinese_comma: 含中文逗号 ``, `` (Section 11 验收)
  - newline_in_cell: 单元格内 ``\n`` → ``\\n`` 转义 (Section 11 验收)
  - empty: 空 csv
  - only_header: 只有 header 没有 data
  - quoted_field: 引号字段, csv.reader 默认行为
  - format_text_markdown_structure: 验证 markdown table 格式正确 (有 ``| --- |`` 分隔行)
"""

from __future__ import annotations

import pytest

from rag.ingest.reader.extensions.csv import CSV_MIME, csv_adapter

# ── basic ──


@pytest.mark.asyncio
async def test_csv_basic() -> None:
    """简单 3x3 表: header + 2 行数据, 全部解出。"""
    buf = b"name,age,city\nAlice,30,Beijing\nBob,25,Shanghai"
    result = await csv_adapter(buf)

    assert "name" in result.raw_text
    assert "Alice" in result.raw_text
    assert "Bob" in result.raw_text
    assert "Beijing" in result.raw_text
    assert result.meta.mime == CSV_MIME
    assert result.meta.mime == "text/csv"
    assert result.format_text is not None
    assert result.images == []
    # 3 列, header + 2 数据行
    assert "Alice" in result.raw_text


# ── chinese comma ──


@pytest.mark.asyncio
async def test_csv_chinese_comma() -> None:
    """Section 11 验收: 含中文逗号 ``，`` (U+FF0C), 不应被 csv.reader 误分列。

    csv.reader 默认 excel dialect 只把半角 ``,`` (U+002C) 视作分隔符;
    中文逗号 ``，`` (U+FF0C) 是不同的字符, 不会被切, 单元格应原样保留。
    """
    # header 用半角 ``,`` 分隔, 数据行第二列含中文逗号 ``，`` 整段保留
    buf = "标题,内容\n第一行,你好，世界\n第二行,foo， bar".encode()
    result = await csv_adapter(buf)

    assert "标题" in result.raw_text
    assert "你好，世界" in result.raw_text
    assert "foo， bar" in result.raw_text
    assert result.format_text is not None
    # header 2 列, 第二列含中文逗号仍为 1 个 cell
    assert "| 标题 | 内容 |" in result.format_text
    assert "| 你好，世界 |" in result.format_text
    assert "| --- | --- |" in result.format_text


# ── newline in cell ──


@pytest.mark.asyncio
async def test_csv_with_newline_in_cell() -> None:
    """Section 11 验收 + Section 4.3: 单元格内 ``\\n`` → ``\\\\n`` 转义。

    csv.reader 处理引号字段时, 字段内的 ``\n`` 会被保留;
    format_text 阶段必须把 ``\n`` 转义为 ``\\n``, 否则会破坏 markdown table 行结构。
    """
    buf = b'a,b\n"line1\nline2",x\nplain,row'
    result = await csv_adapter(buf)

    # raw_text 保留原始换行
    assert "line1\nline2" in result.raw_text
    # format_text 转义: 单元格内 \n 变 \n (字面 2 字符)
    assert result.format_text is not None
    assert "line1\\nline2" in result.format_text
    # 不应残留真实的换行在数据行内 (只会出现在 header/sep/row 间)
    lines = result.format_text.split("\n")
    # header / sep / line1\nline2 row / plain row
    assert len(lines) == 4
    assert lines[2] == "| line1\\nline2 | x |"
    assert lines[3] == "| plain | row |"


# ── empty ──


@pytest.mark.asyncio
async def test_csv_empty() -> None:
    """空 csv (空 buffer) → raw_text 空, format_text None。"""
    result = await csv_adapter(b"")
    assert result.raw_text == ""
    # 无 header, format_text 应为 None
    assert result.format_text is None
    assert result.meta.mime == "text/csv"
    # 空字符串 split('\n') 后没有非空行
    assert result.raw_text == ""


# ── only header ──


@pytest.mark.asyncio
async def test_csv_only_header() -> None:
    """Section 4.3: 只有 header 没有 data → 仍输出 header + sep。"""
    buf = b"name,age,city"
    result = await csv_adapter(buf)

    assert result.raw_text == "name,age,city"
    assert result.format_text is not None
    # header + sep (无数据行)
    assert result.format_text == "| name | age | city |\n| --- | --- | --- |"


# ── quoted field ──


@pytest.mark.asyncio
async def test_csv_with_quoted_field() -> None:
    """csv.reader 默认 excel dialect 正确处理引号字段 (含分隔符/引号转义)。

    raw_text 保留 csv 原文 (含引号), format_text 把引号剥掉 / 双引号还原为单引号。
    """
    buf = b'a,b,c\n"hello, world","x""y","plain"'
    result = await csv_adapter(buf)

    # raw_text: csv 原文保留, 引号未剥
    assert "hello, world" in result.raw_text
    assert '"x""y"' in result.raw_text
    assert result.format_text is not None
    # format_text: 引号剥除, 双引号转义还原 (``""`` → ``"``), 半角逗号保留
    assert '| hello, world | x"y | plain |' in result.format_text


# ── markdown structure ──


@pytest.mark.asyncio
async def test_csv_format_text_markdown_structure() -> None:
    """验证 markdown table 格式正确:
    - 第 0 行 = header
    - 第 1 行 = ``| --- | ... |`` 分隔行
    - 后续 = 数据行
    - 单元格内 ``\\n`` 转义
    - 列数与 header 一致
    """
    buf = b"id,name,score\n1,alice,90\n2,bob,85"
    result = await csv_adapter(buf)

    assert result.format_text is not None
    lines = result.format_text.split("\n")
    assert len(lines) == 4
    # header
    assert lines[0] == "| id | name | score |"
    # 分隔行
    assert lines[1] == "| --- | --- | --- |"
    # 数据行
    assert lines[2] == "| 1 | alice | 90 |"
    assert lines[3] == "| 2 | bob | 85 |"
    # 列数对齐
    for line in lines:
        # 每行 ``|`` 数量一致: 单元格数 + 1 (两侧) = 4
        assert line.count("|") == 4
