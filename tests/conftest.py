import pytest


@pytest.fixture
def tmp_snapshot_dir(tmp_path):
    """A writable per-test directory for state.save_all / SyncHistory."""
    return str(tmp_path)
