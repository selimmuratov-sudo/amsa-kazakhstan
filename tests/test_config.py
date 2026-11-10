import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.config import Settings


def test_development_settings_keep_local_defaults():
    settings = Settings(_env_file=None)
    assert settings.is_production is False
    assert settings.host_list == ["localhost", "127.0.0.1", "testserver"]


def test_production_requires_secure_and_deployment_specific_secrets():
    with pytest.raises(ValidationError, match="Invalid production configuration"):
        Settings(environment="production", _env_file=None)

    settings = Settings(
        environment="production",
        database_url="postgresql+psycopg://amsa:secret@db.internal/amsa",
        cookie_secure=True,
        allowed_hosts="amsa.example.org,portal.amsa.example.org",
        auth_rate_limit_pepper="random-deployment-secret-at-least-32-characters",
        backup_encryption_key=Fernet.generate_key().decode(),
        private_files_encryption_key=Fernet.generate_key().decode(),
        _env_file=None,
    )
    assert settings.is_production


@pytest.mark.parametrize("hosts", ["*", "localhost", "127.0.0.1", "testserver"])
def test_production_rejects_non_deployment_hosts(hosts):
    with pytest.raises(ValidationError, match="AMSA_ALLOWED_HOSTS"):
        Settings(
            environment="production",
            database_url="postgresql://db/amsa",
            cookie_secure=True,
            allowed_hosts=hosts,
            auth_rate_limit_pepper="random-deployment-secret-at-least-32-characters",
            backup_encryption_key=Fernet.generate_key().decode(),
            private_files_encryption_key=Fernet.generate_key().decode(),
            _env_file=None,
        )
