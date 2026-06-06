# Sandboxed `gee_run_script`

Follow-up to the #5 in-process-`exec` isolation (which covered `pdf_compose`).
`gee_run_script` runs LLM-authored Python via `exec()`; the in-process
`_safe_exec` hardening is defence-in-depth, not a boundary. This routes it into
the ephemeral evolver container — the same mechanism as `pdf_compose` — but
Earth Engine has two extra needs that make it a **distinct, default-OFF** opt-in.

## Why distinct from `sandboxed_tool_exec_enabled`

Earth Engine needs **(a)** the service-account credential and **(b)** outbound
network (`earthengine.googleapis.com`). The credential is a bind-mounted file
the `VOLUMES:0` container can't see, so the gateway forwards the **SA-JSON
content inside the job dict** (`AAI_GEE_JOB`), the container writes it to a temp
file + `ee.Initialize()`s, and the container runs with `network_mode="bridge"`.

Forwarding a GCP key into a sandbox is a real decision, so it gets its **own
switch** — `sandboxed_gee_exec_enabled` (default OFF) — separate from pdf's.

## Net security (it's still a big win)

| | in-process (today) | sandboxed container |
|---|---|---|
| Reach the EE SA key | yes (reads the cred file) | yes (forwarded) |
| Reach **other** gateway secrets | **yes** | no |
| Reach chromadb / workspace | **yes** | no |
| Reach docker-proxy → **host root** | **yes** | no |
| Network | full | full (bridge) |

So containerizing **strictly shrinks the blast radius**: a sandbox escape sees
only the EE key + internet — which it already had in-process — and loses
everything else. With the docker-broker (PR #151) active, the bridge-network
gee container also can't be created with binds/privileged. The residual (EE-key
exfil + egress) is the reason it's an explicit opt-in.

## Activation

1. Build the evolver image: `docker compose --profile evolver build evolver`.
2. Flip `sandboxed_gee_exec_enabled` ON in `/cp/settings`.
3. Run a `gee_run_script` call (e.g. a Hansen loss query that renders a map) and
   confirm the result + the PNG land in `workspace/output/maps/`.

Default OFF → inert until flipped. On any infra failure (image not built, no
credential to forward, transport error) it **falls back to in-process** and logs
a warning, so the tool keeps working. A script that merely errored still ran in
the container (returns `ok=False`, not a fallback).

## Verification
`tests/test_gee_sandbox_exec.py` (host-runnable, injected fake runner): sentinel
round-trip, oversize-artifact rejection, SA-credential forwarding + `bridge`
network + decoded-PNG write to the real maps dir, and fallback-to-`None` on
no-creds / infra failure. The full in-container EE run needs the evolver image +
network + a real SA key (operator validation step above).

## Residual / follow-up
The `gee` script can read the forwarded EE key from `os.environ["AAI_GEE_JOB"]`
if it escapes `_safe_exec` — unavoidable, since EE needs the credential
in-container (and it could read the same key in-process). A future hardening
would mint a short-lived EE token gateway-side and forward only that.
