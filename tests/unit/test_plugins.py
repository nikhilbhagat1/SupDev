import sys
from pathlib import Path

import pytest

from supdev.core.errors import PluginError
from supdev.plugins.base import PluginKind
from supdev.plugins.registry import PluginRegistry, default_registry

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "examples"))


def test_builtin_plugins_discovered():
    reg = default_registry()
    assert {"dev", "support"} <= set(reg.names(PluginKind.MODE))
    assert {"fake", "anthropic"} <= set(reg.names(PluginKind.LLM))
    assert "sqlite" in reg.names(PluginKind.STORAGE) and "regex" in reg.names(PluginKind.REDACTOR)


def test_duplicate_plugin_rejected():
    reg = PluginRegistry()
    reg.register(PluginKind.SECRETS, "x", type("S", (), {"get": lambda s, t, n: None}))
    with pytest.raises(PluginError, match="duplicate"):
        reg.register(PluginKind.SECRETS, "x", type("S", (), {"get": lambda s, t, n: None}))


def test_invalid_plugin_rejected_with_missing_members():
    reg = PluginRegistry()
    with pytest.raises(PluginError, match="missing: authenticate"):
        reg.register(PluginKind.AUTH, "bad", object())


def test_third_party_plugins_load_without_core_changes():
    reg = default_registry({
        "supdev.modes:review": "supdev_example_plugin:ReviewMode",
        "supdev.capabilities:statuspage": "supdev_example_plugin:StatuspageFactory",
    })
    assert "review" in reg.names(PluginKind.MODE) and "statuspage" in reg.names(PluginKind.CAPABILITY)


def test_broken_plugin_is_isolated_not_fatal():
    reg = PluginRegistry()
    problems = reg.discover({"supdev.modes:nope": "does.not.exist:X"})
    assert problems and "nope" not in reg.names(PluginKind.MODE)
    with pytest.raises(PluginError):
        PluginRegistry().discover({"supdev.modes:nope": "does.not.exist:X"}, strict=True)
