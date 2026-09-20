# Contributing

## Ground rules

Implementation is the source of truth. A feature claim needs a code path and a
test; a quality claim needs a versioned dataset and committed result artifact.
Keep canonical source data separate from derived data, prefer deterministic
logic, and add model/agent complexity only for a measured failure.

## Development workflow

1. Create a branch from `main`.
2. Run `scripts/local.sh setup` once and `scripts/local.sh up` for development.
3. Make the smallest change that addresses the measured problem.
4. Add or update tests and, when behavior changes, the relevant frozen eval.
5. Update documentation in the same change.
6. Run the checks from the root README before opening a pull request.

Do not commit `.env`, model keys, database dumps, copyrighted source PDFs,
private media, or generated run directories that contain user content.

## Documentation rules

- Link important claims to code, a command, or a committed evaluation artifact.
- Label human review, model review, and synthetic data separately.
- Never turn a single-source result into a general benchmark claim.
- Record failed experiments when they affect an architecture decision.
- Keep diagrams aligned with executable graph nodes and worker stages.

## Pull requests

A useful pull request states the problem, why the chosen boundary is correct,
how it was verified, and any remaining limitation. Schema changes need a new
ordered migration; never rewrite an existing migration. Changes to parsing,
chunking, prompts, models, or evaluation rubrics need a version/provenance
change so old and new outputs cannot be confused.

## Before making the repository public

- Choose and add an explicit open-source license.
- Confirm that tracked source/evaluation excerpts are legally redistributable.
- Run secret and large-file scans over full Git history.
- Replace personal deployment/account references with neutral examples.
- Configure private vulnerability reporting and repository contact details.
- Add screenshots or a demo video made only from redistributable sources.
- Re-run CI from a clean clone and verify the setup instructions on a second
  machine.
