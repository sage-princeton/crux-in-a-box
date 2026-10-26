"""Resolve a staged drop-in and install the pinned official paper style, before launch."""

import argparse
import hashlib
import io
import re
import urllib.request
import zipfile
from pathlib import Path

TEMPLATE_URL = "https://media.neurips.cc/Conferences/NeurIPS2025/Styles.zip"
TEMPLATE_SHA256 = "72032130232955b785cd622b7c52d893e3166f30a90e1e6279d0a28e8373beef"


def prepare(root, channel, operator, after_ts, archive):
    for value, pattern, name in ((channel, r"[CG][A-Z0-9]+", "channel"),
                                  (operator, r"U[A-Z0-9]+", "operator"),
                                  (after_ts, r"\d+(?:\.\d+)?", "after_ts")):
        if not re.fullmatch(pattern, value):
            raise ValueError(f"Invalid {name}")
    if hashlib.sha256(archive).hexdigest() != TEMPLATE_SHA256:
        raise ValueError("Venue template checksum mismatch; verify the upstream archive before changing the pin")
    with zipfile.ZipFile(io.BytesIO(archive)) as template:
        style = template.read("Styles/neurips_2025.sty")
    config = root / "scaffold.toml"
    text = config.read_text()
    for key, value in {"SLACK_CHANNEL_ID": channel, "SLACK_OPERATOR_ID": operator,
                       "SLACK_AFTER_TS": after_ts}.items():
        if "{{" + key + "}}" not in text:
            raise ValueError("Drop-in is already resolved; use a fresh staged copy for a new run")
        text = text.replace("{{" + key + "}}", value)
    templates = root / "workspace/templates"
    templates.mkdir(exist_ok=True)
    (templates / "paper_template.zip").write_bytes(archive)
    (root / "workspace/paper/neurips_2025.sty").write_bytes(style)
    config.write_text(text)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", required=True)
    parser.add_argument("--operator", required=True)
    parser.add_argument("--after-ts", required=True)
    parser.add_argument("--template", type=Path, help="Use a previously downloaded, checksum-verified archive")
    args = parser.parse_args()
    if args.template:
        archive = args.template.read_bytes()
    else:
        with urllib.request.urlopen(TEMPLATE_URL, timeout=60) as response:
            archive = response.read(2_000_000)
    prepare(Path(__file__).resolve().parent, args.channel, args.operator, args.after_ts, archive)
    print("Drop-in resolved and pinned venue template installed. Run scaffold check before starting.")
