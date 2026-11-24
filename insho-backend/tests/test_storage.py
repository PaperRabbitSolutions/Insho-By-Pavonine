from io import BytesIO

import pytest

from app.services.storage import (
    InvalidStorageKey,
    LocalStorageProvider,
    ObjectAlreadyExists,
    ObjectNotFound,
    StorageError,
)


def test_local_storage_store_open_metadata_exists_and_delete(tmp_path):
    provider = LocalStorageProvider(tmp_path / "private")
    key = "a" * 32
    metadata = {"original_filename": "design.jpg", "size": 4}

    provider.store(key, BytesIO(b"data"), metadata)

    assert provider.exists(key)
    assert provider.get_metadata(key) == metadata
    with provider.open(key) as source:
        assert source.read() == b"data"
    provider.delete(key)
    assert not provider.exists(key)
    with pytest.raises(ObjectNotFound):
        provider.open(key)


def test_local_storage_refuses_collisions_without_overwriting(tmp_path):
    provider = LocalStorageProvider(tmp_path / "private")
    key = "b" * 32
    provider.store(key, BytesIO(b"first"), {"size": 5})

    with pytest.raises(ObjectAlreadyExists):
        provider.store(key, BytesIO(b"second"), {"size": 6})

    with provider.open(key) as source:
        assert source.read() == b"first"
    assert provider.get_metadata(key) == {"size": 5}


@pytest.mark.parametrize("key", ["", "../secret", "a/b", "/" + "a" * 32, "G" * 32, "a" * 31])
def test_local_storage_rejects_invalid_keys(tmp_path, key):
    provider = LocalStorageProvider(tmp_path / "private")
    with pytest.raises(InvalidStorageKey):
        provider.exists(key)


def test_local_storage_cleans_up_partial_write(tmp_path):
    class BrokenReader:
        def __init__(self):
            self.calls = 0

        def read(self, _size):
            self.calls += 1
            if self.calls == 1:
                return b"partial"
            raise OSError("simulated disk/network read failure")

    provider = LocalStorageProvider(tmp_path / "private")
    key = "c" * 32
    with pytest.raises(StorageError):
        provider.store(key, BrokenReader(), {"size": 7})
    assert not provider.exists(key)
