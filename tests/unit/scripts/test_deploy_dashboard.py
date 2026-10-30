"""Dashboard deploys: hashed assets cache for a year, entry files never; stale files go."""

from pathlib import Path
from typing import Any

import pytest

from scripts.deploy_dashboard import deploy, plan


class FakeS3:
    def __init__(self, existing: list[str]) -> None:
        self.objects: dict[str, dict[str, Any]] = {key: {} for key in existing}
        self.deleted: list[str] = []

    def put_object(self, **kwargs: Any) -> None:
        self.objects[kwargs["Key"]] = kwargs

    def delete_object(self, **kwargs: Any) -> None:
        self.deleted.append(kwargs["Key"])

    def get_paginator(self, name: str) -> Any:
        keys = list(self.objects)

        class Pages:
            def paginate(self, **kwargs: Any) -> list[dict[str, Any]]:
                return [{"Contents": [{"Key": key} for key in keys]}]

        return Pages()


class FakeCloudFront:
    def __init__(self) -> None:
        self.paths: list[str] = []

    def create_invalidation(self, **kwargs: Any) -> None:
        self.paths = kwargs["InvalidationBatch"]["Paths"]["Items"]


def built(tmp_path: Path) -> Path:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html></html>")
    (tmp_path / "assets" / "index-abc.js").write_text("x")
    (tmp_path / "eval-results.json").write_text("{}")
    return tmp_path


def test_cache_rules_and_content_types(tmp_path: Path) -> None:
    files = plan(built(tmp_path))
    assert files["assets/index-abc.js"][1:] == (
        "text/javascript; charset=utf-8",
        "public, max-age=31536000, immutable",
    )
    assert files["index.html"][1:] == ("text/html; charset=utf-8", "no-cache")
    assert files["eval-results.json"][2] == "no-cache"


def test_deploy_removes_stale_files_but_keeps_config_and_the_status_page(tmp_path: Path) -> None:
    s3 = FakeS3(["config.json", "assets/index-old.js", "status/index.html"])
    cloudfront = FakeCloudFront()
    stale = deploy(s3, cloudfront, "bucket", "DIST", built(tmp_path))
    assert stale == ["assets/index-old.js"]
    assert "config.json" not in s3.deleted
    assert cloudfront.paths == ["/", "/eval-results.json", "/index.html"]
    with pytest.raises(SystemExit, match=r"no index.html"):
        deploy(s3, cloudfront, "bucket", "DIST", tmp_path / "assets")
