"""Render the experiment's checked-in Markdown instructions."""
import re
from pathlib import Path

PROMPTS = Path(__file__).resolve().parent / "prompts"


def render(name: str, **values: object) -> str:
    """Substitute named fields once, preserving literal text supplied by an agent.

    :param name: Trusted template basename under the experiment's prompts directory.
    :param values: Values for the template's angle-bracketed fields.
    :returns: Rendered Markdown with its terminating newline removed.
    :raises KeyError: A required field was omitted.
    """
    template = (PROMPTS / name).read_text().rstrip()
    return re.sub(r"<([a-z][a-z0-9-]*)>", lambda match: str(values[match[1]]), template)
