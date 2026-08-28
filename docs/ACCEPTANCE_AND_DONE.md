# Acceptance Criteria and Definition of Done

## 1. Acceptance Criteria

Acceptance Criteria are required for non-trivial coding tasks.

They must be written before implementation.

Acceptance Criteria should be:

- observable;
- testable where practical;
- scoped to the user request;
- explicit about important error paths;
- explicit about persistence or user-visible behavior where relevant;
- compatible with existing contracts unless the requirement changes them.

## 2. Assumptions

A detail may be recorded as `Assumption` when it is reasonably implied by:

- existing repository behavior;
- existing API/data contract;
- nearby analogous implementation;
- explicit user constraints.

Do not convert a critical unknown into an assumption merely to avoid investigating it.

## 3. AC Traceability

Each Acceptance Criterion should eventually have a traceable evidence chain:

```text
Acceptance Criterion
  ↓
Implementation location
  ↓
Review result
  ↓
Verification evidence
  ↓
Final status
```

Recommended final table:

| AC | Implementation | Review | Verification | Status |
|---|---|---|---|---|
| AC1 | file/function | PASS / issue | command/scenario/evidence | PASS/FAIL |
| AC2 | file/function | PASS / issue | command/scenario/evidence | PASS/FAIL |

For large tasks, this table is strongly preferred.

## 4. Evidence Rules

A PASS should be based on evidence appropriate to the criterion.

Examples:

- output correctness → executed test or direct runtime output;
- API behavior → actual/request-level API verification;
- persistence → database state or integration test;
- UI behavior → browser/UI verification;
- regression → relevant regression test suite;
- compatibility → explicit contract comparison and tests.

Do not use "looks correct" as evidence when executable evidence is feasible.

## 5. Definition of Done

A task may be marked `DONE` only when all required conditions are satisfied:

- user-requested behavior is implemented;
- Acceptance Criteria are accounted for;
- Builder implementation is complete;
- required tests pass;
- no unresolved P0/P1 Critic findings remain;
- required Verifier checks pass;
- meaningful fixes were re-verified;
- unrelated user modifications were preserved;
- obvious regressions were checked;
- known limitations are explicitly recorded.

## 6. Non-DONE States

### PARTIALLY VERIFIED

Use when implementation exists but one or more important verification steps could not be completed.

State exactly:

- what is implemented;
- what was verified;
- what remains unverified;
- why it remains unverified.

### BLOCKED

Use when completion is prevented by an external or unresolved dependency.

State exactly:

- blocking condition;
- evidence for the block;
- what would be required to continue.

## 7. Final Report

The Orchestrator should report:

### Result

`DONE / PARTIALLY VERIFIED / BLOCKED`

### Implementation

- behavior implemented;
- key files changed;
- important design decisions.

### Review

- review status;
- P0/P1 findings;
- whether findings were fixed and re-reviewed.

### Verification

- commands/tests/scenarios executed;
- runtime/API/UI/database evidence;
- result.

### Acceptance Evidence

Prefer an AC traceability table for non-trivial work.

### Remaining Risks

Only list real remaining:

- unverified behavior;
- environment limitations;
- known technical risks;
- follow-up work.

Never describe "theoretically should work" as "verified".
