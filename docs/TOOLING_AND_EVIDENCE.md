# Tooling and Evidence

## 1. Tool-first Principle

When the environment provides executable tools, use them to obtain evidence.

Possible tools:

- shell;
- git;
- test runner;
- linter/type checker;
- browser;
- API client;
- mock server;
- test database;
- logs;
- network inspection;
- repository search;
- project-specific MCP/tools.

## 2. Evidence Preference

Prefer:

```text
actual execution > runtime observation > test result > code inspection > assumption
```

Use code reasoning when execution is unavailable or disproportionate, but label the verification limitation.

## 3. Command Evidence

For important commands, preserve:

- command;
- exit code;
- relevant output;
- failing test names;
- concise error excerpt.

Avoid flooding the main orchestration context with thousands of log lines.

## 4. Noisy Operations

When possible, delegate noisy searches/log analysis/test runs to a narrow subagent or save full output to a file.

Return only:

```text
Command
Exit code
Relevant failures
Relevant excerpt
Conclusion
```

## 5. Environment Limits

If runtime verification is blocked by:

- missing credentials;
- unavailable external service;
- missing test data;
- platform restriction;
- inaccessible browser/UI;
- unavailable database;

do not simulate successful verification.

Record the limitation and downgrade final status when it affects critical Acceptance Criteria.
