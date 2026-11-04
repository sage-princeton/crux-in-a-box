"""Resolve Slack steering in a fresh staged drop-in before launch."""

import argparse
import re
from pathlib import Path


def prepare(root, channel, operator, after_ts):
    """Validate Slack identifiers and resolve all placeholders without retargeting a run."""
    for value, pattern, name in ((channel, r"[CG][A-Z0-9]+", "channel"),
                                 (operator, r"U[A-Z0-9]+", "operator"),
                                 (after_ts, r"\d+(?:\.\d+)?", "after_ts")):
        if not re.fullmatch(pattern, value):
            raise ValueError(f"Invalid {name}")
    config = root / "scaffold.toml"
    text = config.read_text()
    for key, value in {"SLACK_CHANNEL_ID": channel, "SLACK_OPERATOR_ID": operator,
                       "SLACK_AFTER_TS": after_ts}.items():
        placeholder = "{{" + key + "}}"
        if placeholder not in text:
            raise ValueError("Drop-in is already resolved; use a fresh staged copy for a new run")
        text = text.replace(placeholder, value)
    config.write_text(text)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", required=True)
    parser.add_argument("--operator", required=True)
    parser.add_argument("--after-ts", required=True)
    args = parser.parse_args()
    prepare(Path(__file__).resolve().parent, args.channel, args.operator, args.after_ts)
    print("Slack steering resolved. Run scaffold check before starting.")
