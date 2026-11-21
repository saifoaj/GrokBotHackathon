# Proof pack

Evidence that the screening system is deterministic, robust to surface changes in the data, and
scores as reported. Every file here was produced from code commit `bcb186b` on the dev data; nothing in
this folder is code. Rerun any of it with the commands shown at the top of each file.

| What | Result | File |
|---|---|---|
| Practice score (validate.py --score, 50 labelled hits) | 100.0, 50/50 right, 0 unsafe clearances, 0 problems | [scorecard.txt](scorecard.txt) |
| Determinism: 1 normal run + 5 runs on shuffled copies (line and key order) with different PYTHONHASHSEED | all 6 SHA-256 hashes identical | [shuffle5.txt](shuffle5.txt) |
| Robustness: surface changes that must not change any decision | 0 decisions changed in every category | [perturbation.txt](perturbation.txt) |
| Run receipt: input hash, code commit, output hash | output byte-identical to the committed output/dev.jsonl | [receipt.txt](receipt.txt) |

## Determinism (the six hashes)

```
run 0  original  system/run         3ea41dc49a427c444e839353bc3ae7e81a13911ef58a9a5fff3fd49eb7e102f2
run 1  shuffled  PYTHONHASHSEED=1  3ea41dc49a427c444e839353bc3ae7e81a13911ef58a9a5fff3fd49eb7e102f2  ok
run 2  shuffled  PYTHONHASHSEED=42  3ea41dc49a427c444e839353bc3ae7e81a13911ef58a9a5fff3fd49eb7e102f2  ok
run 3  shuffled  PYTHONHASHSEED=777  3ea41dc49a427c444e839353bc3ae7e81a13911ef58a9a5fff3fd49eb7e102f2  ok
run 4  shuffled  PYTHONHASHSEED=2024  3ea41dc49a427c444e839353bc3ae7e81a13911ef58a9a5fff3fd49eb7e102f2  ok
run 5  shuffled  PYTHONHASHSEED=31337  3ea41dc49a427c444e839353bc3ae7e81a13911ef58a9a5fff3fd49eb7e102f2  ok
```

## Perturbation categories

| Category | What changes in the data | Decisions changed |
|---|---|---|
| dates | customer and KYC dates rewritten in another unambiguous format | 0 |
| name_case | names re-cased, accents added or removed (customers, KYC, screened, hits, aliases) | 0 |
| note_names | names inside KYC notes written in Title case instead of capitals | 0 |
| aliases | hit aliases reordered | 0 |
| note_noise | notes with extra whitespace and punctuation | 0 |
| countries | country names swapped with ISO codes and back | 0 |
| ALL | all of the above at once | 0 |

## Tools

- `tools/scorecard.sh [checkout] [data]` one summary line: score, unsafe clearances, problems, decision counts, determinism, run time.
- `tools/shuffle5.sh [data] [system dir]` the determinism check above.
- `tools/perturb.py [data] [--only cat1,cat2]` the robustness check above.
- `tools/compare.py a=out1.jsonl b=out2.jsonl` two or more output files side by side, "one clears, another doesn't" first.
