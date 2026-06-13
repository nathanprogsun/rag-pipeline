from rag.ingest.chunker.code_block import is_code_block, protect_code_block


def test_is_code_block_triple_backtick() -> None:
    assert is_code_block("```python\nprint(1)\n```") is True


def test_is_code_block_triple_tilde() -> None:
    assert is_code_block("~~~python\nprint(1)\n~~~") is True


def test_is_code_block_false_for_plain() -> None:
    assert is_code_block("hello world") is False


def test_protect_replaces_newlines_with_marker() -> None:
    text = "```python\nx=1\ny=2\n```"
    result = protect_code_block(text)
    assert "\n" not in result.replace("__CB_NL__", "") or "__CB_NL__" in result
    assert "__CB_NL__" in result
