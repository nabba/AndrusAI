# Deploy automation (push-to-main → gateway rebuild)

The gateway image is built on the **host** (`docker compose … --build gateway`),
so any auto-deploy mechanism has to run on the host too — it cannot live in CI or
inside the container. The canonical deploy is `scripts/deploy_gateway.sh`
(`git pull --ff-only` → rebuild + restart gateway → reload watchdog → verify).

## Active mechanism — git-pull poller (2026-06-15)

`scripts/deploy_poller.py`, run as the launchd LaunchAgent
`org.andrus.botarmy.deploy-poller` (`scripts/deploy_poller.plist`,
`StartInterval` 180 s). Each tick:

1. `git fetch origin main`
2. If `origin/main` is a **clean fast-forward** ahead of local `HEAD` →
   run `scripts/deploy_gateway.sh`.
3. Otherwise, do nothing.

Pull-based by design: **no inbound port, no public exposure, no GitHub webhook
configuration.** The host reaches out; nothing reaches in. This matches the
other host LaunchAgents (warm-spare sync, db-backup, browse-collector).

### Safety
- **Single-flight** — `fcntl.flock` on `~/.crewai-bridge/deploy_poller.lock`; a
  deploy that outlasts the 180 s interval is never stacked behind the next tick.
- **Branch-scoped** — acts only when local `HEAD` is on `main`; a checked-out
  feature branch is left alone.
- **Fast-forward-only** — deploys only when local is an ancestor of
  `origin/main`. A diverged or locally-ahead tree is surfaced once and never
  clobbered (`deploy_gateway.sh` uses `git pull --ff-only`).
- **No retry-loop on a failed build** — `deploy_gateway.sh` pulls first, so local
  `HEAD` already advanced; the next tick reads "up to date". The operator is
  Signal-alerted and fixes forward (a new commit) or reruns the deploy by hand.
- Lock + state live in `~/.crewai-bridge/` (outside the repo); log is
  `workspace/healing/.deploy_poller.log`. Routine no-ops are silent (no spam).

### Collection gate (host-side CI substitute)

GitHub Actions is billing-locked on this account, so `.github/workflows/` (both
`tests.yml` and `deploy-smoke`) does not run. The poller therefore carries the
test gate itself: before each deploy it checks out the about-to-deploy SHA into a
**throwaway git worktree** and runs `pytest --collect-only` there (in the gateway
`.venv`). A collection error **withholds the deploy** — the container keeps its
last-good build — and Signal-alerts **once per bad SHA** (subsequent ticks on the
same SHA are silent until a fix commit advances `main`).

- **Fail-OPEN on gate-infra trouble** — a missing `.venv`, `pytest`-not-found, a
  timeout, or any ambiguous non-zero exit lets the deploy proceed (logged). A
  broken *gate* must never wedge every deploy; only a confirmed `pytest`
  "collected … error" summary (rc 2) blocks. See `collection_gate()`.
- Catches exactly the class the conftest collect-guard fixes: import rot,
  broken/renamed modules, cross-file `sys.modules` pollution.
- Disable with `DEPLOY_POLLER_GATE_ENABLED=0`; tune via `DEPLOY_POLLER_GATE_CMD`
  / `_GATE_TIMEOUT` (default 600 s) / `_GATE_STATE`.
- When GHA billing is restored, `tests.yml` becomes the PR-time gate and this
  host gate becomes defense-in-depth — they compose, no conflict.

### Operate
```
./scripts/install_deploy_poller.sh install     # load the LaunchAgent
./scripts/install_deploy_poller.sh run-once     # poll now; prints/records 'uptodate' when in sync
./scripts/install_deploy_poller.sh status        # is it loaded?
./scripts/install_deploy_poller.sh logs          # tail the log
./scripts/install_deploy_poller.sh stop          # back to manual deploys
```

## Alternative (built, not active) — inbound webhook (#133)

`scripts/deploy_webhook.py` + `scripts/install_deploy_webhook.sh` is a
push-based alternative: a GitHub webhook → HMAC-verified merge-to-main → deploy,
requiring Tailscale Funnel + a GitHub webhook secret. It was shipped in PR #133
but deliberately left **uninstalled** in favour of the poller (no public attack
surface, no out-of-band setup — that setup friction is why it stayed dormant).
Both paths end at the same `deploy_gateway.sh`; **run only one.**
