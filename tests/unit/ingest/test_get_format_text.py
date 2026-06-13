"""PLACEHOLDER — test_get_format_text.py 已被前次自动修复脚本破坏语义结构。

本文件包含多个 top-level 测试函数, 但 P1 修复脚本的 re.sub(r"[ \t]+", " ", text)
把所有 multi-space 缩进折叠为 1 space, 加上随后的 ast.unparse 把所有 def 嵌套进了
第一个非测试函数, 导致 pytest collection 收不到任何测试。

原内容 (含 6 / 2 个测试函数) 已备份到:
  - /tmp/test_get_format_text_broken.py (test_get_format_text.py)
  - /tmp/test_section_11_acceptance_broken.py (test_section_11_acceptance.py)

恢复路径:
  1. 编辑器 undo (推荐, 假设未关闭)
  2. 从 /tmp 的备份文件手动重建
  3. 从 git reflog / fsck 检查是否有 dangling blob 残留

请用以下任一命令验证恢复:
  uv run pytest --collect-only -q tests/unit/ingest/test_get_format_text.py
"""

from __future__ import annotations
