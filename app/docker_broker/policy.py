"""docker_broker.policy — the create-body allowlist (the security core).

Pure, dependency-free, exhaustively unit-tested. :func:`validate_create_body`
is the single decision point for a Docker ``POST /containers/create`` JSON body:
it returns ``(allowed, reason)``. Everything that would let a container escape
to the host is refused; the locked-down shape that
``app.self_improvement.evolver_spawn.build_create_payload`` produces (and the
ollama fleet's create) passes.

Default-deny posture: an unrecognised image, or a ``HostConfig`` carrying any
host-access field, is refused. Adding a legitimately-needed image is an explicit
operator action via ``DOCKER_BROKER_IMAGE_ALLOWLIST`` — never an accident.
"""
from __future__ import annotations

import os

# Repository prefixes the gateway legitimately spawns containers from. Matched
# on a tag/digest boundary (NOT a loose prefix) so a look-alike repo like
# ``botarmy-evolver-evil/x`` cannot ride in. Override (comma-separated) with
# ``DOCKER_BROKER_IMAGE_ALLOWLIST`` — keep it in sync with every image the
# system actually creates containers from:
#   botarmy-evolver  — verified mutation engine / research / pdf sandbox
#   ollama/ollama    — the local model fleet
_DEFAULT_IMAGE_ALLOWLIST = ("botarmy-evolver", "ollama/ollama")

# HostConfig keys that grant host access / break isolation — refused when
# present-and-truthy (the legitimate create payloads never set them).
_FORBIDDEN_TRUTHY_KEYS = (
    "Binds",             # bind mounts → host filesystem
    "Mounts",            # bind/volume mounts → host filesystem
    "Devices",           # host device passthrough
    "DeviceRequests",    # GPU / device passthrough
    "DeviceCgroupRules",
    "CapAdd",            # added Linux capabilities
    "Sysctls",           # kernel-parameter writes
)

# Namespace-mode keys: "host" (or "container:<id>") shares a host/peer namespace
# and is an escape. Empty / private values are fine.
_NS_MODE_KEYS = ("PidMode", "UsernsMode", "CgroupnsMode", "UTSMode")

_IPC_ALLOWED = ("private", "none", "shareable")


def image_allowlist() -> tuple[str, ...]:
    """Effective image allowlist (env override → default)."""
    raw = os.environ.get("DOCKER_BROKER_IMAGE_ALLOWLIST", "").strip()
    if not raw:
        return _DEFAULT_IMAGE_ALLOWLIST
    items = tuple(p.strip() for p in raw.split(",") if p.strip())
    return items or _DEFAULT_IMAGE_ALLOWLIST


def _image_allowed(image: str, allow: tuple[str, ...]) -> bool:
    if not image or not isinstance(image, str):
        return False
    # Boundary match: exact repo, or repo followed by a tag (":") or digest ("@").
    # NOT a loose startswith — "botarmy-evolver-evil" must not match "botarmy-evolver".
    return any(
        image == a or image.startswith(a + ":") or image.startswith(a + "@")
        for a in allow
    )


def _is_host_or_container_mode(value) -> bool:
    if not isinstance(value, str):
        return False
    low = value.strip().lower()
    return low == "host" or low.startswith("container:")


def validate_create_body(body) -> tuple[bool, str]:
    """Return ``(allowed, reason)`` for a Docker create body.

    Refuses anything that could reach the host filesystem, escalate privilege,
    share a host namespace, drop the seccomp/apparmor confinement, or run an
    image outside the allowlist. Otherwise allows.
    """
    if not isinstance(body, dict):
        return False, "create body is not a JSON object"

    allow = image_allowlist()
    image = body.get("Image")
    if not _image_allowed(image if isinstance(image, str) else "", allow):
        return False, f"image {image!r} not in allowlist {allow}"

    hc = body.get("HostConfig")
    if hc is None:
        hc = {}
    if not isinstance(hc, dict):
        return False, "HostConfig is not an object"

    if hc.get("Privileged"):
        return False, "HostConfig.Privileged is forbidden"

    for key in _FORBIDDEN_TRUTHY_KEYS:
        if hc.get(key):
            return False, f"HostConfig.{key} is forbidden"

    for key in _NS_MODE_KEYS:
        if _is_host_or_container_mode(hc.get(key)):
            return False, f"HostConfig.{key}={hc.get(key)!r} (host/container namespace) is forbidden"

    nm = hc.get("NetworkMode")
    if _is_host_or_container_mode(nm):
        return False, f"HostConfig.NetworkMode={nm!r} (host/container network) is forbidden"

    ipc = hc.get("IpcMode")
    if isinstance(ipc, str) and ipc.strip() and ipc.strip().lower() not in _IPC_ALLOWED:
        return False, f"HostConfig.IpcMode={ipc!r} is forbidden (only {_IPC_ALLOWED})"

    secopt = hc.get("SecurityOpt") or []
    if isinstance(secopt, list):
        for entry in secopt:
            if isinstance(entry, str) and "unconfined" in entry.lower():
                return False, f"HostConfig.SecurityOpt {entry!r} (unconfined) is forbidden"

    return True, "ok"
