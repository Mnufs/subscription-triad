# Security, billing, and cache boundaries

## Provider routes

| Role | Allowed route | Rejected route |
|---|---|---|
| Codex root | Current Codex task authenticated with the user's ChatGPT subscription | A custom OpenAI API provider created by this plugin |
| Fable reviewer | Official `claude -p`, first-party Claude.ai Pro/Max authentication | `ANTHROPIC_API_KEY`, Bedrock, Vertex, Foundry, extracted OAuth tokens, third-party credential proxying |
| Grok executor | Official `grok` CLI with `--oauth` and the current Grok Build model (`grok-4.5`, with legacy `grok-build` fallback) | `XAI_API_KEY`, `api.x.ai`, OpenRouter, custom endpoint overrides |
| Handoff transport | Installed agmsg public scripts, or the plugin's separate project-local SQLite lifecycle store | Reading or mutating agmsg's private SQLite/config internals |

Provider subprocesses receive a sanitized environment. The sanitizer removes Anthropic and xAI API credentials plus endpoint/provider overrides. Claude authentication must additionally report `claude.ai`, `firstParty`, and a Pro or Max subscription. For Grok, every subprocess sets `GROK_DISABLE_API_KEY_AUTH=1`, verifies `loginPolicy.apiKeyAuthDisabled` through `grok inspect --json`, forces the OAuth flag, fixes `--cwd` to the target repository, enables Grok's built-in `workspace` OS sandbox, selects an advertised supported model (`grok-4.5` preferred, legacy `grok-build` accepted), and never constructs an `api.x.ai` request. The workspace profile limits code writes to the target repository while retaining Grok state and OS temporary-directory writes. Grok Build still does not expose an equally strong machine-readable subscription-plan identity.

Provider readiness also requires a fresh network-backed model listing. Cached Grok model output is rejected when the CLI reports a refresh or settings-network failure.

## Host permission boundary

The plugin does not change target-project or global Codex network configuration. Its MCP server performs local state operations and returns one argument-vector request that starts `triad_provider.py session` for a specific run and target repository. The root asks the Codex host to approve that one temporary feature session, then sends later provider actions as bounded JSON lines over the already-approved process stdin.

The bridge is deliberately narrow:

- its command line exposes only `session --run <canonical-run-dir>`;
- the live protocol accepts only `doctor`, `review`, `dispatch`, `continue`, and `close` for that run;
- it accepts provider payloads through validated run artifacts rather than arbitrary shell text;
- continuation instructions are written with mode `0600`, bound to a SHA-256 digest, constrained to the run's private request directory, and consumed once;
- it holds an exclusive mode-`0600` lease with a short heartbeat so a Grok worker stops when the approved bridge disappears;
- it closes after 30 minutes idle or four hours total, and the root closes it immediately after final verification;
- it never requests a reusable command rule, persistent daemon, project network setting, global network setting, or unrestricted Python approval.

Disabling the plugin removes its tools and workflow from new tasks and leaves no network configuration behind. A live feature session remains bounded by its lease and hard timeout; loss of its heartbeat terminates its Grok worker. Grok also keeps its independent two-hour execution timeout and does not become a general service.

"One feature, one authorization" means one uninterrupted provider process session. A new feature/run, an expired or closed session, a Codex/app restart, or a lost process-session handle requires a new single authorization. Existing user-selected host behavior such as automatic approval may review the start request, but the plugin never changes that behavior.

This is a technical guardrail, not a promise that a provider will never change its terms, limits, CLI behavior, or enforcement. Users remain responsible for their accounts and current provider terms.

## Caller boundary

MCP tool calls do not identify whether the root or another model initiated them. The skill reserves review and dispatch tools for the Codex root, but the MCP server cannot mechanically prove caller identity. Provider MCP tools only prepare the session-start request or exact session input; the Codex host still controls the single feature-session approval. Mechanical protections enforce run binding, exclusive leasing, provider authentication, no-tools Fable review, plan-hash freshness, state transitions, execution gating, and continuation-payload integrity.

## Approval integrity

The canonical plan is normalized and hashed with SHA-256. Fable approval stores that exact digest. Dispatch requires:

1. state `approved`;
2. a non-empty approved digest;
3. equality between approved and current plan digests.

Changing the plan invalidates approval. Once execution starts, plan mutation is rejected; new scope requires a new run.

## What cache reuse means here

Provider prompt caches typically match a stable prefix, not the semantic idea of "the same feature." Cache policies and hit metrics are provider-controlled and may not be exposed under subscriptions.

Subscription Triad optimizes the controllable parts:

- stable Fable system instructions precede the changing review packet;
- the root sends only canonical artifacts, not the whole Codex transcript;
- one Grok session UUID is created per feature;
- bounded fixes use `--resume` with that same session;
- Grok cross-session memory is disabled to avoid unrelated-project contamination;
- agmsg or the embedded transport carries compact lifecycle signals and artifact paths.

Fresh Fable calls deliberately trade session reuse for independent review and tool isolation. Reusing Fable's conversation could improve continuity but also anchor later reviews to earlier mistakes. The gate favors review independence.

## Failure behavior

- Missing or invalid subscription authentication: stop.
- Fable response without `PLAN_APPROVED` or `PLAN_REVISE`: stop.
- Missing pinned Fable runtime identity or unexpected helper model: stop.
- Five reviews without approval: stop.
- Changed plan hash after approval: stop.
- External agmsg unavailable: use the embedded project-local lifecycle transport.
- Long-lived host process stdin unavailable, session start denied, or session lost: stop without changing Codex configuration; a fresh session requires a fresh one-time approval.
- Grok nonzero exit: record `execution_failed`; root must verify and decide whether a bounded continuation is appropriate.
- Verification failure: keep the run incomplete.

## Local data

Run artifacts live under `<project>/.subscription-triad/runs/<uuid>/`. They may contain task text, repository context, plans, reviews, provider output, and verification reports. When external agmsg is absent, compact lifecycle messages live under `<project>/.subscription-triad/transport/`. The default project `.gitignore` entry should exclude `.subscription-triad/`; review artifacts before sharing them manually.
