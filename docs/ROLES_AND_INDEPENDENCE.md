# Roles and Independence

## 1. Role Model

The system requires four responsibility boundaries:

1. Orchestration
2. Implementation
3. Independent Review
4. Runtime Verification

These responsibilities do not require four permanent agent processes.

Role separation exists to reduce self-confirmation, not to maximize agent count.

## 2. Orchestrator

Responsible for:

- understanding the request;
- repository reconnaissance coordination;
- extracting Acceptance Criteria;
- task complexity classification;
- planning;
- delegating work;
- managing information flow;
- choosing PASS / FIX / REPLAN;
- final status reporting.

The Orchestrator should not normally perform the main implementation for complex tasks.

For simple tasks, one agent may sequentially perform orchestration and implementation if independent review/verification is not justified.

## 3. Builder

Responsible for implementation.

Builder should:

- inspect relevant code;
- identify existing interfaces/dependencies;
- follow existing patterns;
- implement the requested behavior;
- fix bugs;
- perform necessary local refactoring only;
- add/update tests;
- run basic validation.

Builder output:

```text
Change summary
Changed files
Implementation rationale
Tests run
Known limitations / unverified areas
```

Builder cannot independently declare overall completion.

## 4. Critic

Critic's objective is not to prove the Builder correct.

Critic should actively search for:

- incomplete requirements;
- incorrect logic;
- edge cases;
- exception handling failures;
- inconsistent state;
- concurrency hazards;
- data integrity problems;
- API contract violations;
- frontend/backend mismatch;
- compatibility regressions;
- security issues;
- performance regressions;
- unnecessary complexity;
- insufficient tests;
- Mock/real behavior divergence;
- requirement misunderstanding.

Critic should normally not modify implementation code.

## 5. Critic Independence

The first Critic pass should preferably receive:

- original requirement;
- Acceptance Criteria;
- `git diff`;
- changed code;
- relevant surrounding/upstream/downstream code;
- tests and test output.

Builder rationale should not be the primary framing source for the first pass.

After the Critic forms an independent assessment, Builder rationale may be read to resolve uncertainty.

This rule is intended to reduce anchoring and confirmation bias.

## 6. Verifier

Verifier answers:

> Does the software actually behave as required?

Verifier should prefer executable evidence.

Possible interfaces include:

- unit tests;
- integration tests;
- regression tests;
- test databases;
- test APIs;
- mock systems;
- CLI;
- browser/UI;
- logs;
- network requests;
- database state;
- real input/output behavior.

Verifier normally does not modify implementation code.

## 7. Verifier Independence

Builder saying "tests pass" is not equivalent to independent verification.

Verifier should independently select or execute scenarios that map to Acceptance Criteria.

Where practical, the Verifier should reproduce critical paths without relying on Builder's narrative.

## 8. Scout / Recon Capability

Scout is an optional exploration capability.

Use Scout when:

- repository structure is unfamiliar;
- the task spans multiple modules;
- searching would pollute the main context;
- multiple independent areas can be explored in parallel;
- there is value in identifying an existing analogous implementation before planning.

Scout should not implement the main change.

Scout output should be factual and concise:

```text
Relevant files
Call/data flow
Existing pattern
Tests
Contracts
Risks
Unknowns
```

Scout is not a mandatory fifth role.

## 9. Role Separation Rules

Do not allow responsibility drift such as:

- Builder reviewing its own work and treating that as independent review;
- Critic silently editing the implementation;
- Verifier changing code to make a test pass;
- Orchestrator declaring PASS without examining evidence.

A single agent may perform multiple roles sequentially for low-risk tasks, but it must still preserve the conceptual boundaries and not treat self-assertion as independent evidence.
