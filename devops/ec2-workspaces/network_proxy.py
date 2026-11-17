"""Optional mitmproxy URL filter, configured by an operator-owned TOML file."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Self
from urllib.parse import unquote, urlsplit

if TYPE_CHECKING:
    from mitmproxy.addonmanager import Loader
    from mitmproxy.http import HTTPFlow


class PolicyError(Exception):
    """A policy could not be loaded or safely evaluated."""


class Policy:
    def __init__(self, patterns: tuple[re.Pattern[str], ...]) -> None:
        self.patterns = patterns

    @classmethod
    def load(cls, path: Path | str) -> Self:
        try:
            data = tomllib.loads(Path(path).read_text())
            if data.keys() != {"network"} or not isinstance(data["network"], dict):
                raise ValueError
            network = data["network"]
            if network.keys() != {"blacklist"} or not isinstance(network["blacklist"], list):
                raise ValueError
            if any(not isinstance(value, str) or not value.strip() for value in network["blacklist"]):
                raise ValueError
            return cls(tuple(re.compile(value) for value in network["blacklist"]))
        except (OSError, ValueError, TypeError, re.error) as from_error:
            raise PolicyError("Invalid network policy; expected [network] blacklist with URL regex strings") from from_error

    def match(self, url: str) -> int | None:
        for _ in range(8):
            decoded = unquote(url, errors="strict")
            if decoded == url:
                break
            url = decoded
        else:
            if unquote(url, errors="strict") != url:
                raise PolicyError("URL encoding exceeds the policy decoding limit")
        return next((index for index, pattern in enumerate(self.patterns, 1) if pattern.search(url)), None)


class Blacklist:
    def __init__(self) -> None:
        self.policy: Policy | None = None

    def load(self, loader: Loader) -> None:
        loader.add_option("crux_network_policy", str, "", "Operator-owned TOML URL blacklist")

    def configure(self, updated: set[str]) -> None:
        if "crux_network_policy" in updated:
            from mitmproxy import ctx, exceptions

            try:
                self.policy = Policy.load(ctx.options.crux_network_policy)
            except PolicyError as error:
                raise exceptions.OptionsError(str(error)) from error

    def request(self, flow: HTTPFlow) -> None:
        from mitmproxy import http

        try:
            if self.policy is None:
                raise PolicyError("Network policy is unavailable")
            if flow.request.pretty_url == "http://crux-proxy.invalid/healthz":
                flow.response = http.Response.make(200, b"Policy proxy ready.\n")
                return
            rule = self.policy.match(flow.request.pretty_url)
            if rule is None:
                return
        except Exception:
            # Addon exceptions otherwise leave a flow eligible for forwarding.
            rule = "policy_error"
        logging.warning(json.dumps({"event": "network_denied", "rule": rule,
                                    "host": urlsplit(flow.request.pretty_url).hostname}))
        flow.response = http.Response.make(403, b"Blocked by run network policy.\n",
                                           {"Content-Type": "text/plain", "Cache-Control": "no-store"})


addons = [Blacklist()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", type=Path, required=True)
    args = parser.parse_args()
    try:
        policy = Policy.load(args.check)
    except PolicyError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(f"Valid network policy: {len(policy.patterns)} blacklist rules")
    return 0


if __name__ == "__main__":
    sys.exit(main())
