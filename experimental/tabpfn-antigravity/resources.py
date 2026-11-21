"""Scope optional GPU and checkpoint credentials to the research process."""
import os

OPTIONAL = ("RUNPOD_API_KEY", "HF_TOKEN", "PRIOR_LABS_API_KEY")


def research_resources() -> dict[str, str]:
    """Select credentials and the run ownership marker for research or reproduction.

    :returns: Configured research resources with the Prior Labs client's token alias.
    """
    values = {name: os.environ[name] for name in (*OPTIONAL, "RUN_SLUG") if os.environ.get(name)}
    if "PRIOR_LABS_API_KEY" in values:
        values["TABPFN_TOKEN"] = values["PRIOR_LABS_API_KEY"]
    return values
