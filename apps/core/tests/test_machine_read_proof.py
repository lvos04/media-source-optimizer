"""Alleen MSO kan voor de aangeboden leessleutel van deze installatie een kort bewijs afgeven."""

import base64
import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import NonCallableMock

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from test_machine_keys import create, revoke

PROOF = "/api/v1/machine-keys/read-proof"
PIN = "/api/v1/machine-keys/verification-key"


def decode(segment):
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def test_twee_installaties_hebben_elk_een_vaste_handtekeningssleutel(tmp_path, monkeypatch):
    from mso import vault
    from mso.read_proof import verification_key

    def open_installatie(naam):
        bestand = tmp_path / naam / "vault.key"
        monkeypatch.setattr(vault, "get_settings", lambda: SimpleNamespace(secret_key_file=bestand))
        return vault.Vault(NonCallableMock(spec_set=[])).read_proof_signer

    eerste = open_installatie("eerste")
    handtekening = eerste.sign(b"proef")
    herstart = open_installatie("eerste")
    assert verification_key(eerste) == verification_key(herstart)
    herstart.public_key().verify(handtekening, b"proef")
    tweede = open_installatie("tweede")
    assert verification_key(eerste) != verification_key(tweede)
    with pytest.raises(InvalidSignature):
        tweede.public_key().verify(handtekening, b"proef")


def test_verklaring_bindt_instantie_sleutel_versie_en_geldigheid(admin, client):
    from mso.config import VERSION, get_settings

    jwk_response = admin.get(PIN)
    assert jwk_response.status_code == 200
    jwk = jwk_response.json()
    assert set(jwk) == {"kty", "crv", "x", "kid"}
    assert (jwk["kty"], jwk["crv"]) == ("OKP", "Ed25519")
    public = decode(jwk["x"])
    assert jwk["kid"] == "sha256:" + hashlib.sha256(public).hexdigest()
    key = Ed25519PublicKey.from_public_bytes(public)
    proofs = []
    for name in ("Eerste lezer", "Tweede lezer"):
        made = create(admin, name=name)
        response = client.get(PROOF, headers={"X-Api-Key": made["key"]})
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        header, claims, signature = response.json()["proof"].split(".")
        assert json.loads(decode(header)) == {"alg": "EdDSA", "kid": jwk["kid"], "typ": "mso-read-proof+jwt"}
        signed = (header + "." + claims).encode("ascii")
        key.verify(decode(signature), signed)
        with pytest.raises(InvalidSignature):
            Ed25519PrivateKey.generate().public_key().verify(decode(signature), signed)
        payload = json.loads(decode(claims))
        assert payload["iss"] == "urn:mso:" + jwk["kid"]
        assert payload["aud"] == "hub" and payload["scopes"] == ["read"]
        assert payload["sub"] == made["item"]["id"]
        assert payload["key_sha256"] == hashlib.sha256(made["key"].encode()).hexdigest()
        source = get_settings().build.version
        assert payload["source_version"] == (VERSION if source == "onbekend" else source)
        assert payload["iat"] <= datetime.now(UTC).timestamp() < payload["exp"]
        assert payload["exp"] - payload["iat"] == 60
        assert made["key"].encode() not in decode(claims)
        proofs.append((signed, decode(signature)))
        assert revoke(admin, made["item"]["id"]).is_success
        assert client.get(PROOF, headers={"X-Api-Key": made["key"]}).status_code == 401
    with pytest.raises(InvalidSignature):
        key.verify(proofs[0][1], proofs[1][0])


def test_weiger_sessies_onbevestigde_pins_en_extra_rechten(admin, operator, viewer, client, db, database_bytes):
    assert client.get(PROOF).status_code == 401
    read = create(admin)
    for session in (admin, operator, viewer):
        assert session.get(PROOF).status_code == 403
        assert session.get(PROOF, headers={"X-Api-Key": read["key"]}).status_code == 403
    for caller in (operator, viewer):
        assert caller.get(PIN).status_code == 403
    assert client.get(PIN, headers={"X-Api-Key": read["key"]}).status_code == 403
    for scopes in (["read", "changes:approve"], ["changes:approve"]):
        made = create(admin, scopes, name=" ".join(scopes))
        before = database_bytes(db)
        assert client.get(PROOF, headers={"X-Api-Key": made["key"]}).status_code == 403
        assert database_bytes(db) == before
