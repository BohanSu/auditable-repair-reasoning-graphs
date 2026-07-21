# Accepted DOT Graphs for the Locked 300/350 State

This directory contains exactly one repaired terminal DOT graph for every row
listed in `../results/STRICT_ACCEPTED_350.csv`. The set was selected from the
locked semantic-repair-stage accounting, not by taking an arbitrary 300-file
subset from the later 350/350 state. The remaining 50 benchmark rows are typed
residuals in this branch and therefore have no graph in this directory.

## Layout

```text
dot/<model>/<paper>.dot
manifest.csv
summary.json
```

`manifest.csv` binds each file to its paper-model key and records its SHA-256
digest, byte size, parsed node/edge counts, root, and validation flags.
`summary.json` records the set-level counts and hashes of the accounting and key
tables used to define this release.

## Verification

All 300 files were checked against the same acceptance boundary used by this
branch: the paper-model key is unique, final CG and REA are both 1.0, the row
has no provider or judge failure, the DOT is non-empty UTF-8, DOT parsing
succeeds, PEARL's strict graph-structure validator succeeds, and the copied
file has the same SHA-256 digest as the accepted terminal artifact.

The model counts are 61 Claude, 53 Gemini, 68 GPT-5.2, 59 Grok, and 59 Qwen,
which sum to the locked 300 strict rows.
