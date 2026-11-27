"""Kortlevende, instantiegebonden JWS voor de alleen-lezen Hub-koppeling."""

import base64
import hashlib
from datetime import UTC, datetime
from typing import Literal

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import BaseModel

from mso.change_preview import canonical_json
from mso.config import VERSION, get_settings

PATH = "/api/v1/machine-keys/read-proof"


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


class VerificationKey(BaseModel):
    kty: Literal["OKP"] = "OKP"
    crv: Literal["Ed25519"] = "Ed25519"
    x: str
    kid: str


class ReadProof(BaseModel):
    proof: str


def verification_key(signer: Ed25519PrivateKey) -> VerificationKey:
    public = signer.public_key().public_bytes_raw()
    return VerificationKey(x=_encode(public), kid="sha256:" + hashlib.sha256(public).hexdigest())


def issue(signer: Ed25519PrivateKey, key_id: str, key_hash: str) -> ReadProof:
    kid = verification_key(signer).kid
    version = get_settings().build.version
    now = int(datetime.now(UTC).timestamp())
    header = {"alg": "EdDSA", "typ": "mso-read-proof+jwt", "kid": kid}
    claims = {
        "iss": "urn:mso:" + kid, "aud": "hub", "sub": key_id, "key_sha256": key_hash,
        "scopes": ["read"], "iat": now, "exp": now + 60,
        "source_version": VERSION if version == "onbekend" else version,
    }
    signed = _encode(canonical_json(header)) + "." + _encode(canonical_json(claims))
    return ReadProof(proof=signed + "." + _encode(signer.sign(signed.encode("ascii"))))
