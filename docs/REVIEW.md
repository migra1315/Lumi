# Independent Code Review

## 1. Objective

Critic performs adversarial review.

The goal is to discover where the implementation can fail, not to confirm the Builder's reasoning.

## 2. Required Inputs

Prefer reviewing from:

- original request;
- Acceptance Criteria;
- `git diff`;
- changed code;
- relevant surrounding code;
- upstream/downstream contracts;
- tests;
- test results.

## 3. First-Pass Isolation

Where practical, do not show the Critic Builder rationale before the Critic forms its first independent assessment.

Reason:

- implementation explanations create anchoring;
- test claims can bias review;
- independent interpretation better detects requirement mismatch.

After the first-pass findings exist, Builder rationale may be consulted to resolve ambiguity.

## 4. Review Checklist

Inspect:

- requirement completeness;
- logic correctness;
- edge conditions;
- error handling;
- state consistency;
- concurrency;
- data integrity;
- API contract;
- frontend/backend consistency;
- compatibility;
- security;
- performance;
- complexity;
- test coverage;
- mock fidelity;
- regression risk;
- accidental unrelated changes.

## 5. Severity

### P0 — Blocking

Critical failure. Task cannot be accepted.

Examples:

- destructive data corruption;
- security-critical issue;
- core requirement completely broken;
- severe production failure.

### P1 — Major

Important functional, reliability, architectural, or compatibility problem that should be fixed before completion.

### P2 — Minor

Non-blocking issue that may be fixed depending on cost and scope.

## 6. Finding Format

Every finding should include:

```text
Severity
Location
Trigger condition
Problem
Impact
Recommended fix direction
```

Example:

```text
P1
Location: src/foo.py:120
Trigger: empty payload with existing record
Problem: update path clears persisted field
Impact: existing data can be lost
Fix direction: preserve field when key is absent; add regression test
```

## 7. Review Outcome

If no substantive issue is found:

`REVIEW PASS`

Do not invent low-value findings merely to prove review occurred.

## 8. Re-review

If Builder fixes a P0/P1 issue:

- review the affected area again;
- inspect whether the fix introduced a new regression;
- do not close the finding solely from Builder explanation.
