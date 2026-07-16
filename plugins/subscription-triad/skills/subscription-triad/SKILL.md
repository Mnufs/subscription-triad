---
name: subscription-triad
description: Run a subscription-only coding workflow in which the current Codex root plans and researches, Claude Fable 5 independently reviews the exact plan, Grok Build executes only an approved plan, and Codex verifies the result. Use when a user asks for Codex, Fable, and Grok to collaborate on a feature, requests a plan-review-build pipeline, wants official CLI subscription authentication instead of API billing, or wants provider-context reuse with a persistent Grok execution session.
---

# Subscription Triad

Keep the current Codex task as the root orchestrator. The root owns intent, repository research, the canonical plan, review reconciliation, execution authorization, integration, verification, and the final answer. Do not delegate orchestration to Fable or Grok.

## Preconditions

1. Confirm the user asked for implementation before dispatching Grok. A request for advice, diagnosis, review, or planning does not authorize execution.
2. Prefer GPT-5.6 Sol at max reasoning as the root when the host exposes that route. Do not claim that route when the host does not confirm it.
3. Call `doctor` with the project root, then follow the scoped provider execution protocol below. Stop before model calls when Claude or Grok Build is unavailable. External agmsg is optional because the plugin has an embedded local transport fallback.
4. Require first-party Claude Pro/Max login and Grok Build OAuth login. Never request, store, print, or configure API keys.
5. If `doctor` reports API environment variables in the parent process, disclose them. Provider subprocesses strip them mechanically, but the user should remove unrelated global keys when strict subscription-only operation is required.
6. Never add or modify a target `.codex/config.toml`, the user's `~/.codex/config.toml`, permission profiles, network allowlists, proxy rules, or persistent command approvals for this workflow.

Read [security-and-cache.md](references/security-and-cache.md) when authentication, billing, cache behavior, threat boundaries, or provider failures matter.

## Scoped provider execution protocol

The bundled MCP server performs local state operations only. `doctor`, `review_plan`, `dispatch_grok`, and `continue_grok` return a structured `scoped_host_execution` request instead of contacting a provider from the MCP sandbox.

For each returned request:

1. Tell the user which provider action is about to run and why it needs the host's one-command approval.
2. Execute exactly the returned `argv`, from the returned `cwd`, using the host's per-command sandbox/network approval mechanism. Preserve each argument as a distinct shell-quoted value; do not add a shell wrapper, environment override, alternate binary, or extra argument.
3. Request approval for that command once. Do not request, suggest, or save a reusable prefix rule or persistent permission.
4. Parse the bridge's JSON output. Continue only when it reports `ok: true`; otherwise treat its error as a closed gate.

If the host cannot provide a one-command approval, stop and explain that the current Codex surface cannot run subscription providers without changing durable permissions. Do not fall back to API keys, a daemon, global network access, target-repository configuration, or a hidden sandbox escape.

## 1. Create a run

Inspect the real repository first. Collect:

- exact user intent and exclusions;
- observable acceptance criteria;
- relevant files, current behavior, constraints, and dirty-worktree facts;
- security, compatibility, concurrency, data, and verification risks.

Call `create_run` once with the project root, task, acceptance criteria, and compact verified context. Keep the returned `run_dir`; every later operation must use it.

## 2. Produce the canonical Codex plan

Create a complete plan in the root task. Include:

- scoped files or components and ownership;
- ordered implementation steps;
- invariants and compatibility boundaries;
- acceptance criteria mapping;
- tests, compilation, behavioral checks, and diff review;
- rollback or failure handling where relevant.

Call `record_plan`. Its SHA-256 is the approval identity. Any material plan change creates a new version and invalidates earlier approval.

## 3. Run the Fable gate

Call `review_plan` only from the root, then execute its returned scoped provider request. The MCP request does not carry caller identity, so this boundary is instruction-enforced; the bridge itself still disables tools, edits, permission prompts, and session persistence.

Handle the decision exactly:

- `PLAN_APPROVED`: the current plan hash is approved. Stop reviewing.
- `PLAN_REVISE`: resolve every material finding in the root, record the full revised plan, and review that new version.
- Provider, format, model-identity, authentication, or state error: treat review as unavailable, never as approval.

Never exceed five reviews. If review five does not approve the plan, halt before Grok and show the latest plan plus unresolved findings to the user.

## 4. Dispatch Grok Build

Call `dispatch_grok` and execute its returned scoped provider request only when all are true:

- the user authorized implementation;
- the run state is `approved`;
- the approved hash equals the current plan hash;
- the working scope still matches the reviewed context.

The provider bridge starts an official Grok Build headless process with OAuth forced, API and endpoint override variables removed, a dedicated feature session ID, no cross-session memory, and the approved handoff artifact. If an external agmsg installation is available, its documented scripts carry lifecycle messages; otherwise the plugin uses its own project-local SQLite transport and never reads or mutates agmsg's database.

Poll with `run_status` without blocking the user for more than 60 seconds between updates. A Grok exit code of zero is only an execution handoff, not acceptance.

## 5. Verify as root

After `executed`:

1. Inspect the actual worktree and preserve unrelated user changes.
2. Compare the diff to the approved plan and acceptance criteria.
3. Run relevant tests or compilation independently.
4. Check security, compatibility, error handling, and regressions in proportion to risk.

If correction stays inside the approved plan, call `continue_grok` with a bounded instruction, then execute the returned one-time hash-bound provider request. It resumes the same Grok session to reuse execution context. If correction changes scope, architecture, data contract, or acceptance criteria, create a new run and repeat review; do not smuggle new scope through a continuation.

Call `record_verification`:

- `pass` only after independent evidence; this completes the run.
- `fail` with exact failures and required corrections; this permits a same-session continuation.

## Cache and context policy

- Keep Codex planning and verification in one root task.
- Send Fable only the canonical packet. Its calls are fresh for independent review; keep the stable system prefix unchanged and accept that provider cache hits are not guaranteed or user-visible.
- Give Grok one dedicated session per feature and resume it for bounded corrections.
- Store large handoffs as run artifacts and exchange paths/status through agmsg instead of repeatedly embedding full transcripts.
- Do not optimize cache hit rate at the cost of independent review, stale-plan safety, or scope control.

## Final response

Report:

- run state and approved plan version/hash prefix;
- Fable decision and review count;
- Grok execution rounds and relevant artifacts;
- root verification commands and results;
- remaining risks or anything that prevented completion.

Do not describe a dispatched or executed run as complete until root verification passes.
