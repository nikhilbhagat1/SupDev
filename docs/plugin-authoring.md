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
Optional duck-typed hooks the platform uses when present (implement them and the feature lights up for your vendor):

| Method | Used for |
|---|---|
| `async ping() -> str` | the Settings **Test connection** button (raise with an actionable message on failure) |
| Jira-style ticketing: `issues()`, `board_columns()`, `move_issue()`, `releases()`, `release_issues()`, `project_meta()`, `transition_to_named()` | the Kanban board, release filter, drag-to-move, Settings pickers and phase→status sync (see `adapters/jira.py`) |

Rules: tag every tool (`capability`, `access`, `environment`); write tools **must** declare `approval_kind`
(the gateway refuses write tools without a gate); execution tools return `{"passed": bool}`; never log credentials.

## Mode
Subclass `supdev.modes.base.ModeBase`: `name` and a human **`label`** (required — the UI builds its mode toggle from it), `phases()` (with
`exit_check`s; set `PhaseSpec(hint="work" | "review")` on the phase where work begins / goes out for review so Settings can suggest Jira
statuses by category), `check_tool()` guardrails, `mode_tools()` (engine-native tools), `template_phases`, `generic_kinds`, and a
`prompt_file`. See `modes/dev` and `modes/support`. The mode then appears in the UI and `/api/modes` with no other change.

## LLM / storage / secrets / audit / auth / redactor
Implement the Protocol in `supdev/plugins/base.py`; the registry validates required members at load time.
