import asyncio
import json
import re
import time

MODEL = "claude-opus-4-8"


class LLMError(Exception):
    pass


def _extract_json(text):
    """從模型輸出抽出 JSON dict（容忍 ```json 圍欄與前後雜訊）。"""
    if not text or not text.strip():
        raise LLMError("No text content in response")
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.DOTALL)
    if fence:
        t = fence.group(1).strip()
    if not t.startswith("{"):
        start, end = t.find("{"), t.rfind("}")
        if start != -1 and end > start:
            t = t[start:end + 1]
    try:
        return json.loads(t)
    except json.JSONDecodeError as e:
        raise LLMError(f"Invalid JSON: {e}") from e


async def _agent_complete(system, user, schema, model):
    """用 Claude Agent SDK（吃 Max 訂閱認證）跑一次查詢，回最終文字。

    schema=None → 自由文字回答；否則要求只輸出符合 schema 的 JSON。
    """
    from claude_agent_sdk import query, ClaudeAgentOptions

    if schema is not None:
        full_system = (
            f"{system}\n\n"
            "只輸出一個合法的 JSON 物件，必須符合以下 JSON schema；"
            "不要任何說明文字、前言或 markdown 圍欄。\n"
            f"{json.dumps(schema, ensure_ascii=False)}"
        )
    else:
        full_system = system
    options = ClaudeAgentOptions(
        model=model,
        system_prompt=full_system,
        allowed_tools=[],   # 純文字生成，不需任何工具
        max_turns=6,        # 給足回合；偶爾第一則非最終結果會被 max_turns=1 誤殺
    )
    text = None
    async for message in query(prompt=user, options=options):
        result = getattr(message, "result", None)
        if result is not None:
            text = result
    return text


def _default_complete(system, user, schema, attempts=3):
    """跑 Agent SDK，對偶發錯誤(含 Reached maximum number of turns、空回覆)重試。"""
    last = None
    for i in range(attempts):
        try:
            text = asyncio.run(_agent_complete(system, user, schema, MODEL))
            if text and text.strip():
                return text
            last = LLMError("Empty response from Agent SDK")
        except Exception as e:  # noqa: BLE001 — SDK 會丟泛型 Exception
            last = e
        if i < attempts - 1:
            time.sleep(2 * (i + 1))
    raise last if last is not None else LLMError("Agent SDK failed")


def generate_json(system, user, schema, complete=None):
    """產生結構化 JSON（透過 Claude Agent SDK / 訂閱認證），回 parsed dict。

    complete: 可注入的文字產生函式 (system, user, schema) -> str，方便測試。
    """
    runner = complete or _default_complete
    text = runner(system, user, schema)
    return _extract_json(text)


def generate_text(system, user, complete=None):
    """自由文字回答（非 JSON），供機器人自然語言問答用。回純文字字串。

    complete: 可注入的文字產生函式 (system, user, schema) -> str，方便測試。
    """
    runner = complete or _default_complete
    return (runner(system, user, None) or "").strip()
