import pytest

import notify


@pytest.fixture(autouse=True)
def no_real_alerts(monkeypatch):
    """Tests must never message a real Telegram chat, even if .env has a bot configured."""
    sent = []
    monkeypatch.setattr(notify, "_config", lambda: None)
    monkeypatch.setattr(notify, "send", lambda text, **kw: sent.append(text) or True)
    return sent
