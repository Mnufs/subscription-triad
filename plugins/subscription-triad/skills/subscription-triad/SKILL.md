---
name: subscription-triad
description: Run a subscription-only coding workflow in which the current Codex root plans and researches, Claude Fable 5 independently reviews the exact plan, Grok Build executes only an approved plan, and Codex verifies the result. Use when a user asks for Codex, Fable, and Grok to collaborate on a feature, requests a plan-review-build pipeline, wants official CLI subscription authentication instead of API billing, wants one temporary provider authorization per feature, or wants provider-context reuse with a persistent Grok execution session.
---

# Subscription Triad

Keep the current Codex task as the root orchestrator. The root owns intent, repository research, the canonical plan, review reconciliation, execution authorization, integration, verification, and the final answer. Do not delegate orchestration to Fable or Grok.

## Preconditions

1. Confirm the user asked for implementation before dispatching Grok. A request for advice, diagnosis, review, or planning does not authorize execution.
2. Prefer GPT-5.6 Sol at max reasoning as the root when the host exposes that route. Do not claim that route when the host does not confirm it.
3. Inspect the repository, create the run, and record the canonical plan before starting the provider session. Then run `doctor` inside that session before calling Fable or Grok.
4. Require first-party Claude Pro/Max login and Grok Build OAuth login. Never request, store, print, or configure API keys.
5. If `doctor` reports API environment variables in the parent process, disclose them. Provider subprocesses strip them mechanically, but the user should remove unrelated global keys when strict subscription-only operation is required.
6. Never add or modify a target `.codex/config.toml`, the user's `~/.codex/config.toml`, permission profiles, network allowlists, proxy rules, or persistent command approvals for this workflow.

Read [security-and-cache.md](references/security-and-cache.md) when authentication, billing, cache behavior, threat boundaries, session expiry, or provider failures matter.

## One-approval provider session protocol

The bundled MCP server performs local state operations only. After `record_plan`, call `start_provider_session`. It returns one structured `scoped_host_session` request bound to the run ID and target repository.

1. Tell the user that one temporary provider session is about to start for this feature. Explain that it covers readiness checks, Fable reviews, the approved Grok execution, and same-scope Grok continuations until the session closes or expires.
2. Execute exactly the returned `argv` from the returned `cwd` as a long-lived host process with writable stdin. Preserve every argument, retain the host process-session identifier, and wait for its `session_ready` JSON event.
3. Request host approval only for that session-start command. Do not request, suggest, or save a reusable prefix rule or persistent permission.
4. For `doctor`, `review_plan`, `dispatch_grok`, `continue_grok`, and `close_provider_session`, send the exact returned `stdin` JSON line to that same live process. Never execute those values as shell commands and never start a second provider process for the run while the first remains usable.
5. Match each response by `request_id`. Continue only when it reports `ok: true`; otherwise treat the action as a closed gate.
6. After final verification, a terminal provider failure, or a decision to stop, call `close_provider_session`, send its exact input, and let the process exit. An idle timeout, hard timeout, app restart, lost process session, or denied start requires a new one-time approval before provider work can resume.

If the host cannot keep a process session open and write subsequent stdin, stop and explain that the current Codex surface cannot provide one-approval orchestration without changing durable permissions. Do not fall back to per-action approvals, API keys, a global daemon, global network access, target-repository configuration, or a hidden sandbox escape.

## 1. Create a run

Inspect the real repository first. Collect:

- exact user intent and exclusions;
- observable acceptance criteria;
- relevant files, current behavior, constraints, and dirty-worktree facts;
- security, compatibility, concurrency, data, and verification risks.

Call `create_run` once with the project root, task, acceptance criteria, and compact verified context. Keep the returned `run_dir`; every later operation and the provider session must use it.

## 2. Produce the canonical Codex plan

Create a complete plan in the root task. Include:

