# Security, billing, and cache boundaries

## Provider routes

| Role | Allowed route | Rejected route |
|---|---|---|
| Codex root | Current Codex task authenticated with the user's ChatGPT subscription | A custom OpenAI API provider created by this plugin |
| Fable reviewer | Official `claude -p`, first-party Claude.ai Pro/Max authentication | `ANTHROPIC_API_KEY`, Bedrock, Vertex, Foundry, extracted OAuth tokens, third-party credential proxying |
| Grok executor | Official `grok` CLI with `--oauth` and the `grok-build` model | `XAI_API_KEY`, `api.x.ai`, OpenRouter, custom endpoint overrides |
| Handoff transport | agmsg public scripts (`join.sh`, `send.sh`, `api.sh`) | Direct SQLite or private config-file mutation |

Provider subprocesses receive a sanitized environment. The sanitizer removes Anthropic and xAI API credentials plus endpoint/provider overrides. Claude authentication must additionally report `claude.ai`, `firstParty`, and a Pro or Max subscription. For Grok, every subprocess sets `GROK_DISABLE_API_KEY_AUTH=1`, verifies `loginPolicy.apiKeyAuthDisabled` through `grok inspect --json`, forces the OAuth flag, checks that the official CLI can list `grok-build`, and never constructs an `api.x.ai` request. Grok Build still does not expose an equally strong machine-readable subscription-plan identity.

This is a technical guardrail, not a promise that a provider will never change its terms, limits, CLI behavior, or enforcement. Users remain responsible for their accounts and current provider terms.

## Caller boundary

MCP tool calls do not identify whether the root or another model initiated them. The skill reserves review and dispatch tools for the Codex root, but the MCP server cannot mechanically prove caller identity. Mechanical protections still enforce provider authentication, no-tools Fable review, plan-hash freshness, state transitions, and execution gating.

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
- agmsg messages carry compact lifecycle signals and artifact paths.

Fresh Fable calls deliberately trade session reuse for independent review and tool isolation. Reusing Fable's conversation could improve continuity but also anchor later reviews to earlier mistakes. The gate favors review independence.

## Failure behavior

- Missing or invalid subscription authentication: stop.
- Fable response without `PLAN_APPROVED` or `PLAN_REVISE`: stop.
- Missing pinned Fable runtime identity or unexpected helper model: stop.
- Five reviews without approval: stop.
- Changed plan hash after approval: stop.
- agmsg unavailable: stop before Grok dispatch.
- Grok nonzero exit: record `execution_failed`; root must verify and decide whether a bounded continuation is appropriate.
- Verification failure: keep the run incomplete.

## Local data

Run artifacts live under `<project>/.subscription-triad/runs/<uuid>/`. They may contain task text, repository context, plans, reviews, provider output, and verification reports. The default project `.gitignore` entry should exclude `.subscription-triad/`; review artifacts before sharing them manually.
