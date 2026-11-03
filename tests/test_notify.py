import importlib

import requests

import notify as notify_module


class Resp:
    def __init__(self, status, text=""):
        self.status_code, self.text = status, text


def real_notify(monkeypatch, cfg):
    """The conftest stubs out send/_config; reload to test the real functions."""
    mod = importlib.reload(notify_module)
    monkeypatch.setattr(mod, "_config", lambda: cfg)
    return mod


def test_send_posts_message_and_is_disabled_without_config(monkeypatch):
    mod = real_notify(monkeypatch, ("123:ABC", "42"))
    calls = []
    assert mod.send("hello", post=lambda url, **kw: calls.append((url, kw["json"])) or Resp(200))
    assert calls == [("https://api.telegram.org/bot123:ABC/sendMessage",
                      {"chat_id": "42", "text": "[MCR] hello", "disable_web_page_preview": True})]
    monkeypatch.setattr(mod, "_config", lambda: None)
    assert not mod.send("hello", post=lambda *a, **k: 1 / 0)


def test_send_never_raises_and_hides_token(monkeypatch, caplog):
    mod = real_notify(monkeypatch, ("123:ABC", "42"))

    def boom(url, **kw):
        raise requests.ConnectionError(f"failed {url}")

    assert not mod.send("x", post=boom)
    assert not mod.send("x", post=lambda url, **kw: Resp(401, f"bad token 123:ABC"))
    assert "123:ABC" not in caplog.text


def test_hint_maps_kis_codes():
    assert "기간 만료" in notify_module.hint("40910000: 모의투자 주문이 불가한 계좌입니다.")
    assert notify_module.hint("something else") == ""
