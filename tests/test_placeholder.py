"""Placeholder test: the suite (and therefore CI) passes on an empty project."""

import t


def test_package_importable() -> None:
    assert isinstance(t.__doc__, str)
