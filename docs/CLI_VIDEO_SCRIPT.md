# Specimen video script (about 100 seconds)

Target length: 90–120 seconds. Narration is written for a calm, natural pace of about 2.6 words per second, so the spoken track runs roughly 95 seconds with short pauses. Every command in this script was run on 2026-10-27 with the output shown here; the conflict command exits 1 by design (see [CLI QA](CLI_QA.md)).

The video focuses on the real reduction and on the privacy conflict. It does not show the dashboard or ZIP download; those belong to the browser QA.

## Demo setup (off camera)

1. Terminal in the repository root, large font, dark theme, prompt short.
2. Dependencies already synced: `uv sync --extra dev`.
3. Clear old outputs so the video starts clean:

   ```sh
   rm -rf /tmp/specimen-video
   ```

4. Run `uv run specimen --help` once to warm up the environment.
5. Docker is not needed for this video because all three demos run in the default local mode for bundled synthetic fixtures. Do not show `--runner local` on screen for supplied files.
6. Optional on-screen still: `docs/images/specimen.png` (the existing dashboard screenshot) for the opening card only.

Every command below is pasted and run as written. Do not use `--model-assist` in this video.

## Shot list and timing

| Time | Shot | On-screen action |
| --- | --- | --- |
| 0:00–0:11 | Title card | Still image `docs/images/specimen.png`, with the words "Specimen" on screen. |
| 0:11–0:38 | Unicode demo | Terminal: run the unicode demo, then `cat` the specimen. |
| 0:38–0:58 | Relations demo | Terminal: run the relations demo, then `cat` the specimen. |
| 0:58–1:30 | Conflict demo (main point) | Terminal: run the conflict demo, print exit code, list the folder, show the blocker. |
| 1:30–1:42 | Closing | Plain text card listing the three limits. |

## Commands and expected on-screen output

### Shot 2 — Unicode (0:11–0:38)

```sh
uv run specimen demo unicode --output /tmp/specimen-video/unicode
cat /tmp/specimen-video/unicode/specimen.csv
```

Expected on screen:

- stderr progress lines (`[progress] ...`, ending with a completion line).
- A JSON report on stdout with `"status": "verified"` and `"final_rows": 1`.
- The `cat` output is two lines: a header and one data row. The name and email fields are pseudonymized and contain non-ASCII characters in the name field.

Narration (word-for-word):

> Start with the Unicode case. This synthetic file has 1,200 rows, and the script fails when it writes the names to an ASCII export. Specimen removes rows and replays the script after each change. When it finishes, one row is left. Its selected values are pseudonymized, and the report records only the exception type and the final script line, not the message.

### Shot 3 — Relations (0:38–0:58)

```sh
uv run specimen demo relations --output /tmp/specimen-video/relations
cat /tmp/specimen-video/relations/specimen.csv
```

Expected on screen:

- `"status": "verified"` and `"final_rows": 2` in the JSON report.
- The `cat` output is a header and two data rows that share the same pseudonymized email and `customer_id`.

Narration (word-for-word):

> The relations case starts with 1,200 rows and keeps two. Both rows share the same pseudonymized email and customer ID. Specimen kept that repeated pair, and the duplicate-relationship failure still reproduces with it.

### Shot 4 — Conflict (0:58–1:30) — the main point

```sh
uv run specimen demo conflict --output /tmp/specimen-video/conflict; echo "exit $?"
ls /tmp/specimen-video/conflict
grep -A1 '"blockers"' /tmp/specimen-video/conflict/report.json
```

Expected on screen:

- stderr progress lines, then `[warning] Privacy or verification checks blocked specimen output`.
- `exit 1`.
- `ls` shows only `REPORT.md` and `report.json`. There is no `specimen.csv` and no `repro.py`.
- The `grep` shows `"blockers": [` followed by `"sanitized candidate did not preserve the baseline failure"`.

Narration (word-for-word):

> Now the conflict case. This failure depends on one literal name in a sensitive column. Specimen tries to replace that name, and the sanitized candidate no longer shows the same failure. So it does not write a shareable result. The exit code is one. The folder has only the report and its Markdown summary. There is no specimen CSV and no reproducer. The report names the blocker.

### Shot 5 — Closing (1:30–1:42)

On-screen text card (no command):

- Specimen does not prove root cause.
- It does not guarantee the smallest possible CSV.
- Pseudonymization is not anonymization. Review every artifact before sharing.

Narration (word-for-word):

> Specimen does not prove root cause, and it does not promise the smallest possible CSV. Pseudonymization is not anonymization. Review every file before you share it.

## Claims verified

These were checked by running commands, not by reading the code alone.

| Claim | Evidence (from CLI QA) |
| --- | --- |
| The unicode demo reduces 1,200 rows to one verified row with the same fingerprint. | Exit 0; `final_rows: 1`; baseline and final fingerprint `UnicodeEncodeError\|line 13 in <module>`; `specimen.csv` is 2 lines. |
| The relations demo keeps two rows with the same repeated pair, and the failure reproduces. | Exit 0; `final_rows: 2`; fingerprint `ValueError\|line 12 in <module>`; both rows share the same email and customer ID. |
| The conflict demo is blocked and writes no shareable specimen. | Exit 1; status `needs_review`; blocker message in `report.json`; only `report.json` and `REPORT.md` in the output folder. |
| Fingerprints contain no exception message or raw output. | `report.json` fingerprint is type, line, and function only. |
| Supplied files run through Docker by default with the same verified result. | `reduce --runner docker` on the synthetic unicode and relations examples: verified, same fingerprints. |
| The full unit suite passes. | `uv run pytest -q`: 73 passed. |

## Claims to avoid

Do not say or show any of the following. Each one is either unverified or contradicted by the project's own docs.

- "Specimen proves the two runs share a root cause." It checks an observed fingerprint, not root cause.
- "Specimen finds the smallest possible CSV." It is 1-minimal by row deletion, under the tested script and configuration only.
- "Specimen anonymizes data." It pseudonymizes selected fields only.
- "The output is byte-for-byte reproducible." Pseudonym keys are random per run, so specimen bytes and `sha256` differ between runs.
- "The CLI writes a ZIP." It does not. The ZIP is produced only by the dashboard, and that path is not verified in this video.
- "The dashboard ZIP download works." Not verified here; leave it to the browser QA.
- "The model adviser helped." `--model-assist` was not used in any run shown.
- "Docker makes any script safe." Docker reduces exposure. It is not a guarantee.
- "Specimen works on any CSV or any bug." It is for deterministic CSV and Python failures only.
- Any claim about an external service integration. This project does not claim one, and none is needed for the demo.

## Pre-recording checklist

- [ ] Run each command once off camera and confirm the expected output.
- [ ] Confirm `/tmp/specimen-video` is removed before recording.
- [ ] Keep the narration within the time budget; pause after each `cat` and `grep`.
- [ ] Final length between 90 and 120 seconds.
