import json
import pytest
from core.llm import generate_json, LLMError, MODEL

SCHEMA = {
    "type": "object",
    "properties": {"signal": {"type": "string"}},
    "required": ["signal"],
    "additionalProperties": False,
}


def test_model_is_opus():
    assert MODEL == "claude-opus-4-8"


def test_generate_json_parses():
    out = generate_json(
        "sys", "user", SCHEMA,
        complete=lambda s, u, sc: json.dumps({"signal": "觀望"}),
    )
    assert out == {"signal": "觀望"}


def test_generate_json_strips_markdown_fence():
    text = "```json\n{\"signal\": \"進場\"}\n```"
    out = generate_json("sys", "user", SCHEMA, complete=lambda s, u, sc: text)
    assert out == {"signal": "進場"}


def test_generate_json_extracts_embedded_object():
    text = "結果如下：{\"signal\": \"避開\"} 以上。"
    out = generate_json("sys", "user", SCHEMA, complete=lambda s, u, sc: text)
    assert out == {"signal": "避開"}


def test_generate_json_empty_raises():
    with pytest.raises(LLMError, match="No text content"):
        generate_json("sys", "user", SCHEMA, complete=lambda s, u, sc: "")


def test_generate_json_invalid_raises():
    with pytest.raises(LLMError, match="Invalid JSON"):
        generate_json("sys", "user", SCHEMA, complete=lambda s, u, sc: "not json")
