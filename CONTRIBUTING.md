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
- Describe current repository behavior in neutral language; omit conversational
  references and personal plans. Reuse the existing guide for each flow, with
  short explanations, simple diagrams, and links to implementation.
- Update the established architecture, API, flow, interface, operations and
  evaluation guides in place. Describe code paths, configuration, evaluation
  methods, measurements and limits; do not append revision-history sections.
- Add a guide only for a distinct topic that existing guides cannot reasonably
  cover. Follow their structure: purpose, behavior, code references, setup or
  commands, verification and limits.
- Do not commit plans, delivery trackers, verification logs or per-round result
  pages. Fold an experiment's outcome into [evaluation](docs/evaluation.md)
  (what changed, what was measured, what was rejected) and commit its sanitized
  result JSON under `evaluation/`; private run bundles stay in ignored
  `evaluation/runs/`.

## Pull requests

A useful pull request states the problem, why the chosen boundary is correct,
how it was verified, and any remaining limitation. Schema changes need a new
ordered migration; never rewrite an existing migration. Changes to parsing,
chunking, prompts, models, or evaluation rubrics need a version/provenance
change so old and new outputs cannot be confused.
