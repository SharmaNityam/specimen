# CLI and Docker QA

This log records end-to-end runs of the Specimen CLI, the Docker-backed `reduce` path, and the ZIP artifact replay. It covers command-line usage only. Browser and native dashboard checks are out of scope here.

All outputs were written to isolated temporary directories under `/tmp`, not under the repository. No `--model-assist` flag was used, so no model endpoint was contacted. No private input files were read. Source files were not edited.

## Environment

- Host: macOS (Darwin 27.0.0), zsh.
- Repository: `/Users/nityam/Documents/ChatGPT/openfire`, branch `codex/specimen`, clean working tree at the start of this QA.
- Python: `uv run` uses the project `.venv` (Python 3.14 in this checkout). The README recommends Python 3.12; these runs did not test 3.12.
- Docker: daemon `29.5.3`; image `python:3.12-slim` present locally (`a0f57064cbdc`).

## 1. Full unit suite (run once)

```sh
uv run pytest -q
```

Result: **73 passed, 1 warning** in 17.68s.

The warning is a deprecation notice from `starlette.testclient` (it recommends `httpx2`). It is not a failure.

## 2. Local CLI demos

Each demo ran into a fresh directory under one temp root (`/tmp/specimen-qa.u8uA7f`).

```sh
uv run specimen demo unicode   --output "$T/unicode"   > "$T/unicode.stdout.json"   2> "$T/unicode.stderr.txt"
uv run specimen demo relations --output "$T/relations" > "$T/relations.stdout.json" 2> "$T/relations.stderr.txt"
uv run specimen demo conflict  --output "$T/conflict"  > "$T/conflict.stdout.json"  2> "$T/conflict.stderr.txt"
```

| Demo | Exit | Input rows (`examples/*/data.csv`) | Report status | Final rows | Runs | Baseline fingerprint | Final fingerprint | Artifacts written |
| --- | ---: | ---: | --- | ---: | ---: | --- | --- | --- |
| `unicode` | 0 | 1200 | `verified` | 1 (`specimen.csv` has header + 1 row) | 31 | `UnicodeEncodeError\|line 13 in <module>` | same | `specimen.csv`, `repro.py`, `report.json`, `REPORT.md` |
| `relations` | 0 | 1200 | `verified` | 2 (header + 2 rows) | 58 | `ValueError\|line 12 in <module>` | same | `specimen.csv`, `repro.py`, `report.json`, `REPORT.md` |
| `conflict` | 1 | 240 | `needs_review` | 1 (candidate only, not written) | 39 | `ValueError\|line 10 in <module>` | not reached | `report.json`, `REPORT.md` only |

Checks on the outputs:

- **Row counts.** `wc -l` on the specimens returned 2 (unicode) and 3 (relations), matching header plus 1 and 2 data rows. Input counts were confirmed with `csv.DictReader`.
- **Fingerprint evidence.** `report.json` records equal baseline and final fingerprints for both verified runs. The fingerprint is only exception type, `|`, and the final script frame line and function. No exception message or stdout appears.
- **Blocked conflict artifacts.** The conflict run has no `specimen.csv` and no `repro.py`. Its `report.json` sets `"blockers": ["sanitized candidate did not preserve the baseline failure"]`, `"sha256": null`, and `artifact_names: ["report.json", "REPORT.md"]`. The CLI exits 1 on any non-verified status. The stderr progress shows `[warning] Privacy or verification checks blocked specimen output`.
- **Unicode content.** The retained row keeps non-ASCII characters in the name and email fields (`Pþsc Tzowjhwje`). That is the expected trigger for the ASCII-export failure.
- **Relations content.** Both rows share the same pseudonymized email and `customer_id` (`dqfioj@jjddcuk.xweb`, `W0132`). The repeated pair is preserved.

## 3. Docker-backed `reduce` on supplied synthetic files

These runs use the `reduce` command with the bundled synthetic CSV and reproducer passed as supplied files. The default runner is Docker.

```sh
uv run specimen reduce examples/unicode/data.csv --script examples/unicode/repro.py \
  --sensitive name,email --output "$T/docker-unicode" --runner docker
uv run specimen reduce examples/relations/data.csv --script examples/relations/repro.py \
  --sensitive name,email,customer_id --output "$T/docker-relations" --runner docker
uv run specimen reduce examples/conflict/data.csv --script examples/conflict/repro.py \
  --sensitive name --output "$T/docker-conflict" --runner docker
```

| Case | Exit | Wall time | Status | Final rows | Runs | Final fingerprint | Artifacts |
| --- | ---: | ---: | --- | ---: | ---: | --- | --- |
| unicode | 0 | 8.21 s | `verified` | 1 | 31 | `UnicodeEncodeError\|line 13 in <module>` | 4 standard files |
| relations | 0 | 12.43 s | `verified` | 2 | 58 | `ValueError\|line 12 in <module>` | 4 standard files |
| conflict | 1 | not timed | `needs_review` | 1 (candidate) | not recorded | not reached | `report.json`, `REPORT.md` only |

