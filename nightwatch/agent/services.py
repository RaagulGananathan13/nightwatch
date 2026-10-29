"""The service catalogue (``nightwatch/services.yaml``): where the agent may look, per service."""

from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

CATALOGUE = Path(__file__).resolve().parents[1] / "services.yaml"


class ServiceInfo(BaseModel):
    name: str
    description: str = ""
    owner: str = ""
    repository: str = ""
    lambda_functions: list[str] = Field(default_factory=list)
    lambda_alias: str = "live"
    log_groups: list[str] = Field(default_factory=list)
    dynamodb_tables: list[str] = Field(default_factory=list)
    ssm_parameters: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    deploys: str = ""
    runbooks: list[str] = Field(default_factory=list)

    @property
    def resources(self) -> set[str]:
        return {*self.lambda_functions, *self.dynamodb_tables}


@cache
def load_services(path: Path = CATALOGUE) -> dict[str, ServiceInfo]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {
        name: ServiceInfo.model_validate({"name": name, **(body or {})})
        for name, body in (raw.get("services") or {}).items()
    }


def service_info(name: str) -> ServiceInfo:
    """The catalogue entry, or an empty one (the agent then has nothing extra to read)."""
    return load_services().get(name) or ServiceInfo(name=name)
