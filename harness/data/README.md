# `harness/data/` — the pre-staged data volume

Mounted **read-only at `/data`** in the container when `CRUX_DATA_DIR` is set in
`harness/.env`; with it unset there is no `/data` at all, and most runs need none.

## What belongs here

**Only what the agent cannot retrieve itself.** The container has open egress (only the
cloud metadata endpoint and the model-provider API domains are blocked), so anything
reachable by a scripted client is the agent's job to fetch — that is part of what the run
measures, and pre-staging it both wastes your time and hands the agent a head start that
is not being tested.

Stage a source here only when it is genuinely out of reach:

- it requires registration, a login, an emailed extract, or a click-through licence;
- it is only available through an interactive export that no scripted client can drive;
- or it is revised in place under an unchanged URL, so a later rerun could fetch different
  bytes and nothing in the record would reveal it.

If you find yourself staging something because it was *convenient*, take it out.

## Layout

One directory per source, with a `SOURCE.txt` beside the files.

```
harness/data/
  <source-name>/
    SOURCE.txt
    <the files>
```

`SOURCE.txt` is free text but should carry the origin, the date you retrieved it, the
vintage or release, why it had to be staged rather than fetched, and anything that would
mislead a reader later. Provenance that exists only in your head is provenance the paper
cannot cite. The staging script warns about any directory missing one.

## After adding or changing anything

```bash
ops/stage_data.sh            # hash + regenerate INDEX.md
ops/stage_data.sh --check    # verify nothing moved; run before launch
```

`INDEX.md` is what the agent reads to discover what it has, so a stale index means data
you staged is invisible. `manifest.sha256` is the record of exactly which bytes the run
started from: `ops/provision-box.sh` re-checks it after the copy to the box, `ops/run.sh`
checks it again at launch, and `ops/collect.sh` re-checks it after the run.

## Notes

- Everything here except this README is **gitignored** — the staged files, the generated
  `INDEX.md`, `manifest.sha256`, and the `SOURCE.txt` files. The volume is a *run input*,
  not repository content, and keeping its inventory out of the repo is what lets the
  harness stay general rather than carrying one study's subject matter around with it.
  The index and manifest are collected with the run output instead.
- The mount is read-only. Decompress or transform into `/workspace`, which keeps the
  staged copy pristine and makes the transformation part of the reproducible record.
- To hand the agent a file *after* the run has started, drop it in `/workspace/inbox`
  (`docker cp <file> <container>:/workspace/inbox/`): the loop delivers it as an operator
  message at the next turn and the agent logs it verbatim. Anything staged here before
  launch is simply part of the environment.
