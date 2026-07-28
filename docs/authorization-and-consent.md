# Authorization and consent

A Conduit provisioning envelope is purpose-bound authorization to contact only its declared server, verify only its declared bootstrap, and submit exactly one enrollment request. Access is issued only after separate local approval.

## Authorization classes

- `LIVE_PROBATION` — expiring least-privilege probation.
- `REGULAR_OPERATOR_PROMOTION` — credential-rotating promotion to the exact active grant in the envelope.

The generated `CONDUIT_OPERATOR_CONFIRMATION.md` is ready to paste into chat when a conservative agent requires explicit controlling-operator confirmation in addition to the package.

## Time

All timestamps are ISO-8601 absolute instants. `Z` means UTC. Compare parsed timezone-aware instants; never compare calendar-date text. If current time cannot be established, stop rather than guessing.

## Boundaries

The package does not authorize undeclared privileges or workspaces. Secrets must never be printed, uploaded, committed, or copied into a Conduit workspace. Pending enrollment is resumable through the dedicated local mode-`0600` enrollment record; the invite is never reused.
