# Source and Artifact Boundary

This GitHub-ready package contains source-code snapshots and lightweight audit
tables. It does not include the full per-paper run artifacts.

## Included

- Python source snapshots under `code/`.
- Public summary JSON files and slim row-level CSV files under `results/`.
- Lightweight figures under `figures/`.
- Public documentation under `docs/`.

## Excluded

- Full per-paper graph directories.
- Full evaluator-output directories.
- Provider logs and raw provider responses.
- Local caches, temporary outputs, and Python bytecode.
- Machine-specific absolute paths.
- Credentials and local configuration files.

## Reproduction Boundary

The lightweight release is enough to inspect the reported counts, row-level
outcomes, controller decisions, merge-candidate metrics, and ANS guards.

Full regeneration requires restoring the external graph, evidence, and evaluator
artifacts expected by the scripts, or adapting the script arguments to point to
equivalent artifacts. Provider-backed scripts require credentials supplied
through environment variables outside the repository.
