"""Resolve a staged drop-in and install the bundled, pinned NeurIPS 2026 paper style, before launch."""

import argparse
import hashlib
import io
import re
import zipfile
from pathlib import Path

# The NeurIPS 2026 formatting-instructions archive (neurips_2026.sty, neurips_2026.tex, checklist.tex), bundled.
TEMPLATE = Path(__file__).resolve().with_name("neurips_2026.zip")
TEMPLATE_SHA256 = "6d06b8938bd88f56986f2858e90f6a98b9eeb1f29f613ca8ae708d0be553318a"


def prepare(root, channel, operator, after_ts, archive):
    for value, pattern, name in ((channel, r"[CG][A-Z0-9]+", "channel"),
                                  (operator, r"U[A-Z0-9]+", "operator"),
                                  (after_ts, r"\d+(?:\.\d+)?", "after_ts")):
        if not re.fullmatch(pattern, value):
            raise ValueError(f"Invalid {name}")
    if hashlib.sha256(archive).hexdigest() != TEMPLATE_SHA256:
        raise ValueError("Venue template checksum mismatch; verify the archive before changing the pin")
    with zipfile.ZipFile(io.BytesIO(archive)) as template:
        style = template.read("neurips_2026.sty")
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
    (root / "workspace/paper/neurips_2026.sty").write_bytes(style)
    config.write_text(text)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", required=True)
    parser.add_argument("--operator", required=True)
    parser.add_argument("--after-ts", required=True)
    parser.add_argument("--template", type=Path, default=TEMPLATE,
                        help="Venue template archive (default: the bundled neurips_2026.zip; checksum-verified)")
    args = parser.parse_args()
    prepare(Path(__file__).resolve().parent, args.channel, args.operator, args.after_ts, args.template.read_bytes())
    print("Drop-in resolved and pinned venue template installed. Run scaffold check before starting.")
