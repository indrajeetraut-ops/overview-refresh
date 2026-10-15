"""The App JWT carries the claims GitHub requires (git-source.md §7.3): RS256, iss=client_id,
exp within 10 minutes, iat backdated for clock drift. Verified against a throwaway RSA key so
the test needs no real GitHub credentials."""
from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from server.ingest.git.client import GitHubClient
from server.ingest.git.config import GithubConfig


@pytest.fixture
def keypair(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path = tmp_path / "key.pem"
    path.write_bytes(pem)
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return str(path), public_pem


def test_jwt_claims_and_signature(keypair):
    key_path, public_pem = keypair
    config = GithubConfig(client_id="Iv1.abc123", private_key_path=key_path)
    client = GitHubClient(config)

    token = client._app_jwt()
    decoded = jwt.decode(token, public_pem, algorithms=["RS256"],
                         options={"verify_exp": True})

    assert decoded["iss"] == "Iv1.abc123"
    now = int(time.time())
    assert decoded["iat"] <= now                 # backdated, never in the future
    assert decoded["exp"] <= now + 600           # GitHub's 10-minute hard cap
    assert decoded["exp"] > now                   # still valid


def test_jwt_prefers_client_id_over_app_id(keypair):
    key_path, _ = keypair
    config = GithubConfig(app_id="123456", client_id="Iv1.xyz", private_key_path=key_path)
    client = GitHubClient(config)
    token = client._app_jwt()
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["iss"] == "Iv1.xyz"


def test_jwt_falls_back_to_app_id(keypair):
    key_path, _ = keypair
    config = GithubConfig(app_id="123456", client_id="", private_key_path=key_path)
    client = GitHubClient(config)
    token = client._app_jwt()
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["iss"] == "123456"
