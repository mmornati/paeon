"""Running the few programs Paeon runs (`git`, `ssh`): a fixed argument list, never a shell."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping, Sequence

Runner = Callable[..., tuple[int, str]]


def run(
    argv: Sequence[str],
    *,
    timeout: float = 20,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[int, str]:
    """Exit code and output (stdout, then stderr). Raises OSError or TimeoutExpired."""
    done = subprocess.run(  # noqa: S603 - a list, no shell; callers build it from configuration
        list(argv),
        capture_output=True,
        timeout=timeout,
        cwd=cwd,
        env={**os.environ, **(env or {})},
        stdin=subprocess.DEVNULL,
        check=False,
    )
    text = done.stdout.decode("utf-8", errors="replace")
    if done.stderr:
        text += done.stderr.decode("utf-8", errors="replace")
    return done.returncode, text


# A repository is data: its own configuration must not be able to run anything.
GIT = (
    "git",
    "--no-pager",
    "-c", "core.fsmonitor=false",
    "-c", "core.hooksPath=/dev/null",
    "-c", "safe.directory=*",
    "-c", "protocol.ext.allow=never",
    # Signatures are checked and made by a program the repository's own config can name.
    "-c", "log.showSignature=false",
    "-c", "commit.gpgSign=false",
    "-c", "tag.gpgSign=false",
    "-c", "gpg.program=false",
)  # fmt: skip
GIT_ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}


def ssh_command(identity_file: str | None, known_hosts_file: str | None) -> list[str]:
    """`ssh` that never asks, never forwards, and trusts only the host keys it was given."""
    argv = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "ForwardAgent=no",
        "-o", "ClearAllForwardings=yes",
        "-o", "ConnectTimeout=10",
        "-o", "IdentitiesOnly=yes",
    ]  # fmt: skip
    if identity_file:
        argv += ["-i", identity_file]
    if known_hosts_file:
        argv += ["-o", f"UserKnownHostsFile={known_hosts_file}"]
    return argv
