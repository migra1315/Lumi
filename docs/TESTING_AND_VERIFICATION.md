# Testing and Verification

## 1. Principle

Verification answers whether the software actually satisfies the requirement.

Prefer runtime evidence over static confidence.

## 2. Bug Fix Rule — Reproduce Before Fix

For a bug fix, the preferred workflow is:

```text
REPRODUCE FAILURE
  ↓
CAPTURE FAILURE EVIDENCE
  ↓
IMPLEMENT FIX
  ↓
RE-RUN SAME REPRODUCTION
  ↓
REGRESSION CHECK
```

Before the fix, record when practical:

```text
Input / scenario
Expected result
Actual result
Relevant log/error
FAIL confirmed
```

After the fix:

```text
Same input / scenario
Expected result
Actual result
PASS confirmed
```

Do not claim a bug is fixed when the original failure was never reproduced unless reproduction is genuinely impossible. In that case, mark the limitation explicitly.

## 3. Test Integrity Rule

Tests must not be weakened merely to make the implementation pass.

Unless the requirement explicitly changes expected behavior, do not:

- delete a failing test because it exposes the implementation bug;
- loosen assertions solely to accept new incorrect output;
- skip or disable a failing test;
- blindly update snapshots;
- replace meaningful integration coverage with a Mock-only test;
- change expected values merely to match the new implementation.

If existing tests are wrong because the requirement changed, explain the contract change and update tests deliberately.

## 4. Verification Fidelity Ladder

Use the strongest practical evidence required by the Acceptance Criteria.

From weaker to stronger:

### L0 — Static Reasoning

Code inspection only.

Useful for local invariants, but not sufficient for behavior that can be executed.

### L1 — Unit Test

Validates isolated logic.

### L2 — Mock / Simulation

Validates interaction shape under simulated dependencies.

Mock success does not prove the real dependency behaves identically.

### L3 — Integration Test

Validates multiple real components together.

### L4 — Real Test Service / Test Database / Test API

Validates actual runtime integration against a controlled real environment.

### L5 — User-visible End-to-End

Validates behavior through the same surface the user or downstream consumer actually uses.

Examples:

- Browser → API → DB → UI refresh
- CLI → service → persistence → CLI output
- client request → backend → external test service → returned result

## 5. Fidelity Selection

The required level depends on the Acceptance Criterion.

Examples:

```text
"helper returns normalized value"
→ unit test may be sufficient.

"API persists a new record"
→ integration/API + database evidence preferred.

"admin user can delete and see it disappear"
→ browser/UI + API/database state preferred.

"external provider integration works"
→ Mock is not enough if a real test endpoint is available.
```

Do not promote L2 Mock success into L4/L5 verification.

## 6. Verifier Scenarios

Where relevant, cover:

1. normal path;
2. boundary conditions;
3. invalid input;
4. exception path;
5. state transition;
6. persistence;
7. frontend/backend interaction;
8. regression behavior;
9. user-visible behavior.

## 7. Failure Evidence

On failure, return:

```text
Test scenario
Input conditions
Expected result
Actual result
Relevant log/error
Reproduction steps
VERIFICATION FAIL
```

## 8. Pass Evidence

A verification pass should identify:

- scenario;
- command/tool used;
- relevant output;
- Acceptance Criterion mapped;
- environment used.

Return `VERIFICATION PASS` only after all required critical verification checks succeed.

## 9. Re-verification

After a meaningful implementation fix:

- rerun the exact failing scenario when possible;
- rerun affected regression tests;
- re-check impacted Acceptance Criteria.

A small diff is not grounds for skipping re-verification.
