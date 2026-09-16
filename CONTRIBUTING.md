# Contributing to searchwrap

Thanks for helping build the wrapper. Quick rules so reviews stay fast:

## Scope
searchwrap owns **only** the wrapper: lane routing, engine racing, fallback, the unified
JSON envelope, and the CLI. The three engines (es.exe, rg, fff) are external dependencies -
never vendor or modify them. es.exe is closed-source freeware: never commit it, never ship
it; it is downloaded from voidtools at runtime.

## Setup
```
git clone https://github.com/problaems/searchwrap
cd searchwrap
pip install -e .
searchwrap status   # shows which engines you have / need
searchwrap selftest # self-contained fixtures, safe on any machine
```

## Ground rules
- **Benchmark discipline**: any perf claim needs a like-for-like measurement (same query,
  same fixture, medians n>=7, cold and warm states noted). Keep harnesses in `benchmarks/`.
- **Fixtures**: tests must be self-contained. Never point default tests at real user
  directories; use the temp fixtures in `daemon.py`.
- **Engines change**: fff-search is pinned `<0.11`. If you bump it, run the full selftest
  and re-check the grep API surface (`FileFinder.search` is name-only; content = `grep`).
- **No comments explaining obvious code**; comments only for non-obvious Windows APIs
  (struct layouts, flag offsets, index-latency workarounds).
- **Every product change** gets a regression check: all 7 selftest cases must pass, plus
  the envelope fields (`lane/winner/ms/hits/verified_not_found/cancelled/partial`) intact.

## Commits & PRs
- Small, single-purpose commits; imperative subject ("fix: wait for es index on fixture")
- CI (GitHub Actions, windows-latest) must pass: import, routing, fixtures, selftest
