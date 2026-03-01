# Capability Gap Process (Missing or Partial Tool Support)

Use this process when a user asks for a task that:
- has no available plugin/tool, or
- has a related plugin but not the specific method needed.

This guide is written for both human operators and AI agents.

## Purpose

- Capture the user’s intent clearly.
- Determine whether existing allowlisted capabilities can satisfy the request.
- Plan a safe, minimal toolset update when needed.
- Document development steps from request to production availability.

## Scope and Security Rules

- Keep execution constrained to allowlisted modules/classes/methods in `config.py`.
- Do not bypass permission checks in `executor/permissions.py`.
- Keep API error format stable: `{"status":"error","message":"..."}`.
- Avoid unrestricted dynamic execution (`eval`, `exec`, unsafe imports).
- Use JSON-serializable inputs/outputs for constructor args and method args.

---

## Phase 1: Document the Incoming Request

Create a request record with the fields below.

### Required Request Record

```json
{
  "request_id": "REQ-YYYYMMDD-001",
  "timestamp_utc": "2026-02-28T00:00:00Z",
  "requester": "<user_or_team>",
  "user_goal": "Plain-language outcome the user wants",
  "requested_module": "<if provided>",
  "requested_class": "<if provided>",
  "requested_method": "<if provided>",
  "input_example": {},
  "expected_output_example": {},
  "priority": "low|medium|high|urgent",
  "constraints": ["security", "latency", "compliance", "data sensitivity"]
}
```

### Clarify Before Building

1. What exact result must be produced?
2. What inputs are available, and in what format?
3. Are there timing, security, or compliance constraints?
4. What is the acceptable fallback if full automation is not available?

---

## Phase 2: Capability Check (Existing Toolset)

### 2.1 Check allowlist and available methods

- Verify module/class/method in `config.py` (`ALLOWED_MODULES`).
- Verify runtime validation path in `executor/permissions.py`.
- Verify implementation availability in `plugins/`.

## 2.2 Determine gap type

- **Type A: No matching plugin** (new plugin required)
- **Type B: Plugin exists, method missing** (extend plugin)
- **Type C: Method exists but request shape unsupported** (API contract/update)
- **Type D: Method exists but blocked by allowlist** (config update + validation)

## 2.3 Decide immediate response path

- If current tools can satisfy the goal via alternate method/workflow, provide a safe workaround.
- If not, proceed to Phase 3 and open a development plan.

---

## Phase 3: Feasibility and Update Plan

Create a concise plan document containing:

1. **Proposed change**
   - New plugin OR new method in existing plugin.
2. **Minimal API surface**
   - Method name, args schema, return schema, errors.
3. **Security impact**
   - Input validation, auth requirements, path/network limits.
4. **Allowlist changes**
   - Exact `config.py` update needed.
5. **Backward compatibility**
   - Confirm no breaking changes to existing methods.
6. **Test approach**
   - Unit/integration test cases and JSON payload examples.

### Feasibility Decision Matrix

- **Proceed now**: low risk, clear requirements, dependencies available.
- **Proceed with guardrails**: medium risk; add strict validation/limits first.
- **Defer**: high risk, unclear requirements, or dependency/security blockers.

---

## Phase 4: Development Steps

1. Implement plugin/method under `plugins/`.
2. Add/adjust allowlist in `config.py`.
3. Ensure permission checks still enforce module/class/method validation.
4. Add request example JSON under `jsons/`.
5. Add or update docs under `generated_data/docs/`.
6. Validate with local run (`python app.py`) and endpoint tests.

### Definition of Done

- Method is implemented and allowlisted.
- Input validation and error handling are explicit and safe.
- Response contract is stable and JSON-serializable.
- Example request JSON exists and runs successfully.
- Documentation includes usage notes and constraints.

---

## Phase 5: Communication Back to User

When capability is missing now:

- State current limitation plainly.
- Share temporary workaround (if available).
- Provide ETA or next checkpoint for toolset update.
- Confirm what will be delivered (method name, input/output shape).

When capability is delivered:

- Provide exact module/class/method now available.
- Provide one working request payload example.
- Provide expected success and error response examples.

---

## Agent-Readable Checklist

```yaml
capability_gap_workflow:
  - capture_request_record
  - validate_existing_allowlist
  - classify_gap_type
  - determine_workaround_or_escalation
  - draft_minimal_change_plan
  - implement_plugin_or_method
  - update_allowlist
  - add_examples_and_docs
  - verify_end_to_end
  - report_status_to_user
```

## Human Handoff Template

```markdown
Request ID: <REQ-ID>
Gap Type: <A|B|C|D>
Current Limitation: <one sentence>
Workaround Available: <yes/no + details>
Proposed Update: <plugin/method>
Risk Level: <low|medium|high>
Dependencies: <libraries/credentials/services>
ETA: <date or sprint>
Owner: <person/team>
Verification Plan: <tests + sample payloads>
```

---

## Example Outcome Statement

"Your request cannot be completed with current allowlisted methods. We documented it as `REQ-20260228-001`, confirmed a Type B gap (method missing in an existing plugin), and planned a minimal update: add `<PluginClass>.<new_method>` with strict input validation, allowlist update in `config.py`, sample JSON payload, and endpoint verification before release."