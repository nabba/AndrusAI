import stat

from scripts.provision_memory_platform_secrets import SECRET_KEYS, provision


def _values(path):
    result = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            result[key] = value
    return result


def test_provision_adds_distinct_secrets_without_overwriting(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text("EXISTING=keep-me\n")
    changed = provision(env)
    values = _values(env)
    assert changed == SECRET_KEYS
    assert values["EXISTING"] == "keep-me"
    assert all(len(values[key]) >= 48 for key in SECRET_KEYS)
    assert len({values[key] for key in SECRET_KEYS}) == 2
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_provision_is_idempotent(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text("BASE=x\n")
    provision(env)
    first = env.read_text()
    assert provision(env) == ()
    assert env.read_text() == first
