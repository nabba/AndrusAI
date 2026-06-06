# Docker create-body broker

**Closes finding S2** (2026-06-06 whole-system review): the gateway reaches the
Docker Engine API through `tecnativa/docker-socket-proxy`, which gates *which API
paths* are reachable but **cannot inspect request bodies**. With `CONTAINERS=1`
+ `POST=1` enabled (required to spawn the evolver + ollama-fleet containers),
anything that can reach `POST /containers/create` may supply an arbitrary
`HostConfig` — e.g. `{"Binds":["/:/host"],"Privileged":true,"PidMode":"host"}` —
then `/start`, and own the host. `VOLUMES:0` does **not** stop this: bind mounts
ride inside the create-body `HostConfig`, not the `/volumes` endpoint.

This is the *amplifier* behind the in-process-`exec` findings (#5): any code that
executes in the gateway and can reach the proxy escalates to host root.

## Design

A thin, **unprivileged** HTTP body-filter inserted in front of the existing proxy:

```
gateway → docker-broker → docker-proxy → /var/run/docker.sock
```

- Forwards **every** request to the upstream proxy unchanged…
- …**except** `POST /containers/create` (with or without a `/v1.43/`-style
  version prefix), whose JSON body must pass `app/docker_broker/policy.py:validate_create_body`.
- The broker **holds no docker socket** and runs unprivileged — it only speaks
  HTTP to `docker-proxy`. A bug in it **fails closed** (the gateway can't reach
  Docker), it can never *open* a new escape.

### What the policy refuses (`validate_create_body`)
- `HostConfig`: `Binds`, `Mounts`, `Devices`, `DeviceRequests`,
  `DeviceCgroupRules`, `CapAdd`, `Sysctls` (present-and-truthy)
- `Privileged: true`
- `PidMode` / `UsernsMode` / `CgroupnsMode` / `UTSMode` / `NetworkMode` =
  `host` or `container:<id>`
- `IpcMode` other than `private` / `none` / `shareable`
- `SecurityOpt` containing `unconfined` (seccomp/apparmor)
- `Image` not on the allowlist (`DOCKER_BROKER_IMAGE_ALLOWLIST`, default
  `botarmy-evolver,ollama/ollama`; matched on a tag/digest boundary so a
  look-alike repo can't ride in)

The locked-down body `evolver_spawn.build_create_payload` produces (and the
ollama fleet's create) passes unchanged.

## Activation (opt-in, reversible)

Default deployments are **unaffected** — the service doesn't start and the
gateway keeps talking to `docker-proxy`. To cut over:

1. **Build + start the broker:**
   ```
   docker compose --profile docker-broker up -d --build docker-broker
   ```
2. **Validate it passes legitimate traffic** (with the gateway *still* on the
   proxy): from the broker's network, a clean evolver create succeeds and a
   malicious one is refused. Quick check — run the verified engine or a
   `pdf_compose` call and confirm the container still spawns. Watch the broker
   log: `docker compose logs -f docker-broker` (it logs every `BLOCKED create`).
3. **Cut the gateway over:** set in `.env`
   ```
   GATEWAY_DOCKER_HOST=tcp://docker-broker:2375
   ```
   then recreate the gateway: `docker compose up -d gateway`.
   (A dedicated var — **not** `DOCKER_HOST** — so it can't collide with the
   operator's shell `DOCKER_HOST`.)
4. **Confirm** container-spawning flows still work (verified engine / research
   experiment / `pdf_compose` sandbox). Roll back instantly by removing
   `GATEWAY_DOCKER_HOST` and recreating the gateway.

### Adding a legitimately-spawned image
If a real flow spawns a container from another image, add it (it fails *closed*
otherwise — visible immediately):
```
DOCKER_BROKER_IMAGE_ALLOWLIST=botarmy-evolver,ollama/ollama,my/other-image
```

## Verification
`tests/test_docker_broker.py` (host-runnable, no Docker): the policy core
(allows the real evolver payload + ollama; rejects 18 host-escape `HostConfig`
shapes, unlisted/look-alike images) **and** the forwarding server against an
in-process fake upstream (malicious create → 403, never reaches upstream; clean
create + non-create requests forwarded; versioned `/v1.43/containers/create`
path also filtered).

## Residual / follow-ups
- **Image pull progress is buffered**, not streamed (the pull still completes;
  no live progress bar). Fine for the fleet's occasional pulls.
- **`docker-proxy` still runs `privileged: true`.** It's an HAProxy front-end and
  almost certainly doesn't need it; dropping it is a recommended follow-up
  *after* verifying the proxy still reaches the socket on this host.
- **Strategic end-state** beyond this broker: a runtime-isolated sandbox
  (gVisor / Kata / rootless) for the evolver containers, so even a create that
  the broker *allows* can't escape via a kernel bug. Out of scope here.
