"""The service catalogue (``nightwatch/services.yaml``): where the agent may look, per service."""

from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

CATALOGUE = Path(__file__).resolve().parents[1] / "services.yaml"


class Account(BaseModel):
    """Another AWS account the service runs in, read through the role the connect template
    made there (spec section 13). Connected accounts are only read: no fix runs in them."""

    role_arn: str = Field(pattern=r"^arn:aws:iam::\d{12}:role/NightWatchReadOnly-[\w+=,.@-]+$")
    external_id_param: str = Field(pattern=r"^/nightwatch/tenants/[\w.-]+/external_id$")
    region: str = Field(default="us-east-2", pattern=r"^[a-z]{2}(-[a-z]+)+-\d$")
    llm_key_param: str = Field(default="", pattern=r"^(/nightwatch/llm/[\w./-]+)?$")  # their own


class Knob(BaseModel):
    """One Terraform setting a fix PR may raise (spec F10): where it is, and its ceiling."""

    file: str = Field(pattern=r"^infra/[\w./-]+\.tf$")
    block: str  # the top-level block's first line, e.g. 'variable "read_capacity"'
    attribute: str = Field(pattern=r"^\w+$")
    max: int = Field(gt=0)


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
    runbooks: list[str] = Field(default_factory=list)  # fixes NightWatch may propose
    approvers: list[str] = Field(default_factory=list)  # Discord user ids who may approve them
    slack_approvers: list[str] = Field(default_factory=list)  # Slack member ids, the same role
    account: Account | None = None  # a connected account; None: NightWatch's own (section 13)
    terraform: dict[str, Knob] = Field(default_factory=dict)  # settings a fix PR may raise
    slo: float | None = Field(default=None, gt=0, lt=100)  # availability target, percent (F12)
    public_name: str = ""  # what customers call it, on the status page (F13)

    def can_approve(self, user_id: str) -> bool:
        """Is this Discord or Slack user one of the service's approvers?"""
        return bool(user_id) and user_id in (*self.approvers, *self.slack_approvers)

    @property
    def resources(self) -> set[str]:
        return {*self.lambda_functions, *self.dynamodb_tables}

    @property
    def function(self) -> str:
        """The service's main Lambda function (the first one listed)."""
        return self.lambda_functions[0] if self.lambda_functions else ""


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
