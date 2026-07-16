# Security, billing, and cache boundaries

## Provider routes

| Role | Allowed route | Rejected route |
|---|---|---|
| Codex root | Current Codex task authenticated with the user's ChatGPT subscription | A custom OpenAI API provider created by this plugin |
| Fable reviewer | Official `claude -p`, first-party Claude.ai Pro/Max authentication | `ANTHROPIC_API_KEY`, Bedrock, Vertex, Foundry, extracted OAuth tokens, third-party credential proxying |
| Grok executor | Official `grok` CLI with `--oauth` and the current Grok Build model (`grok-4.5`, with legacy `grok-build` fallback) | `XAI_API_KEY`, `api.x.ai`, OpenRouter, custom endpoint overrides |
| Handoff transport | Installed agmsg public scripts, or the plugin's separate project-local SQLite lifecycle store | Reading or mutating agmsg's private SQLite/config internals |

Provider subprocesses receive a sanitized environment. The sanitizer removes Anthropic and xAI API credentials plus endpoint/provider overrides. Claude authentication must additionally report `claude.ai`, `firstParty`, and a Pro or Max subscription. For Grok, every subprocess sets `GROK_DISABLE_API_KEY_AUTH=1`, verifies `loginPolicy.apiKeyAuthDisabled` through `grok inspect --json`, forces the OAuth flag, selects an advertised supported model (`grok-4.5` preferred, legacy `grok-build` accepted), and never constructs an `api.x.ai` request. Grok Build still does not expose an equally strong machine-readable subscription-plan identity.

Provider readiness also requires a fresh network-backed model listing. Cached Grok model output is rejected when the CLI reports a refresh or settings-network failure.

## Host permission boundary

The plugin does not change target-project or global Codex network configuration. Its MCP server performs local state operations and returns an argument-vector request for provider-dependent actions. The root then asks the Codex host to approve that exact `triad_provider.py` command once.

The bridge is deliberately narrow:

- it exposes only `doctor`, `review`, `dispatch`, and `continue`;
- it accepts provider payloads through validated run artifacts rather than arbitrary shell text;
- continuation instructions are written with mode `0600`, bound to a SHA-256 digest, constrained to the run's private request directory, and consumed once;
- it never requests a reusable command rule, background daemon, project network setting, global network setting, or unrestricted Python approval.

Disabling the plugin removes its tools and workflow from new tasks and leaves no network configuration behind. A Grok worker that the user already approved may finish its bounded execution round; it has a two-hour process timeout and does not become a general service.

This is a technical guardrail, not a promise that a provider will never change its terms, limits, CLI behavior, or enforcement. Users remain responsible for their accounts and current provider terms.

## Caller boundary

MCP tool calls do not identify whether the root or another model initiated them. The skill reserves review and dispatch tools for the Codex root, but the MCP server cannot mechanically prove caller identity. Provider MCP tools only prepare scoped requests; the Codex host still controls the one-command approval. Mechanical protections enforce provider authentication, no-tools Fable review, plan-hash freshness, state transitions, execution gating, and continuation-payload integrity.

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
- Scoped host execution unavailable or denied: stop without changing Codex configuration.
- Grok nonzero exit: record `execution_failed`; root must verify and decide whether a bounded continuation is appropriate.
- Verification failure: keep the run incomplete.

## Local data

Run artifacts live under `<project>/.subscription-triad/runs/<uuid>/`. They may contain task text, repository context, plans, reviews, provider output, and verification reports. When external agmsg is absent, compact lifecycle messages live under `<project>/.subscription-triad/transport/`. The default project `.gitignore` entry should exclude `.subscription-triad/`; review artifacts before sharing them manually.
