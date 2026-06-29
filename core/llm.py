import json

MODEL = "claude-opus-4-8"


class LLMError(Exception):
    pass


def _default_client():
    import anthropic
    return anthropic.Anthropic()


def generate_json(system, user, schema, client=None):
    """呼叫 Claude 並強制結構化 JSON 輸出，回 parsed dict。"""
    client = client or _default_client()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        thinking={"type": "adaptive"},
        system=system,
        output_config={
            "effort": "high",
            "format": {"type": "json_schema", "schema": schema},
        },
        messages=[{"role": "user", "content": user}],
    )
    if getattr(resp, "stop_reason", None) == "refusal":
        raise LLMError("Claude refused the request")
    text = next(
        (b.text for b in resp.content if getattr(b, "type", None) == "text"),
        None,
    )
    if not text:
        raise LLMError("No text content in response")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError(f"Invalid JSON: {e}") from e
