# Git and Change Control

## 1. Pre-change Inspection

Before implementation, inspect:

```bash
git status
git diff
```

Determine which modifications belong to:

- the current task;
- the user;
- another agent;
- unrelated in-progress work.

## 2. Preserve Existing Work

Do not overwrite or revert unrelated user changes without explicit instruction.

Do not assume every uncommitted modification belongs to the current task.

## 3. Minimum Necessary Change

Prefer the smallest change that correctly satisfies the Acceptance Criteria.

Avoid:

- opportunistic refactoring;
- broad formatting churn;
- unrelated dependency upgrades;
- file moves unrelated to the requirement;
- changing public interfaces without need.

## 4. Existing Patterns

Prefer the repository's established:

- architecture;
- module boundaries;
- naming;
- error handling;
- dependency management;
- API response format;
- tests;
- migration style.

When a nearby analogous implementation exists, treat it as a primary reference.

## 5. Compatibility

Preserve backward compatibility unless the requirement explicitly changes the contract.

Potential compatibility surfaces include:

- API request/response;
- database schema;
- serialized data;
- CLI arguments;
- configuration;
- environment variables;
- public functions;
- frontend/backend assumptions.

## 6. Multi-agent Modification Control

Parallel modification is allowed only when work areas are sufficiently independent.

Avoid multiple agents editing the same files or dependent contracts simultaneously unless coordination is explicit.

Use isolated worktrees/branches when available and useful.

## 7. Diff Review

Before final completion:

- inspect final `git diff`;
- confirm only intended files changed;
- confirm no accidental generated files/secrets/debug code remain;
- verify user modifications were preserved.
