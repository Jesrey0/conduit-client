# Data / Ledger Reentry Overlay Pattern

> Load this file **only** when the project involves supervised ledger / data reentry. The rules below protect the integrity of source data and the audit trail of changes; ignoring them is the most common way to silently corrupt a reentry workflow.

## Core rules

- **Do not infer ambiguous source lines** — ask the operator.
- **One source line may represent multiple economic / domain events** — split rows by meaning when confirmed.
- **Every row should carry source provenance when possible** — the source file, line, page, or transaction id that the row was derived from.
- **Use canonical IDs / names from project data** — do not invent new identifiers when the source has the canonical one.
- **Record source cash / amounts as authoritative** unless the operator explicitly confirms a correction.
- **Flag variance in notes** — do not silently hide it.
- **Derived calculations should come from project code**, not ad hoc formulas, unless used only for scratch verification.
- **Do not double-count source schedules and cashbook movements** — schedules often explain composition, while cashbook rows represent actual cash movement.
- **Keep separate pools separate** unless a documented transfer / injection exists.

## Standard sequence

1. **Check health and clean status.**

   ```bash
   ./conduit doctor
   await conduit.git.status()
   ```

2. **Inspect relevant entity / context remotely** using project code or CSV / JSON readers. Do not pull data into a local spreadsheet and then back — the source files are authoritative.

3. **Inspect existing derived state** if the project has generated statements or reports, but do not treat ignored / generated outputs as committed truth unless project policy says so.

4. **Edit source / raw files through Conduit** using `filesystem.edit` (for localized changes) or `filesystem.write` with `baseHash` (for whole-file rewrites). For multi-file reentries, use `filesystem.batch` with explicit `baseHash` per op. See `client/ops/OPS.md` § 6.

5. **Run project validation, integrity checks, and tests.** The project overlay (`client/ops/overlays/project.md` or its own `AGENTS.md`) lists the canonical commands; treat any deviation as a flag, not as an optimization.

6. **Inspect diff and commit only source / code / docs / test changes that are intended.** Never commit generated statements, intermediate spreadsheets, or scratch parsing scripts. If a generated statement must be regenerated and committed, do that as a separate, explicit commit with a message that makes the intent unambiguous.

## What the audit trail must show

A future auditor reading the commit log for a reentry session should be able to answer:

- which source files were touched and when;
- which rows were added, modified, or removed, and against which source line;
- which validation commands were run and what they returned;
- which canonical IDs / names were used and where they came from;
- which decisions were operator-confirmed vs. agent-inferred (and how the operator confirmed them).

If the reentry session generates an intermediate "staging" or "preview" output (a derived statement, balance check, or variance report), keep it under `scratch/<task>/` in the Arena sandbox during the current workflow and out of the remote repo. Discard it afterward unless the user requested it as a deliverable or project policy explicitly requires a reviewed, intentional commit.

## When to stop and ask

- A source line is ambiguous (could map to one event or several).
- A source amount disagrees with a previously-recorded derived total.
- A canonical ID is missing or conflicts with another row.
- A pool boundary is unclear (is this the same pool or a transfer?).
- The validation command fails in a way that the project overlay does not document.

In each case, write a short note into `scratch/<task>/` describing the ambiguity, capture the relevant project state, and ask the operator before proceeding. Do not guess and "fix it later" — guessing in a ledger reentry is how audit findings get created.
