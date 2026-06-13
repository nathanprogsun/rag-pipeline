"""``pdf_text_postprocess.postprocess_lite_parse_pages`` 单元测试 (简化版)。

覆盖:
  - basic: 多页文本 → 段落正确 (页间 \\n\\n, 末尾 \\n)
  - repeated_header: 3 页都有相同首行 → 删 header
  - repeated_footer: 3 页都有相同末行 → 删 footer
  - pure_page_number: 删 ``^1$``, ``^- 2 -$``, ``^Page 3$``
  - visual_line_merge: 段末标点不合并, 普通行合并, bullet 不合并
  - unicode_normalize: NFKC 全角转半角
  - empty_pages: 空输入 → 空字符串
  - all_params_signature: 11 个参数签名可被显式传参
"""

from __future__ import annotations

from rag.ingest.reader.pdf_text_postprocess import postprocess_lite_parse_pages

# ── basic ──


def test_postprocess_basic() -> None:
    """多页文本 → 段间 ``\\n\\n``, 末尾保留 ``\\n``。"""
    pages = [
        "第一段。\n第二段。\n",
        "第二页唯一段。\n",
    ]
    out = postprocess_lite_parse_pages(pages)
    # 页间 ``\n\n`` 隔开, 末尾 ``\n`` 保留
    assert "第一段。" in out
    assert "第二段。" in out
    assert "第二页唯一段。" in out
    assert out.endswith("\n")
    # 页间分隔
    assert "\n\n" in out


def test_postprocess_empty_pages() -> None:
    """空输入 → 空字符串。"""
    assert postprocess_lite_parse_pages([]) == ""


def test_postprocess_empty_page_text() -> None:
    """空字符串 page 也 OK, 不会抛。"""
    out = postprocess_lite_parse_pages(["", "  ", "正文内容。"])
    assert "正文内容。" in out


# ── repeated_header ──


def test_postprocess_repeated_header() -> None:
    """3 页都有相同首行 → 标记为 noise, 全部删除。

    简化版用文本特征: 出现 ≥ 3 次的短行视为 noise。
    """
    pages = [
        "公司机密 第 1 章\n\n第一页正文。",
        "公司机密 第 1 章\n\n第二页正文。",
        "公司机密 第 1 章\n\n第三页正文。",
    ]
    out = postprocess_lite_parse_pages(
        pages, repeated_noise_min_count=3, repeated_noise_max_length=30
    )
    assert "公司机密" not in out
    assert "第一页正文" in out
    assert "第二页正文" in out
    assert "第三页正文" in out


# ── repeated_footer ──


def test_postprocess_repeated_footer() -> None:
    """3 页都有相同末行 (例如页脚) → 标记为 noise, 全部删除。"""
    pages = [
        "第一页正文\n\n© 2024 ACME",
        "第二页正文\n\n© 2024 ACME",
        "第三页正文\n\n© 2024 ACME",
    ]
    out = postprocess_lite_parse_pages(
        pages, repeated_noise_min_count=3, repeated_noise_max_length=30
    )
    assert "ACME" not in out
    assert "第一页正文" in out
    assert "第二页正文" in out


def test_postprocess_repeated_only_min_count() -> None:
    """出现 2 次 (< 3) → 不视为 noise。"""
    pages = [
        "页头\n\n第一页。",
        "页头\n\n第二页。",
        "另一行\n\n第三页。",
    ]
    out = postprocess_lite_parse_pages(pages, repeated_noise_min_count=3)
    assert "页头" in out  # 只出现 2 次, 不删
    assert "另一行" in out


# ── pure_page_number ──


def test_postprocess_pure_page_number() -> None:
    """删 ``^1$``, ``^- 2 -$``, ``^Page 3$``, ``^1/3$``。"""
    pages = [
        "第一页正文。\n\n1\n\n后续。",
        "第二页正文。\n\n- 2 -\n\n后续。",
        "第三页正文。\n\nPage 3\n\n后续。",
        "第四页正文。\n\n1/3\n\n后续。",
    ]
    out = postprocess_lite_parse_pages(pages)
    assert "第一页正文。" in out
    assert "第二页正文。" in out
    assert "第三页正文。" in out
    # 纯页码行被删 (但是 "1" 出现在 "1/3" 中, 注意 "1/3" 整行才被删)
    # 验证整行是纯数字的 "1" 不在结果里 (但 "1/3" 整行也不在结果里)
    for line in out.split("\n"):
        stripped = line.strip()
        # 任何保留行不应是纯页码
        assert stripped not in {"1", "- 2 -", "Page 3", "1/3", "2/3", "3/3"}


def test_postprocess_drop_pure_page_number_disabled() -> None:
    """``drop_pure_page_number=False`` → 纯页码行保留。"""
    pages = ["正文。\n\n1\n\n后续。"]
    out = postprocess_lite_parse_pages(pages, drop_pure_page_number=False)
    # ``1`` 在结果里
    lines = [line for line in out.split("\n") if line.strip()]
    assert "1" in lines


