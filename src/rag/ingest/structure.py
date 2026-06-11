import re

from pydantic import BaseModel

_HEADING_RE = re.compile(r"^(#{1,5})\s+(.+)$", re.MULTILINE)
_CODE_RE = re.compile(r"```[\s\S]*?```|~~~[\s\S]*?~~~")
_TABLE_RE = re.compile(r"^\|.+\|$", re.MULTILINE)


class Heading(BaseModel):
    level: int
    text: str
    line: int
    children: list["Heading"] = []


class DocumentStructure(BaseModel):
    heading_tree: list[Heading] = []
    list_nesting_depth: int = 0
    has_code_blocks: bool = False
    has_tables: bool = False
    page_count: int | None = None


def _nest(flat: list[Heading]) -> list[Heading]:
    """把 flat heading list 构造成嵌套树。

    用栈维护祖先链: 新 heading 进来时弹出 level >= 自身的栈顶,
    再挂到当前栈顶 children 或 roots。
    """
    roots: list[Heading] = []
    stack: list[Heading] = []
    for heading in flat:
        heading.children = []
        while stack and stack[-1].level >= heading.level:
            stack.pop()
        if not stack:
            roots.append(heading)
        else:
            stack[-1].children.append(heading)
        stack.append(heading)
    return roots


def _list_nesting_depth(text: str) -> int:
    """计算 Markdown 无序列表的最大嵌套深度。"""
    max_depth = 0
    for match in re.finditer(r"^( {0,10})([-*+])\s+", text, re.MULTILINE):
        depth = len(match.group(1)) // 2 + 1
        if depth > max_depth:
            max_depth = depth
    return max_depth


def _page_count(text: str, chars_per_page: int = 2000) -> int:
    """按字符数估算页数; PDF 等格式可在上层用真实页数覆盖。"""
    return max(1, (len(text) + chars_per_page - 1) // chars_per_page)


def extract_markdown_structure(text: str) -> DocumentStructure:
    """从 Markdown 文本提取标题树与文档级结构元信息。"""
    headings: list[Heading] = []
    for match in _HEADING_RE.finditer(text):
        level = len(match.group(1))
        headings.append(
            Heading(
                level=level,
                text=match.group(2).strip(),
                line=text[: match.start()].count("\n"),
            )
        )

    nested = _nest(headings)
    return DocumentStructure(
        heading_tree=nested,
        list_nesting_depth=_list_nesting_depth(text),
        has_code_blocks=bool(_CODE_RE.search(text)),
        has_tables=bool(_TABLE_RE.search(text)),
        page_count=_page_count(text),
    )
