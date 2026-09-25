# 01 Plugin kernel

**Purpose** Make modes, integrations, LLMs and infrastructure swappable without core edits.

**Requirements** Modules are pluggable (user requirement). Bad third-party plugins must not break startup. Duplicates rejected.

**Design** `PluginRegistry` (`plugins/registry.py`) keyed by `PluginKind` = entry-point group (`supdev.llm|capabilities|modes|storage|secrets|audit|auth|redactors`).
`discover()` loads entry points + `SUPDEV_PLUGINS` dotted paths (`kind:name=pkg.mod:Attr`). `register()` instantiates classes with no args and checks required members per kind (`_REQUIRED`). Failures are collected and logged; `strict=True` raises.

**Interfaces** Protocols in `plugins/base.py`: `LLMProvider`, `CapabilityAdapterFactory`/`CapabilityAdapter`, `Mode`, `SessionStore`, `SecretStore`, `AuditSink`, `Authenticator`, `Redactor`. Capability factory name == tenant integration key.

**Acceptance → tests** (`tests/unit/test_plugins.py`)
- built-ins discovered → `test_builtin_plugins_discovered`
- duplicate rejected → `test_duplicate_plugin_rejected`
- missing members rejected with names → `test_invalid_plugin_rejected_with_missing_members`
- third-party mode + integration load with no core change → `test_third_party_plugins_load_without_core_changes` (uses `examples/supdev_example_plugin`)
- broken plugin isolated; strict raises → `test_broken_plugin_is_isolated_not_fatal`

**Test gaps** Structural check only (no signature validation); no test for installed-package entry points from a *separate* distribution.
**Out of scope** Plugin sandboxing/permissions; hot reload; versioned plugin API.
