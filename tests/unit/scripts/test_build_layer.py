"""The layer zip holds only runtime files, and is rebuilt only when requirements change."""

import zipfile
from pathlib import Path

import pytest

from scripts import build_layer


def fake_install(requirements: Path, target: Path) -> None:
    files = {
        "pydantic/__init__.py": "VERSION = '2'",
        "pydantic-2.14.1.dist-info/METADATA": "Name: pydantic",
        "pydantic-2.14.1.dist-info/RECORD": "differs between pip versions",
        "pydantic-2.14.1.dist-info/INSTALLER": "pip",
        "pydantic/__pycache__/x.cpython-313.pyc": "bytecode",
    }
    for name, text in files.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


@pytest.fixture
def requirements(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(build_layer, "install", fake_install)
    path = tmp_path / "requirements-lambda.txt"
    path.write_text("pydantic==2.14.1\n")
    return path


def test_layer_keeps_runtime_files_under_python(requirements: Path, tmp_path: Path) -> None:
    out = tmp_path / "layer.zip"
    build_layer.build(requirements, out)
    with zipfile.ZipFile(out) as archive:
        names = archive.namelist()
    assert names == [
        "python/pydantic-2.14.1.dist-info/METADATA",
        "python/pydantic/__init__.py",
    ]


def test_unchanged_requirements_skip_the_download(requirements: Path, tmp_path: Path) -> None:
    out = tmp_path / "layer.zip"
    first = build_layer.build(requirements, out)
    assert build_layer.build(requirements, out) == "unchanged"
    assert build_layer.build(requirements, out, force=True) == first
    requirements.write_text("pydantic==2.15.0\n")
    assert build_layer.build(requirements, out) != "unchanged"
