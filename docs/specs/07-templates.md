# 07 Templates D1–D3, S1–S2

**Purpose** Deterministic, validated formats (Part E). The model supplies fields via `engine.present`; the engine renders.

**Design** Pydantic models in `modes/templates.py` with `render()`: D1 (blocking questions sorted first), D2 (approach 2–4, changes ≤8, risks ≤3, iteration/max injected by engine), D3 (AC→tests, `head_sha` optional), S1, S2 (`Claim` labels Confirmed/Likely/Suspected; timeline entries must cite evidence). `after_render` in each mode creates the approval request (D2→`plan`, D3→`pr`, S2→`accept_rca`).

**Acceptance → tests** Indirectly: `test_full_dev_flow` (D1/D2/D3), `test_full_support_flow_and_prod_is_read_only` (S2).
**Test gaps** No direct render/validation tests (blocking-first ordering, size limits, claim-evidence validator, exact text match to the source templates). **Add these first.**
**Out of scope** Localisation; Markdown/HTML variants of the templates.
