import pytest

from scope_agent.persistence.database import Database


@pytest.fixture
def db():
    d = Database("sqlite://")
    d.create_all()
    return d
