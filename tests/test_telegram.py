import core.telegram as tg


class _Resp:
    status_code = 200


def test_send_missing_token_returns_false(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert tg.send("hi") is False


def test_send_posts_with_env(monkeypatch):
    calls = {}

    def fake_post(url, data=None, timeout=None):
        calls["url"] = url
        calls["data"] = data
        return _Resp()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TOK")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(tg.requests, "post", fake_post)

    assert tg.send("hello") is True
    assert "TOK" in calls["url"]
    assert calls["data"]["chat_id"] == "123"
    assert calls["data"]["text"] == "hello"
