import json
import pytest
from core.llm import generate_json, LLMError, MODEL


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Resp:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [_Block(text)]
        self.stop_reason = stop_reason


class _Messages:
    def __init__(self, resp):
        self._resp = resp
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self._resp


class _Client:
    def __init__(self, resp):
        self.messages = _Messages(resp)


SCHEMA = {
    "type": "object",
    "properties": {"signal": {"type": "string"}},
    "required": ["signal"],
    "additionalProperties": False,
}


def test_generate_json_parses():
    client = _Client(_Resp(json.dumps({"signal": "觀望"})))
    out = generate_json("sys", "user", SCHEMA, client=client)
    assert out == {"signal": "觀望"}
    assert client.messages.kwargs["model"] == MODEL
    assert "temperature" not in client.messages.kwargs


def test_generate_json_refusal_raises():
    client = _Client(_Resp("", stop_reason="refusal"))
    with pytest.raises(LLMError, match="refused"):
        generate_json("sys", "user", SCHEMA, client=client)
