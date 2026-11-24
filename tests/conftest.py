import pytest


@pytest.fixture
def data_files(tmp_path, monkeypatch):
    """Create the three data files and point their env vars at them."""
    paths = {}
    for var, name in [
        ("SCOPE_ROUTING_FILE", "routing.yaml"),
        ("SCOPE_DEPENDENCY_HINTS_FILE", "dependency_hints.yaml"),
        ("SCOPE_AREAS_FILE", "areas.yaml"),
    ]:
        p = tmp_path / name
        p.write_text("{}\n")
        monkeypatch.setenv(var, str(p))
        paths[var] = p
    return paths
