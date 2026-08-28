# Agent Engineering Guide

This repository uses an evidence-driven software engineering workflow.

Goal: make the smallest correct change, independently challenge it, verify it with executable evidence, and only then declare completion.

## Start

For non-trivial work:

1. Inspect `git status` and `git diff`.
2. Read relevant `docs/` guidance.
3. Inspect existing code, tests, interfaces, and analogous implementations.
4. Extract Acceptance Criteria before implementation.
5. Do not invent repository conventions or missing product behavior.

## Workflow

Default:

`REQUEST → RECON → AC → PLAN → IMPLEMENT → REVIEW / VERIFY → QUALITY GATE → DONE`

Bug fix:

`REPRODUCE → CAPTURE FAILURE → FIX → RE-RUN SAME CASE → REGRESSION CHECK`

See `docs/WORKFLOW.md` and `docs/TESTING_AND_VERIFICATION.md`.

## Responsibilities

Required responsibility boundaries:

- Orchestrator: understands, plans, delegates, gates.
- Builder: implements.
- Critic: independently challenges.
- Verifier: proves behavior through execution.

These are roles, not mandatory permanent agent processes.

Use separate agents/passes when independence or parallelism materially improves quality. A Scout/Recon subagent is optional, not a mandatory fifth role.

See `docs/ROLES_AND_INDEPENDENCE.md`.

## Recon

Before a non-trivial change identify:

- entry points and affected modules;
- analogous implementations;
- tests and fixtures;
- API/data/persistence contracts;
- user-visible surfaces;
- runtime dependencies;
- regression scope;
- existing user modifications.

## Non-negotiable Rules

Never:

- overwrite unrelated user work;
- weaken tests merely to make code pass;
- claim verification that was not performed;
- treat Builder self-test claims as independent verification;
- mark DONE with unresolved P0/P1 findings;
- skip affected re-verification after a meaningful fix;
- treat Mock success as proof of real integration when stronger evidence is required and available;
- perform unrelated broad refactors;
- invent API, database, domain, or product rules.

## Change Policy

Prefer **Minimum Necessary Change**.

Follow existing architecture, style, abstractions, contracts, tests, and compatibility behavior unless the requirement explicitly changes them.

See `docs/GIT_AND_CHANGE_CONTROL.md`.

## Acceptance Evidence

Every non-trivial task requires Acceptance Criteria before implementation.

Each AC should map to:

`AC → implementation → review → verification evidence → status`

See `docs/ACCEPTANCE_AND_DONE.md`.

## Independent Review

Critic should actively search for failure.

First-pass review should prefer:

- original requirement;
- Acceptance Criteria;
- diff;
- changed and related code;
- tests/results.

Avoid anchoring the first pass on Builder rationale.

See `docs/REVIEW.md`.

## Verification

Prefer executable evidence.

Evidence strength:

`Static < Unit < Mock < Integration < Real test service/data < User-visible E2E`

Use the highest practical level required by the Acceptance Criteria.

See `docs/TESTING_AND_VERIFICATION.md`.

## Tools

When available, use shell, git, tests, browser, API tools, test databases, logs, network inspection, and project-specific tools.

- If it can run, do not only read it.
- If it can be verified, do not only infer it.
- If it can be reproduced, do not only assume it.

See `docs/TOOLING_AND_EVIDENCE.md`.

## Project Knowledge

Project-specific facts belong in:

- `docs/ARCHITECTURE.md`
- `docs/API_CONTRACTS.md`
- `docs/DATABASE.md`
- `docs/DOMAIN_RULES.md`
- `docs/COMMANDS.md`

If these contain placeholders, inspect the repository instead of inventing facts.

## Done

Return `DONE` only when:

- requested behavior is implemented;
- ACs are accounted for;
- required tests pass;
- no unresolved P0/P1 findings remain;
- required verification passes;
- fixes were re-verified;
- unrelated user work is preserved;
- obvious regressions were checked;
- remaining limitations are recorded.

Otherwise return `PARTIALLY VERIFIED` or `BLOCKED`.

See `docs/ACCEPTANCE_AND_DONE.md`.
