# Dashboard UI QA

This log records layout and interaction checks of the local Specimen dashboard (`specimen/web`). CLI and Docker runs are logged separately in [CLI QA](CLI_QA.md).

## Provenance

The browser observations below were made manually by the parent worker in a real browser session. The polish worker did not run browser automation and did not open the dashboard. The parent re-ran the browser checks after the font-size change in this pass; the results in the "Browser observations" section are from that re-run.

## Browser observations (manual, post-font-change)

Recorded by the parent worker after the font-size change:

- **Unicode demo.** Running the Unicode demo in the dashboard reported `verified`.
- **Table overflow at 1205px viewport.** The preview table overflows its container and is reachable with the scroll controls. Width 151px, scroll width 381px.
- **Scroll control.** Clicking the right arrow changed `scrollLeft` from 0 to 120.
- **Full values.** Full names and emails are present in the DOM. No ellipsis truncation was applied to them.
- **Full-page widths (1440px, 980px, 390px).** At each width `document.documentElement` width equals the viewport width. No panel, heading, or main content overflows on the right edge.
- **1205px viewport with zoom simulation.** `document.documentElement.style.zoom = 1.25` was applied at a 1205px viewport. Results match the 1205px check above: the table scrolls, and no page-level or panel-level overflow was observed. Screenshot: `docs/images/qa-scroll-125.png` (refreshed for the final font sizes).

### What the zoom check is and is not

The 1.25 check sets the CSS `zoom` property on the root element. It simulates a larger effective scale for layout stress. It is **not** browser chrome zoom (Cmd +/-), and it does not change device pixel ratio or the browser's own zoom level. Treat it as a stress test of the layout, not as proof that browser zoom behaves identically.

## Font-size change (this pass)

Small monospace text in the preview table was 8px, which is hard to read. The following selectors in `specimen/web/style.css` were changed:

| Selector | Before | After |
| --- | ---: | ---: |
| `.table-wrap table` (table text) | 8px | 11px |
| `.table-label` (table caption row) | 8px | 10px |
| `.preview-foot` (footer) | 8px | 10px |
| `.preview-foot` in the 590px media query | 7px | 10px |

All other 7px and 8px labels were left unchanged. The overall theme, colour tokens, grid tracks, and mobile breakpoints were not changed.

The larger table text makes the table wider, so its scroll width changed. The container height (150px, or 132px on narrow screens) is unchanged. The re-run above covers the resulting horizontal scroll behaviour.

## Known limits

- **Preview size.** The preview table shows at most **5 rows and 5 columns**.
- **Sensitive uploads.** Sensitive uploads are **not displayed** in the preview.

## Test runs (polish worker)

Run from `/Users/nityam/Documents/ChatGPT/openfire` after the font change:

```sh
.venv/bin/python -m pytest -q        # exit 0: 73 passed, 1 warning (starlette httpx deprecation)
node --check specimen/web/app.js     # exit 0
```

The warning is the same `starlette.testclient` deprecation notice recorded in the CLI log. It is not a failure.
