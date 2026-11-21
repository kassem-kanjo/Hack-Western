import importlib

import pytest

SUBPACKAGES = ["darkfleet.core", "darkfleet.ingest", "darkfleet.store", "darkfleet.api", "darkfleet.edge"]


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_imports(name: str) -> None:
    module = importlib.import_module(name)
    assert module.__name__ == name


def test_package_imports() -> None:
    import darkfleet

    assert darkfleet.__name__ == "darkfleet"
