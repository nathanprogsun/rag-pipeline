"""chat / structured chat 单元测试 — mock 第三方 API，覆盖非 happy path。"""

from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel, Field, ValidationError

from rag.infra.llm.chat import get_structured_chat_model


class QueryPlan(BaseModel):
    queries: list[str] = Field(min_length=1, description="expanded queries")


class TestGetStructuredChatModel:
    def test_delegates_to_with_structured_output(self) -> None:
        mock_base = MagicMock()
        mock_structured = MagicMock()
        mock_base.with_structured_output.return_value = mock_structured

        with patch("rag.infra.llm.chat.get_chat_model", return_value=mock_base):
            result = get_structured_chat_model(QueryPlan)

        assert result is mock_structured
        mock_base.with_structured_output.assert_called_once_with(
            QueryPlan,
            method="function_calling",
            include_raw=False,
        )

    def test_forwards_runtime_options_to_get_chat_model(self) -> None:
        mock_base = MagicMock()
        mock_base.with_structured_output.return_value = MagicMock()

        with patch(
            "rag.infra.llm.chat.get_chat_model", return_value=mock_base
        ) as mock_get:
            get_structured_chat_model(
                QueryPlan,
                model="custom-model",
                temperature=0.0,
                timeout=12.5,
                max_retries=2,
                base_url="https://example.com/v1",
                api_key="sk-test",
            )

        mock_get.assert_called_once_with(
            model="custom-model",
            temperature=0.0,
            timeout=12.5,
            max_retries=2,
            base_url="https://example.com/v1",
            api_key="sk-test",
        )

    def test_propagates_get_chat_model_initialization_error(self) -> None:
        with (
            patch(
                "rag.infra.llm.chat.get_chat_model",
                side_effect=ValueError("invalid api key"),
            ),
            pytest.raises(ValueError, match="invalid api key"),
        ):
            get_structured_chat_model(QueryPlan)


class TestStructuredChatSchemaContract:
    """Pydantic schema 约束（parser 失败模式）。"""

    def test_model_validate_json_rejects_wrong_argument_types(self) -> None:
        with pytest.raises(ValidationError):
            QueryPlan.model_validate_json('{"queries": "not-a-list"}')

    def test_model_validate_json_rejects_missing_required_fields(self) -> None:
        with pytest.raises(ValidationError):
            QueryPlan.model_validate_json("{}")

    def test_model_validate_json_rejects_empty_queries_array(self) -> None:
        with pytest.raises(ValidationError):
            QueryPlan.model_validate_json('{"queries": []}')

    def test_model_validate_json_accepts_valid_payload(self) -> None:
        parsed = QueryPlan.model_validate_json('{"queries": ["rag", "pipeline"]}')
        assert parsed.queries == ["rag", "pipeline"]
