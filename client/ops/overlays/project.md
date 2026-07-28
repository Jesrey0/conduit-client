# Project-Specific Overlay Pattern

> Load this file when starting substantive work on a new / unfamiliar project, or when the project has its own rules that diverge from the defaults in `client/ops/OPS.md`. Generic Conduit operating guidance does **not** replace project-specific rules — durable rules live with the project, while task-specific notes remain disposable in the current agent workflow.

## Discover project-specific guidance

Before making substantive changes, inspect:

```text
README.md
AGENTS.md
GEMINI.md
CLAUDE.md
CONTRIBUTING.md
docs/
```

Read them in that order. `README.md` and `CONTRIBUTING.md` set the high-level expectations; `AGENTS.md` / `GEMINI.md` / `CLAUDE.md` capture model-specific guidance if the project supplies it; `docs/` is the deep reference.

## Capture project-specific constraints for the current workflow

For the current task, capture relevant project rules in a disposable workflow
artifact such as `scratch/<task>/project-context.md`. This note exists only to
execute the current workflow safely and is discarded afterward. If guidance
should outlive the task, deliberately promote it into the project's `AGENTS.md`,
README, runbook, or executable project automation after review. At minimum,
record:

- **validation commands** — e.g. `npm test`, `pytest -q`, `cargo test`, `make validate`; note any required venv, interpreter, or env vars;
- **package-manager and lockfile policy** — identify the project-approved clean-install command and whether lockfile changes are intentional/tracked; never discard or regenerate a lockfile merely to make installation pass;
- **generated artifact policy** — confirm whether `build/`, `dist/`, `coverage/`, `node_modules/`, etc. are tracked or gitignored, and whether the project policy differs from the default in `client/ops/OPS.md` § 9;
- **data mutation policy** — read-only / evidence-only files, files that must never be edited by an agent, files that need optimistic concurrency checks beyond the Conduit defaults;
- **commit message conventions** — `feat:` / `fix:` / `refactor:` prefixes, scope tags, max line length, signed-off-by requirements;
- **domain-specific invariants** — e.g. "every row must carry a source provenance field" in a ledger project, "no module may import from `legacy/`" in a refactor project, "all CLI flags must be in `--kebab-case`" in a CLI project;
- **files that are read-only / evidence-only** — never edit these unless the user explicitly asks and the project policy allows it;
- **required test suites before commit** — beyond the obvious test runner, are there mutation tests, contract tests, integration tests, or security tests that must pass?
- **tooling / self-hosting caveats** — is this project itself the tool you are using to edit it (as with `conduit-local`)? If so, note that live tool behavior may lag source edits until rebuild/restart.
- **doc / generated-baseline drift** — if the change adds tests, commands, generated artifacts, or architecture guards, search docs for counts/baselines/claims that must be updated.
- **lexical or architecture guardrails** — record any project tests that ban strings, imports, paths, generated files, or architectural patterns so new names/descriptions do not trip them accidentally.

## Standard sequence for a project-specific change

1. **Read the project overlays first** — at least `README.md`, `AGENTS.md` (if present), and the relevant `docs/` subdirectory.
2. **Capture a task-scoped project note** in `scratch/<task>/project-context.md` with the items above. Discard it after the task; promote durable guidance into the project only through explicit review.
3. **Verify the project-specific validation commands actually exist and run** — don't assume the project's docs are correct; run them in a non-mutating way first (e.g. `--listTests` for pytest, `npm test -- --listTests` for Jest).
4. **Check for project-specific ignored files** with `await conduit.terminal.exec("git status --ignored")` so you know what's "normal noise" before making your diff.
5. **Make changes** following `client/ops/OPS.md` § 6 (Safe Mutation) and § 7 (Validation).
6. **Diff-review against the project overlay** — does the project require additional commit-msg tags? A changelog entry? A docs update? A migration file? If tests were added/removed, search docs for test-count baselines and claim markers.
7. **Cross-check suspect tool results** — when changing a tool family you also rely on for inspection (filesystem/search/git/etc.), verify critical observations with native project commands through `terminal.exec`.
8. **Commit using the project's commit conventions**, not your default. The end-of-task summary in `client/ops/OPS.md` § 11 should reflect any project-specific fields.

## Broad path/refactor and generated-artifact checklist

Use this project overlay — not the core `OPS.md` — for repository-specific
large refactors such as path renames, generated bundle regeneration, or
project-owned artifact layout changes. Before committing:

1. Confirm clean `git.status` and capture the current branch/log.
2. For path renames, prefer semantic `filesystem.move` or a reviewed native
   `git mv`; then run `git grep <old-path>` and update every live reference.
3. For case-only renames, account for `core.ignorecase=true` with a two-step
   rename or `git -c core.ignorecase=false ...` verification.
4. For generated artifacts, edit the source/generator first, run the documented
   `--write`/generation command, then run the corresponding `--check` target.
5. Review `git diff --name-status` so generated outputs, delivery archives, and
   source changes are intentionally grouped.
6. Run the project-specific validation suite and report any generated artifacts
   regenerated but not committed.

## When the project's overlay conflicts with `client/ops/OPS.md`

The project's overlay wins for everything project-specific. `client/ops/OPS.md` is the default for everything not explicitly overridden. Concretely:

- Project says "use a different validation command"? Use that one. The `OPS.md` § 7 example is illustrative, not mandatory.
- Project says "commit generated artifacts in `dist/`"? Commit them, despite the default rule in `OPS.md` § 9.
- Project says "always run the test suite twice before commit"? Do that.

The `client/ops/OPS.md` guidance still applies for anything the project overlay doesn't speak to: ephemeral workflow discipline, mutation hierarchy, the three-environments mental model, the JSON orchestration layer, etc.
