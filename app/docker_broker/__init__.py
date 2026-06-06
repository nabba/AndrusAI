"""docker_broker — a thin, unprivileged HTTP body-filter for the Docker API.

Closes finding S2 from the 2026-06-06 whole-system review: the
tecnativa ``docker-socket-proxy`` gates which Docker API *paths* are reachable
but cannot inspect request *bodies*. With ``CONTAINERS``+``POST`` enabled
(needed to spawn the evolver + ollama-fleet containers), anything that reaches
``POST /containers/create`` can supply an arbitrary ``HostConfig`` —
``{"Binds":["/:/host"],"Privileged":true,"PidMode":"host"}`` — and own the host.

This broker inserts a body-validation hop:

    gateway → docker-broker → docker-proxy → /var/run/docker.sock

It forwards every request to the upstream proxy UNCHANGED except
``POST /containers/create``, whose body must pass :func:`policy.validate_create_body`.
It holds NO docker socket and runs unprivileged, so a bug in it fails CLOSED
(the gateway just can't reach Docker) rather than opening a new escape.

Activation is opt-in (compose profile ``docker-broker`` + ``GATEWAY_DOCKER_HOST``);
default deployments are unaffected. See ``docs/DOCKER_BROKER.md``.
"""
from __future__ import annotations

from app.docker_broker.policy import validate_create_body  # noqa: F401

__all__ = ["validate_create_body"]
