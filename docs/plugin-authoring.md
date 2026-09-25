# Writing plugins

Ship a normal Python package and declare entry points; supdev discovers them at startup
(`supdev plugins` lists them; a broken plugin is skipped and logged, never fatal). For quick experiments,
`SUPDEV_PLUGINS="supdev.modes:review=my_pkg:ReviewMode"` loads a dotted path.

## Integration (capability adapter)
```python
from supdev import capabilities as cap          # vendor-neutral ToolSpecs — reuse them
class MyAdapter:
    capability, vendor = "metrics", "datadog"
    def tools(self): return cap.metrics("datadog", env="prod")
    async def call(self, tool, args): ...        # return JSON-able data; raise on error
class DatadogFactory:                            # entry point: supdev.capabilities  →  name == integration key
    def create(self, tenant, secrets): ...      # tenant.integrations["datadog"]; secrets.get(tenant.tenant_id, name)
```
Rules: tag every tool (`capability`, `access`, `environment`); write tools **must** declare `approval_kind`
(the gateway refuses write tools without a gate); execution tools return `{"passed": bool}`; never log credentials.

## Mode
Subclass `supdev.modes.base.ModeBase`: `phases()` (with `exit_check`s), `check_tool()` guardrails, `mode_tools()`
(engine-native tools), `template_phases`, `generic_kinds`, and a `prompt_file`. See `modes/dev` and `modes/support`.

## LLM / storage / secrets / audit / auth / redactor
Implement the Protocol in `supdev/plugins/base.py`; the registry validates required members at load time.
