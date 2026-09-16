# searchwrap

One search interface over three engines — Everything (es.exe), ripgrep (rg), and fff — with
automatic lane routing, per-query engine **racing**, cross-engine fallback, and a single
unified JSON envelope. You own none of the engines; you own the wrapper.

## Install

```
pip install searchwrap
```

`fff-search` installs automatically as a dependency. `rg` and `es.exe` are provisioned on
demand:

```
searchwrap status          # what's present, what's missing, which lanes are degraded
searchwrap bootstrap rg    # multi-source: github release -> scoop manifest -> system pkg mgr
searchwrap bootstrap es    # downloads official ES CLI from voidtools (never bundled)
searchwrap bootstrap fff   # pip install fff-search into the active interpreter
```

If a source cannot be resolved, bootstrap says exactly that and lists manual fallbacks
(`winget install BurntSushi.ripgrep`, `scoop install ripgrep`, `choco install ripgrep`).

Optional: `set SEARCHWRAP_AUTOBOOT=1` lets the first query auto-provision a missing es.exe
(one attempt, then the lane degrades gracefully until provisioned).

## Engines and licensing

| Engine | License | Bundled? | Provisioned from |
|---|---|---|---|
| ripgrep (rg) | MIT | no | github release / scoop / winget / choco |
| fff | open source (see fff-search) | pip dependency | PyPI |
| Everything es.exe | freeware, closed source | **never** | voidtools official URL at install time |

es.exe additionally requires the Everything *service* (GUI app, admin to install) —
`searchwrap status` detects it; without it, es-native lanes degrade and routing shifts to
fff/rg automatically.

## Usage

```
searchwrap query "pattern"                          # machine-wide name search
searchwrap query "pattern" --tree C:\proj           # tree race: fff vs es
searchwrap query "pattern" --content --tree C:\proj # content search (rg)
searchwrap query "ext:blend" --meta                 # counts/filters (es)
searchwrap query "pattern" --json                   # full envelope
searchwrap serve|stop|health|stats|selftest|setup
```

Human output: `lane=NAME_TREE winner=fff ms=5 hits=6 total=6`. `--json` gives the full
envelope: `lane, engines, winner, ms, hits, total, verified_not_found, cancelled, partial,
cpu_ms, fff_available`.

## Environment

- `SEARCHWRAP_ES`, `SEARCHWRAP_RG` — explicit engine paths (override resolver chain)
- `SEARCHWRAP_PYTHON` — interpreter the CLI uses to spawn the daemon (defaults to its own;
  falls back `%USERPROFILE%\.search-daemon` venv, then `VIRTUAL_ENV`, then PATH)
- `SEARCHWRAP_AUTOBOOT=1` — allow silent engine provisioning on first query

## Architecture notes

- Daemon listens on `127.0.0.1:47611` (exclusive bind — a second daemon cannot silently
  coexist) and reaps all engine children on death via a kill-on-close Job Object.
- Tree lanes **race** fff against es; first valid answer wins, losers are cancelled.
- Machine-name lane: es, falling back to rg-wide filesystem scan on miss/absence.
- Misses are *verified* (`verified_not_found`) across engines before being reported.
- Daemon CPU: ~0 idle, 1.8–6.3 ms/query under load; RAM flat (33 MB idle, 49 MB peak under
  24-way concurrent load); no leaks over 500-query endurance runs. See
  `benchmarks/BENCHMARK-RECORD.txt` for the full record and methodology.