- scoped files or components and ownership;
- ordered implementation steps;
- invariants and compatibility boundaries;
- acceptance criteria mapping;
- tests, compilation, behavioral checks, and diff review;
- rollback or failure handling where relevant.

Call `record_plan`. Its SHA-256 is the review approval identity. Any material plan change creates a new version and invalidates earlier Fable approval, while remaining inside the same feature authorization until execution starts.

## 3. Start the session and run Doctor

Call `start_provider_session` and follow the one-approval protocol. Call `doctor` with `run_dir`, send its exact JSON line to the live session, and stop before external model calls when Claude or Grok Build is unavailable. External agmsg is optional because the plugin has an embedded local transport fallback.

## 4. Run the Fable gate

Call `review_plan` only from the root and send its returned JSON line to the live provider session. The MCP request does not carry caller identity, so this boundary is instruction-enforced; the bridge itself still disables Fable tools, edits, permission prompts, and session persistence.

Handle the decision exactly:

- `PLAN_APPROVED`: the current plan hash is approved. Stop reviewing.
- `PLAN_REVISE`: resolve every material finding in the root, record the full revised plan, and review that new version through the same provider session.
- Provider, format, model-identity, authentication, state, or session error: treat review as unavailable, never as approval.

Never exceed five reviews. If review five does not approve the plan, close the provider session, halt before Grok, and show the latest plan plus unresolved findings to the user.

## 5. Dispatch Grok Build

Call `dispatch_grok` and send its returned JSON line to the live provider session only when all are true:

- the user authorized implementation;
- the run state is `approved`;
- the approved hash equals the current plan hash;
- the working scope still matches the reviewed context.

The provider session starts an official Grok Build headless process with OAuth forced, API and endpoint override variables removed, `--cwd` fixed to the target repository, the built-in `workspace` OS sandbox, a dedicated feature session ID, no cross-session memory, and the approved handoff artifact. If an external agmsg installation is available, its documented scripts carry lifecycle messages; otherwise the plugin uses its own project-local SQLite transport and never reads or mutates agmsg's database.

Poll with `run_status` without blocking the user for more than 60 seconds between updates. Keep the provider process session alive while polling. A Grok exit code of zero is only an execution handoff, not acceptance.

## 6. Verify as root and close

After `executed`:

1. Inspect the actual worktree and preserve unrelated user changes.
2. Compare the diff to the approved plan and acceptance criteria.
3. Run relevant tests or compilation independently.
4. Check security, compatibility, error handling, and regressions in proportion to risk.

If correction stays inside the approved plan, call `continue_grok` with a bounded instruction and send its returned one-time hash-bound JSON line to the same provider session. It resumes the same Grok session to reuse execution context. If correction changes scope, architecture, data contract, or acceptance criteria, close this session, create a new run, and repeat review; do not smuggle new scope through a continuation.

Call `record_verification`:

- `pass` only after independent evidence; this completes the run.
- `fail` with exact failures and required corrections; this permits a same-session continuation.

When no more provider work is needed, call `close_provider_session` and send its exact JSON line. Do not leave the temporary session waiting for its timeout.

## Cache and context policy

- Keep Codex planning and verification in one root task.
- Send Fable only the canonical packet. Its calls are fresh for independent review; keep the stable system prefix unchanged and accept that provider cache hits are not guaranteed or user-visible.
- Give Grok one dedicated session per feature and resume it for bounded corrections.
- Store large handoffs as run artifacts and exchange paths/status through agmsg instead of repeatedly embedding full transcripts.
- Do not optimize cache hit rate at the cost of independent review, stale-plan safety, or scope control.

## Final response

Report:

- run state and approved plan version/hash prefix;
- whether the provider session used one host authorization or had to restart;
- Fable decision and review count;
- Grok execution rounds and relevant artifacts;
- root verification commands and results;
- remaining risks or anything that prevented completion.

Do not describe a dispatched or executed run as complete until root verification passes.
