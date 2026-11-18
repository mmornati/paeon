"""Signed links: authentic, in time, and nothing else."""

from __future__ import annotations

from datetime import timedelta

import pytest

from paeon.links import LinkError, Signer
from support import T0

KEY = "k" * 40


def test_a_link_carries_what_it_was_minted_with() -> None:
    signer = Signer(KEY)
    token = signer.mint("go", expires_at=T0 + timedelta(minutes=30), p=7, h="abc")
    payload = signer.read(token, T0)
    assert (payload["k"], payload["p"], payload["h"]) == ("go", 7, "abc")
    assert signer.mint("go", expires_at=T0, p=7) != signer.mint("go", expires_at=T0, p=7)


@pytest.mark.parametrize("change", ["payload", "signature", "junk", "empty", "other key"])
def test_a_forged_link_is_refused(change: str) -> None:
    signer = Signer(KEY)
    token = signer.mint("go", expires_at=T0 + timedelta(minutes=30), p=7)
    body, _, signature = token.partition(".")
    forged = {
        "payload": f"{body[:-2]}xx.{signature}",
        "signature": f"{body}.{signature[:-2]}xx",
        "junk": "not a link",
        "empty": "",
        "other key": Signer("z" * 40).mint("go", expires_at=T0 + timedelta(minutes=30), p=7),
    }[change]
    with pytest.raises(LinkError, match="not valid"):
        signer.read(forged, T0)


def test_an_expired_link_is_refused_and_a_short_key_too() -> None:
    signer = Signer(KEY)
    token = signer.mint("go", expires_at=T0 + timedelta(minutes=30))
    with pytest.raises(LinkError, match="expired"):
        signer.read(token, T0 + timedelta(minutes=31))
    with pytest.raises(ValueError, match="32 characters"):
        Signer("short")
