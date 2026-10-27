# Specimen submission video — 110 seconds

Use the working local dashboard at http://127.0.0.1:8765 with its bundled synthetic fixtures. Record at 1080p or better, use a readable browser zoom, and keep unrelated tabs and notifications out of the shot. Do not use private data. Run the three cases once before recording so imports are warm; the visible runs must still be real. The optional adviser is off. The [CLI alternative](CLI_VIDEO_SCRIPT.md) contains tested commands if you want an insert shot.

| Time | Exact screen action | Word-for-word narration |
| --- | --- | --- |
| 0:00–0:12 | Show the dashboard and the 1,200-row Unicode demo card. | “You want to report a data bug, but the failing CSV has twelve hundred rows and fields you should not share. Manually deleting rows can delete the trigger. Specimen makes the failure itself the check.” |
| 0:12–0:32 | Click Run on Unicode ASCII export. Show the real evidence timeline and dataset preview. | “This is synthetic data with a real Python exporter failure. Specimen repeats the baseline, removes rows while replaying the script, and records which smaller inputs preserve its observed exception signature.” |
| 0:32–0:52 | Show one retained row, replacement highlighting, fingerprint and final replay count. | “Twelve hundred rows become one. Selected sensitive values are replaced, and the final input still reproduces the same exception type at the same script frame. That is execution evidence, not an AI guess at the cause.” |
| 0:52–1:10 | Click Repeated relationship pair; show the verified two-row output. | “Some failures need a relationship. This second case retains two rows with a repeated email and customer identifier pair, instead of incorrectly shrinking the input to one unrelated record.” |
| 1:10–1:31 | Click Required sensitive value. Show Needs review, the blocker, and disabled ZIP. | “Here the failure depends on a literal in a field that must be replaced. Sanitization breaks the failure, so Specimen blocks the shareable output. Reporting that conflict is more useful than quietly leaking the trigger.” |
| 1:31–1:43 | Return to the Unicode case, wait for Verified, click Download evidence ZIP. Show the downloaded filename. | “A verified run can export the specimen, tested reproducer, and report together. Supplied scripts run through Docker; these bundled demos use checked-in trusted scripts.” |
| 1:43–1:50 | End on the public GitHub README URL. | “Specimen is open source. It preserves an observed failure signature; it does not prove root cause, a global minimum, or complete anonymization. Review artifacts before sharing.” |

## Recording checks

- All three browser cases, the real upload flow, and the ZIP download were exercised during acceptance. See the QA report.
- Keep each result paired with the correct run; don't splice different job IDs into one case.
- Pseudonymized bytes vary between runs. Row counts and failure signatures are the stable demo outcomes; never promise an identical hash.
- If a run is slower, use an obvious labelled cut. Never fake the timeline or claim the privacy-conflict case is verified.
- Keep the disabled download button visible in the conflict shot. End with the real public repository, not a placeholder.
