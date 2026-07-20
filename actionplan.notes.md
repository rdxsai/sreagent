# actionplan.notes — Phase 0 findings (verified against code)

Decisions (from user): Option A fully (dry-run remediation, real notify + approval + audit).
Surface: Slack is the primary target (user has workspace + tunnel). Still build the gate
first, provable WITHOUT any network surface (plan's build order), and keep the web fallback
as the dev surface. Slack secrets requested at Component 6, not before.

## Phase 0 answers

1. Slack: available (workspace + app-creation + public HTTPS tunnel per user). Secrets needed
   at C6: SLACK_BOT_TOKEN (chat:write), SLACK_SIGNING_SECRET, SLACK_CHANNEL_ID. Route must
   fail closed if SLACK_SIGNING_SECRET unset.
2. Handoff artifact: RcaResult (rca.py:26) = root_cause_service, synthesis{ranked_services,
   fault_type, justification}, graph, ranked_services, verdicts[{observed_signatures, evidence,
   ...}], trace_path, usage. GAP CONFIRMED: run.py does NOT persist result.json (only prints to
   stdout). Must add runs/oss/<run_id>.result.json write in C7; action CLI reads that file, not
   live objects (import isolation).
3. Env: add SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET, SLACK_CHANNEL_ID, ACTION_APPROVAL_TTL_S,
   ACTION_WEB_SECRET (web fallback header), SLACK_DRY_RUN. Update .env.example.
4. Deps: use httpx (already a dep). No slack_sdk.
5. Sandbox exclusion point: code_tool_specs() at client_gen.py:31. catalog.py:22 derives
   _SPECS from it, so ONE exclusion (effect != "read") covers sandbox SDK AND manager catalog.

## Seam verification (doc's "current state" table is accurate)
- ToolSpec (registry/__init__.py): frozen dataclass, no effect. Add `effect: str = "read"`
  defaulted -> no call-site changes to the 52 existing tools.
- @tool decorator builds ToolSpec; add effect param.
- /alert route at api/app.py:68, NO signature verification (as doc says). /slack/interact is new.
- ApprovalGuard: HookRunner.pre_tool_use deny mechanism (same as SealGuard/BudgetGuard).

## Two-path rule
sentinel/oss/ and sentinel/agent/ must NOT import sentinel/actions/. Add an import-isolation
test in C7. sentinel/actions/ consumes serialized result.json, never live RcaResult objects.

## Build order (plan, strict): C1 -> C2 -> C5 (+4 security tests) -> C3,C4 -> C6,C7.
Security tests exist before the first Slack call.
