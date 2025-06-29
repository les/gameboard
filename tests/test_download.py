"""Test download functions."""

import xml.etree.ElementTree as ET

import pytest

from scripts import download


@pytest.mark.parametrize(
    ("data", "expected_tag", "expected_exception"),
    [
        (b"<match></match>", "match", None),
        (b"<match></match>", "mismatch", ValueError),
        (b"<match/><match>", "malformed", ET.ParseError),
        (b"", "empty", ET.ParseError),
    ],
)
def test_inspect_data(
    data: bytes, expected_tag: str, expected_exception: type[Exception] | None
) -> None:
    """Test inspect_data."""
    if expected_exception:
        with pytest.raises(expected_exception):
            download.inspect_data(data, expected_tag)
    else:
        root = download.inspect_data(data, expected_tag)
        assert root.tag == expected_tag
