"""Ephemeral wrapping material only: never write PEMs or actual provider keys."""

from uuid import uuid4

import pytest
import sqlalchemy as sa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from labelloop_credentials.envelope import PublicSealer, SecretType
from labelloop_credentials.store import AuthorizedScope, CredentialStore, metadata


@pytest.fixture(scope="session")
def key_material():
    result = {}
    for kind in SecretType:
        private = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        result[kind] = (
            private.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
            private.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            ),
        )
    return result


@pytest.fixture
def scope():
    return AuthorizedScope(uuid4(), uuid4(), SecretType.PLATFORM)


@pytest.fixture
def sealer(scope, key_material):
    return PublicSealer(scope.secret_type, key_material[scope.secret_type][1])


@pytest.fixture
def db(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'disposable.db'}", hide_parameters=True)
    metadata.create_all(engine)
    with engine.begin() as connection:
        yield connection
    engine.dispose()


@pytest.fixture
def store(db):
    return CredentialStore(db)
