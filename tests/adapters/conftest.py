import pytest


@pytest.fixture(autouse=True)
def _allow_fake_hosts(monkeypatch):
    # test hosts (j.test, loki.test) don't resolve; the SSRF guard has its own tests in tests/security
    monkeypatch.setenv("SUPDEV_ALLOW_PRIVATE_URLS", "1")
    monkeypatch.setenv("SUPDEV_ALLOW_HTTP", "1")
