# Product media

Use real interface captures with synthetic public contracts and invented people. Never capture a private upload,
a personal account menu or a real team’s data for this directory. The product recording linked from the README
shows an earlier interface; it is separate from current screenshot evidence.

## Capture

The live capture tool defaults to GET/HEAD interactions only. Select a known synthetic saved review and record
the deployed source commit and service revision:

```bash
node docs/public/shoot_live.mjs CAPTURE_DIR PUBLIC_BASE_URL \
  --review-id SAVED_SYNTHETIC_REVIEW_ID --commit SOURCE_COMMIT --revision SERVICE_REVISION --synthetic-public
```

It explicitly enters the read-only demo, then captures its workspace, a saved review, a selected finding,
the recorded trace and company rules. It cannot create reviews, replies, uploads or policy changes.
For the sample-preview/earlier-result transition, `--cached-sample-id SAMPLE_ID` permits only that exact
sample-opening request with the explicit guest header. Verify the sample already has a saved result first;
a missing guest result must fail, never become a new review. No response delay is inserted.

New live paid-review captures require separate authorization and a synthetic contract. A cached opening is
not fresh processing: do not replay phases, fabricate progress percentages or use it as a speed benchmark.
Do not use a private upload to demonstrate fresh phases. The server-reported phase feature and the source
of each public image must be described separately when they differ.

The optional `shoot_app.mjs` tool requires `--mock --dist DIR --commit SOURCE_COMMIT` and only captures a
local offline synthetic build. Its manifest says `mock`; those images must never silently replace live captures.
Both tools use an already installed browser and dependencies; they do not install software.

## Optimize supplied frames

```bash
python3 docs/public/make_assets.py --manifest CAPTURE_DIR/capture.json --out-dir OUTPUT_DIR
```

The manifest includes:

- `capture`: source `commit`, deployed `revision`, `captured_at` in UTC, `mode` (`live`, `local`, `mock` or
  `illustrative`), `data_scope: "synthetic-public"`, public `base_url`, viewport and an exact action `recipe`.
- `screens`: explicit relative `input` PNG, relative `output` PNG and optional `max_width`, `crop` or palette `colors`.
- `animations`: output GIF, caption, maximum width and ordered `frames` with relative input filenames and
  `duration_ms`. All frames must have the same dimensions after explicit crops/resizing.
- `errors`: capture failures; a nonempty list prevents generation.

The generated `media-evidence.json` records capture provenance, source-frame hashes, output dimensions,
GIF hold times, file sizes, optimization and captions. It excludes source machine paths and browser logs.
A GIF assembled from discrete captured screens uses pauses for reading; those pauses are not review latency.
Review every still and representative GIF frames before publishing, including text at the README’s display size.
Keep unmodified input frames and full browser evidence separately from the public optimized assets.

The chart tool requires an explicit dated evaluation JSON; it never guesses a newest result:

```bash
python3 docs/public/make_assets.py --chart-only --eval eval/results/20261018T124253Z.json --out-dir OUTPUT_DIR
```

A partial or selected-subset evaluation must not replace the full-run benchmark or become a general accuracy claim.
