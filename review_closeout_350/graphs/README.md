# Accepted DOT Graphs for the Current 350/350 State

This directory contains exactly one repaired terminal DOT graph for each row in
`../results/REVIEW_FULL_350_ACCOUNTING.csv`. All 350 rows satisfy the current-
version strict gate: final CG and REA are both 1.0, with no provider or judge
failure. This is the current-version graph set; it does not overwrite the
separately reported original 300/350 result.

## Layout

```text
dot/<model>/<paper>.dot
manifest.csv
summary.json
cross_stage_audit.json
```

`manifest.csv` binds every file to its paper-model key and records its SHA-256
digest, byte size, parsed node/edge counts, root, and validation flags.
`summary.json` records the set-level counts and hashes of the accounting and key
tables used to define this release.

## How the 350 Files Relate to the Earlier 300

The locked 300 paper-model keys are a strict subset of the current 350 keys.
All 300 shared DOT files have identical SHA-256 digests across the two branches;
none was silently replaced. The current version adds the 50 formerly residual
paper-model keys. `cross_stage_audit.json` records this comparison.

Source-path resolution and stage membership are different notions. Of the 350
accepted files, 302 resolve from the materialized semantic-repair collection
and 48 resolve directly from closeout outputs. The 302 include the unchanged
original 300 plus two newly closed rows; this does not change the 300-plus-50
stage accounting.

## Verification

Every file was checked for a unique paper-model key, non-empty UTF-8 content,
successful DOT parsing, PEARL strict graph-structure validity, and exact
source-to-release SHA-256 equality. The final set contains 70 graphs from each
of the five models and five graphs for each of the 70 papers.