# ── visual_line_merge ──


def test_postprocess_visual_line_merge() -> None:
    """段末标点不合并, 普通行合并, bullet 不合并, 段间空行保留。"""
    # merge_visual_lines=True (默认)
    lines = [
        "这是第一行",
        "接续到第一行末尾。",  # 段末标点 → 独立
        "新段开始",
        "接续到新段。",  # 段末标点 → 独立
        "",
        "- bullet 1",  # bullet → 独立
        "- bullet 2",  # bullet → 独立
    ]
    out = postprocess_lite_parse_pages(["\n".join(lines)], merge_visual_lines=True)

    # "第一行" + "接续到第一行末尾。" 拼一起 (上一行无段末标点)
    # 但 "接续到第一行末尾。" 行末有句号, 所以 "新段开始" 独立
    assert "这是第一行 接续到第一行末尾。" in out
    assert "新段开始 接续到新段。" in out
    # bullet 独立
    assert "- bullet 1" in out
    assert "- bullet 2" in out


def test_postprocess_visual_line_merge_disabled() -> None:
    """``merge_visual_lines=False`` → 全部行保留独立。"""
    lines = ["第一行", "接续行。", ""]
    out = postprocess_lite_parse_pages(["\n".join(lines)], merge_visual_lines=False)
    # 三行各自独立 (中间有空行)
    assert "第一行" in out
    assert "接续行。" in out


def test_postprocess_visual_line_merge_chinese_period() -> None:
    """中文段末标点 ``。`` 也算段末。"""
    lines = ["第一行", "接续行。", "新段"]
    out = postprocess_lite_parse_pages(["\n".join(lines)])
    # "接续行。" 以 ``。`` 结尾 → "新段" 独立 (段间换行)
    assert "第一行 接续行。" in out
    assert "新段" in out
    # "新段" 不应接续到 "接续行。" 后面 (段间必有换行)
    assert "接续行。\n新段" in out
    assert "接续行。 新段" not in out


def test_postprocess_visual_line_merge_bullet() -> None:
    """bullet 行不与上一行合并。"""
    lines = ["标题", "- item 1", "- item 2"]
    out = postprocess_lite_parse_pages(["\n".join(lines)])
    # "标题" 独立, bullet 各自独立
    assert "标题" in out
    assert "- item 1" in out
    assert "- item 2" in out


# ── unicode_normalize ──


def test_postprocess_unicode_normalize() -> None:
    """``normalize_unicode=True`` → NFKC 全角转半角等。"""
    pages = ["Ｈｅｌｌｏ Ｗｏｒｌｄ"]
    out = postprocess_lite_parse_pages(pages, normalize_unicode=True)
    # NFKC 把全角英文字母转半角
    assert out == "Hello World\n" or out == "Hello World"


def test_postprocess_unicode_normalize_disabled() -> None:
    """默认 ``normalize_unicode=False`` → 全角字符保留。"""
    pages = ["Ｈｅｌｌｏ"]
    out = postprocess_lite_parse_pages(pages)
    # 全角字符保留
    assert "Ｈｅｌｌｏ" in out


# ── 11 参数签名 ──


def test_postprocess_all_params_signature() -> None:
    """11 个参数全部显式传参 → 正常运行。"""
    pages = ["测试文本。"]
    out = postprocess_lite_parse_pages(
        pages,
        trim_page_edge=True,
        header_ratio=0.05,
        footer_ratio=0.05,
        line_y_ratio=0.55,
        min_space_gap_ratio=0.35,
        wide_space_gap_ratio=1.2,
        merge_visual_lines=True,
        remove_repeated_page_noise=True,
        repeated_noise_min_count=3,
        repeated_noise_max_length=30,
        drop_pure_page_number=True,
        normalize_unicode=False,
    )
    assert "测试文本。" in out


# ── max_length 阈值 ──


def test_postprocess_repeated_max_length_filter() -> None:
    """长行 (例如重复的长段) 不视为 noise, 保留。"""
    long_line = "这是一段很长很长的内容 " * 10  # 超过 30 字符
    pages = [
        f"{long_line}\n\n第一段。",
        f"{long_line}\n\n第二段。",
        f"{long_line}\n\n第三段。",
    ]
    out = postprocess_lite_parse_pages(
        pages, repeated_noise_min_count=3, repeated_noise_max_length=30
    )
    # 长行虽然出现 3 次但超过 30 字符, 不视为 noise
    assert "这是一段很长很长的内容" in out


# ── 中文/英文混合 ──


def test_postprocess_chinese_paragraphs() -> None:
    """中文多页 + 空行段间分隔 + 末尾 ``\\n``。"""
    pages = [
        "第一章 简介\n\n本章介绍 RAG 系统。\n\n接下来讲 chunker。",
        "第二章 方法\n\n本章介绍 chunker 的具体实现。",
    ]
    out = postprocess_lite_parse_pages(pages)
    assert "第一章 简介" in out
    assert "第二章 方法" in out
    assert out.endswith("\n")
