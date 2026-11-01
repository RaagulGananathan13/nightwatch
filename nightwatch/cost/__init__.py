"""The cost guardian (spec F17): cost spikes become incidents, investigated like any other."""

PREFIX = "cost-guard:"  # the alarm names NightWatch makes up for cost signals


def is_cost_alarm(name: str) -> bool:
    return name.startswith(PREFIX)
