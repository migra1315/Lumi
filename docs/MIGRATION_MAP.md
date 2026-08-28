# Migration Map from the Original Single-file Guideline

This file documents where the original rules were moved.

| Original section | New location |
|---|---|
| 1. 总体架构 | `ROLES_AND_INDEPENDENCE.md`, `AGENTS.md` |
| 2. 标准工作流 | `WORKFLOW.md` |
| 3. 失败回流规则 | `WORKFLOW.md`, `TESTING_AND_VERIFICATION.md` |
| 4. Agent 独立性 | `ROLES_AND_INDEPENDENCE.md`, `REVIEW.md` |
| 5. 工具使用原则 | `TOOLING_AND_EVIDENCE.md` |
| 6. Git / 修改范围控制 | `GIT_AND_CHANGE_CONTROL.md` |
| 7. 动态 Agent 策略 | `WORKFLOW.md`, `ROLES_AND_INDEPENDENCE.md` |
| 8. 并行原则 | `WORKFLOW.md`, `GIT_AND_CHANGE_CONTROL.md` |
| 9. Acceptance Criteria | `ACCEPTANCE_AND_DONE.md` |
| 10. Definition of Done | `ACCEPTANCE_AND_DONE.md`, `AGENTS.md` |
| 11. 最终报告格式 | `ACCEPTANCE_AND_DONE.md` |
| 12. 核心原则 | `AGENTS.md`, `ROLES_AND_INDEPENDENCE.md` |

## Newly Integrated Rules

The refactor also integrates these additions:

1. Roles are responsibility boundaries, not mandatory fixed agent processes.
2. Recon/Scout is an explicit pre-planning capability, not a mandatory fifth agent.
3. Bug fixes prefer reproduce-before-fix.
4. Critic first-pass review is isolated from Builder rationale where practical.
5. Test integrity prohibits weakening tests merely to make implementation pass.
6. Verification uses an explicit fidelity ladder from static reasoning to user-visible E2E.
7. Acceptance Criteria map to implementation, review, verification evidence, and status.
8. The root `AGENTS.md` is a compact navigation/critical-rules file; detailed knowledge lives under `docs/`.
