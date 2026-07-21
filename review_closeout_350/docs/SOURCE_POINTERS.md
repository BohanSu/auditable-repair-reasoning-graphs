# Source and Artifact Boundary

This GitHub-ready package contains source-code snapshots, lightweight audit
tables, and the accepted terminal DOT graphs. It does not include the full
per-paper run directories or intermediate candidates.

## Included

- Shared Python source snapshot under `../../code_snapshot/`.
- Public summary JSON files and slim row-level CSV files under `results/`.
- All 350 accepted current-version terminal DOT graphs plus SHA-256 manifests
  under `graphs/`.
- Lightweight figures under `figures/`.
- Public documentation under `docs/`.

## Excluded

- Intermediate, rejected, and superseded per-paper graph candidates.
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
