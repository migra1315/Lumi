# Software Engineering Workflow

## 1. Purpose

This document defines the default execution loop for non-trivial coding tasks.

The workflow optimizes for correctness, reproducibility, bounded change scope, and evidence-backed completion.

## 2. Default Pipeline

```text
REQUEST
  ↓
RECON
  ↓
ACCEPTANCE CRITERIA
  ↓
PLAN
  ↓
IMPLEMENT
  ↓
┌───────────────┐
↓               ↓
REVIEW        VERIFY
↓               ↓
└───────┬───────┘
        ↓
   QUALITY GATE
   ↙    ↓     ↘
 FIX   PASS   REPLAN
  ↓     ↓       ↓
IMPLEMENT DONE  RECON/PLAN
```

Review and verification may run in parallel only after implementation is sufficiently stable and only when neither depends on the other's output.

## 3. Phase 1 — REQUEST

The Orchestrator must identify:

- requested user-visible outcome;
- explicit constraints;
- relevant files/components if supplied;
- out-of-scope work;
- whether this is a feature, bug fix, refactor, migration, investigation, or verification task.

Do not silently transform the user's request into a different engineering objective.

## 4. Phase 2 — RECON

For non-trivial work, inspect the repository before implementation.

Recon should identify:

- entry points;
- affected call chain;
- relevant data flow;
- existing analogous implementations;
- tests and fixtures;
- API contracts;
- schema/persistence behavior;
- UI or CLI surfaces;
- external dependencies;
- configuration;
- likely regression surface;
- existing user modifications.

A Scout may be used for this phase.

Scout is not a mandatory permanent agent. It is an exploration capability that can be delegated when doing so reduces context load or wall-clock time.

Recommended Scout output:

```text
Relevant files
Existing pattern
Dependency / call graph
Tests
Contracts / invariants
Likely impact surface
Unknowns
Risks
```

## 5. Phase 3 — ACCEPTANCE CRITERIA

Before implementation, convert the request into verifiable Acceptance Criteria.

Good Acceptance Criteria describe observable behavior.

Example:

```text
AC1: valid input X produces Y.
AC2: invalid input returns the existing validation error format.
AC3: state is persisted correctly.
AC4: management UI shows the new state.
AC5: existing behavior Z still passes regression tests.
```

Reasonable derived details may be listed as `Assumption`.

If a critical ambiguity cannot be safely resolved from the repository, requirement, or existing behavior, do not invent it.

See `ACCEPTANCE_AND_DONE.md`.

## 6. Phase 4 — PLAN

The implementation plan should describe:

- files/modules likely to change;
- chosen existing pattern to follow;
- compatibility constraints;
- expected tests;
- verification approach;
- risk areas.

The plan should be proportional to task size.

Do not create speculative architecture for a local requirement.

## 7. Phase 5 — IMPLEMENT

Builder should:

- use existing architecture;
- follow local code conventions;
- make the Minimum Necessary Change;
- preserve backward compatibility unless explicitly changed;
- add or update tests where behavior changes;
- avoid unrelated cleanup;
- run basic tests before handoff.

Builder must report:

```text
What changed
Files changed
Why this approach
Tests executed
Known risks / unverified areas
```

Builder does not decide final task completion.

## 8. Phase 6A — REVIEW

Critic independently evaluates correctness and regression risk.

The first-pass review should preferably inspect requirement + AC + diff + code + tests before reading Builder rationale.

This reduces anchoring and confirmation bias.

See `REVIEW.md`.

## 9. Phase 6B — VERIFY

Verifier determines whether the software actually satisfies the Acceptance Criteria.

Prefer black-box or near-real behavior when feasible.

Verifier should not merely repeat Builder tests.

See `TESTING_AND_VERIFICATION.md`.

## 10. Phase 7 — QUALITY GATE

The Orchestrator evaluates:

- Acceptance Criteria coverage;
- Critic findings;
- Verifier evidence;
- test results;
- unresolved risks;
- change scope;
- regression status.

Possible decisions:

### PASS

All blocking conditions are satisfied.

### FIX

Implementation is directionally correct but requires specific corrections.

Typical path:

```text
Critic P1
  ↓
Orchestrator
  ↓
Builder fixes
  ↓
Affected Review + Verification repeated
```

or

```text
Verification failure
  ↓
Orchestrator
  ↓
Builder reproduces + fixes
  ↓
Verifier re-runs the same failing scenario
```

### REPLAN

Use REPLAN when the failure invalidates the current implementation approach, requirement interpretation, dependency assumption, or architecture choice.

Do not mechanically retry the same approach when evidence shows the plan is wrong.

## 11. Failure Return Rules

Any failed quality gate prevents DONE.

A meaningful fix must be re-verified in the affected scope.

Do not skip verification because:

- the diff is small;
- the fix looks obvious;
- the code compiles;
- a unit test passes;
- the Builder believes the problem is solved.

## 12. Dynamic Workflow Depth

### Simple task

Examples:

- typo;
- obvious one-line bug;
- local configuration correction;
- low-risk textual change.

Possible workflow:

```text
Orchestrator → Builder → Basic Test
```

### Medium task

Examples:

- single-module feature;
- API behavior change;
- data transformation;
- moderate regression risk.

Recommended workflow:

```text
Orchestrator → Recon → AC → Builder → Critic → Tests
```

### Complex / high-risk task

Examples:

- cross-module change;
- frontend/backend coordination;
- database change;
- new API;
- state machine;
- concurrency;
- external integration;
- user-requested full validation.

Recommended workflow:

```text
Orchestrator
  ↓
Recon / Scout
  ↓
AC + Plan
  ↓
Builder
  ↓
Critic + Verifier
  ↓
Fix / Replan Loop
  ↓
Quality Gate
```

## 13. Parallelism

Parallelize only independent work.

Good candidates:

- repository exploration of unrelated subsystems;
- independent review and runtime verification after implementation;
- separate analysis of frontend and backend impact when their outputs do not conflict.

Do not parallelize tasks with a strong dependency chain merely to increase agent count.

The objective is lower wall-clock time, not more agent messages.
