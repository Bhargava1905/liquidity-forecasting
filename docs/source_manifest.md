# Source and provenance manifest

| Item | Value |
|---|---|
| External data | None. Every record is synthetic and produced by `scripts/generate.py` |
| Personal or confidential data | None. Names (`Aster … Ltd`, `Meridian Commercial Bank`, `CUST-00001`) are invented |
| Public reference data | Not used. FX starting levels are round illustrative numbers, not a market feed |
| Licence | Generated data is redistributed with this repository |
| Seed | `20261022` (override with `--seed`; the manifest records the seed used) |
| Transformations / discarded fields | Not applicable: no external source |

`data/generated/source_manifest.json` is rewritten on every generation and records the generator command, seed, scale,
time-model constants, reporting currency, provenance statement, and the row count and SHA-256 of every output file.
`reproducibility.json` records the comparison of two generations from the same seed (`passed: true`, with both
checksum sets).