Docker unicode and relations results match the local demo's status, row count, run count, and fingerprint. The specimen bytes differ between the Docker and local runs; see Limitations.

Containers: `docker ps -a` lists 11 stopped containers from unrelated projects. Specimen's runner uses `--rm`, and none of the listed containers came from these runs.

### Isolation probe

This is a separate check. It runs the same security flags Specimen passes to `docker run`, against a container that tries to reach the network and write the root filesystem. Specimen's runner also adds `--user 65534:65534`, a `/tmp` tmpfs, and `--name`, which the probe leaves out.

```sh
docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --memory 256m --cpus 0.5 --pids-limit 64 python:3.12-slim python -c "..."
```

Result: `network=blocked OSError`, `rootfs=read-only OSError`.

## 4. ZIP artifact replay (CLI-generated reproducer)

The CLI does not write a ZIP. The archive is built by `build_artifact_zip` in `specimen/artifacts.py`. The dashboard server calls it at `specimen/server.py:178`.

For this check, the function was called directly on the CLI-generated `unicode` and `relations` output directories. Each archive was then extracted into a fresh temp directory and its `repro.py` was replayed with an isolated interpreter (`python -I repro.py specimen.csv`). The script is at `/tmp/specimen-qa-scripts/zip_replay.py`, outside the repository.

```sh
uv run python -I /tmp/specimen-qa-scripts/zip_replay.py "$T"
```

Result:

| Case | ZIP built | Entries | Replay exit | Last stderr line | Expected type present |
| --- | --- | --- | ---: | --- | --- |
| unicode | yes | `specimen.csv`, `repro.py`, `report.json`, `REPORT.md`, `REPRODUCE.txt` | 1 | `UnicodeEncodeError: 'ascii' codec can't encode character '\xfe' ...` | yes |
| relations | yes | same five entries | 1 | `ValueError: duplicate relationship` | yes |
| negative control (empty output dir) | no (`None`) | n/a | n/a | n/a | n/a |

The replay used the reviewed CLI-generated `repro.py`, not source analysis. The negative control confirms that a missing artifact yields no ZIP.

The conflict case has no ZIP because it has no `specimen.csv` or `repro.py`, and `build_artifact_zip` returns `None` for a missing artifact.

## Limitations

- **ZIP path tested at function level.** The archive was built by calling `build_artifact_zip` directly. The dashboard's HTTP ZIP route (`specimen/server.py:478`) was not exercised here. That belongs to the browser QA.
- **No model adviser.** `--model-assist` was not run. The `model.py` endpoint code path was not exercised.
- **Trusted local runner only for demos.** `--runner local` was used only for the bundled demos. Local mode runs without a sandbox.
- **Specimen bytes differ between runs.** Pseudonyms use a per-run random key (`secrets.token_bytes(32)` in `specimen/core.py:450`). The Docker and local unicode specimens therefore differ in replaced values, and the `sha256` in `report.json` (a SHA-256 of `specimen.csv`, computed at `specimen/core.py:593`) differs between runs (`3149db9d...` local vs `0b00afca...` Docker for unicode). Fingerprints, row counts, and statuses were stable. Byte-identical specimens are not a current guarantee.
- **Isolation probe is not the runner.** It checks the flag set, not Specimen's exact command line.
- **Single-machine, single-run.** Each case ran once. No timing or flakiness study was done.
- **Python version.** The tests and runs used Python 3.14 from `.venv`. Python 3.12 was not tested.
- **Not run:** `node --check specimen/web/app.js` (browser/native QA scope), `serve`, and any uploads to the dashboard.

## Source observations (not edited)

These were found during QA. The source was not changed, per the parent's instruction.

1. **Blocked conflict report wording.** In the conflict run, `REPORT.md` still says `Reduction 1-minimal by row deletion: yes` and `report.json` says `"minimized": true`, even though the sanitized candidate failed and the status is `needs_review`. The minimality claim may be accurate for the pre-sanitization reduction, but a reader could take it as applying to a shareable specimen. The parent should decide whether to scope or reword this line in blocked reports.
2. **`artifact_names` omit `REPRODUCE.txt`.** The verified report lists four artifacts, but the ZIP built from the same output includes `REPRODUCE.txt`. `README.md` describes the ZIP as five files. This is a small inconsistency between the report and archive contents.
3. **Non-reproducible specimen bytes.** Random per-run pseudonym keys mean rerunning the same command produces different specimen bytes and a different `sha256`. The README does not state this. It is worth one sentence in the README or `REPORT.md` if reproducibility of bytes matters for sharing.

No Specimen source bug was found that breaks the CLI, Docker, or ZIP flows.
