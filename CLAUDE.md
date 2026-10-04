# SEO Agent — notes for Claude

Deterministic SEO decision engine; see README.md for layout and
docs/roadmap.md for status. Production runbook: docs/deploy-runbook.md.

- Run unit tests with `pytest -q -m "not integration"`.
- Never put credentials in chat, code, logs or command lines. Customers'
  site credentials go through the API's encrypted `secrets` field.

## Review workflow (mandatory)

When a task is complete (not after every single edit), and before any commit:

1. Run the test suite. Fix failures first.
2. Delegate to the `reviewer` subagent to review the current diff.
3. **BLOCKER** findings: verify each one against the code. If it is valid, fix it.
   If you believe a finding is wrong, do NOT change the code; explain to the user why.
4. After fixing, run the tests and the `reviewer` again: **one fix round only**.
   If blockers remain after that, stop and report to the user instead of looping.
5. **SHOULD** findings: list them for the user and ask before changing anything.
6. **NIT** findings: list them only.
7. Never commit while a BLOCKER is open.
