#!/usr/bin/env python3
"""Provision missing memory-platform secrets without printing their values."""

from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path
from typing import Sequence

SECRET_KEYS = (
    "DURABLE_MEMORY_POSTGRES_PASSWORD",
    "GOVERNANCE_POSTGRES_PASSWORD",
)


def provision(env_file: Path) -> tuple[str, ...]:
    """Atomically add missing keys, preserving every existing line and value."""

    if not env_file.is_file():
        raise FileNotFoundError(f"environment file not found: {env_file}")
    original = env_file.read_text(encoding="utf-8")
    existing: dict[str, str] = {}
    for line in original.splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        existing[key.strip()] = value.strip()

    missing = tuple(key for key in SECRET_KEYS if not existing.get(key))
    if not missing:
        return ()
    generated = {key: secrets.token_urlsafe(48) for key in missing}
    if len(set(generated.values())) != len(generated):
        raise RuntimeError("secret generator returned duplicate values")

    separator = "" if original.endswith("\n") else "\n"
    addition = "\n# Durable memory platform (generated locally; never commit .env)\n"
    addition += "".join(f"{key}={generated[key]}\n" for key in missing)
    temporary = env_file.with_suffix(env_file.suffix + ".memory-platform.tmp")
    temporary.write_text(original + separator + addition, encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(env_file)
    os.chmod(env_file, 0o600)
    return missing


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args(argv)
    changed = provision(args.env_file.resolve())
    if changed:
        print("provisioned keys: " + ", ".join(changed))
    else:
        print("memory-platform secrets already provisioned; no changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
