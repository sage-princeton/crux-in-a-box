"""Optional mitmproxy URL and search-result filter, configured by an operator-owned TOML file."""

from __future__ import annotations

import argparse
import base64
import binascii
import html
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
    from mitmproxy.http import Headers, HTTPFlow

logger = logging.getLogger(__name__)
KEYS = ("blacklist", "inspect_hosts", "content_blacklist")
# Larger or non-text responses from inspected hosts stream through unchecked, like every other response.
INSPECT_LIMIT = 10 * 1024 * 1024
TEXT_TYPES = {"application/json", "application/xml", "application/javascript", "application/x-javascript",
              "application/rss+xml", "application/atom+xml", "application/xhtml+xml"}
TAG = re.compile(r"<[^>]*>")
# Bing result links carry their target as unpadded URL-safe base64 after "a1".
BING_TARGET = re.compile(r"[?&;]u=a1([A-Za-z0-9_-]{8,})")


class PolicyError(Exception):
    """A policy could not be loaded or safely evaluated."""


def views(text: str) -> tuple[str, ...]:
    """The forms of a response body the rules are matched against: percent-decoded markup, the readable text
    without tags or entities (search engines highlight query words with tags inside titles), and the targets of
    encoded redirect links."""
    decoded = text
    for _ in range(3):
        once = unquote(decoded)
        if once == decoded:
            break
        decoded = once
    readable = " ".join(html.unescape(TAG.sub(" ", decoded)).split())
    targets = []
    for match in BING_TARGET.finditer(decoded):
        value = match[1]
        try:
            targets.append(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("utf-8", "replace"))
        except (binascii.Error, ValueError):
            continue
    return (decoded, readable, *targets)


def inspectable(headers: Headers) -> bool:
    """Whether a response is text small enough to check before it is forwarded; event streams always stream."""
    kind = headers.get("content-type", "").split(";")[0].strip().lower()
    if kind == "text/event-stream" or not (kind.startswith("text/") or kind in TEXT_TYPES
                                           or kind.endswith(("+json", "+xml"))):
        return False
    length = headers.get("content-length", "")
    return not (length.isdigit() and int(length) > INSPECT_LIMIT)


class Policy:
    def __init__(self, patterns: tuple[re.Pattern[str], ...], inspect_hosts: tuple[str, ...] = (),
                 content_patterns: tuple[re.Pattern[str], ...] = ()) -> None:
        self.patterns = patterns
        self.inspect_hosts = inspect_hosts
        self.content_patterns = content_patterns

    @classmethod
    def load(cls, path: Path | str) -> Self:
        try:
            data = tomllib.loads(Path(path).read_text())
            if data.keys() != {"network"} or not isinstance(data["network"], dict):
                raise ValueError
            network = data["network"]
            if "blacklist" not in network or not network.keys() <= set(KEYS):
                raise ValueError
            lists = {key: network.get(key, []) for key in KEYS}
            for values in lists.values():
                if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip()
                                                       for value in values):
                    raise ValueError
            hosts = tuple(host.strip().lower().strip(".") for host in lists["inspect_hosts"])
            if any(not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", host) for host in hosts):
                raise ValueError
            return cls(tuple(re.compile(value) for value in lists["blacklist"]), hosts,
                       tuple(re.compile(value) for value in lists["content_blacklist"]))
        except (OSError, ValueError, TypeError, re.error) as from_error:
            raise PolicyError("Invalid network policy; expected [network] blacklist with URL regex strings, "
                              "optional inspect_hosts domains and optional content_blacklist regex strings"
                              ) from from_error

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

    def inspects(self, host: str | None) -> bool:
        """Whether responses from this host (a listed domain or its subdomain) are checked."""
        host = (host or "").lower().rstrip(".")
        return any(host == domain or host.endswith("." + domain) for domain in self.inspect_hosts)

    def match_content(self, text: str) -> int | str | None:
        """The first rule matching a response body: a URL rule by number, else content rule N as `content:N`.
        URL rules apply because result pages list the blocked URLs among their results."""
        forms = views(text)
        rules: list[tuple[int | str, re.Pattern[str]]] = list(enumerate(self.patterns, 1))
        rules += [(f"content:{index}", pattern) for index, pattern in enumerate(self.content_patterns, 1)]
        return next((rule for rule, pattern in rules if any(pattern.search(form) for form in forms)), None)


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
        except Exception:  # noqa: BLE001 - Fail closed: an addon exception would otherwise forward the flow.
            rule = "policy_error"
        self.deny(flow, rule)

    def responseheaders(self, flow: HTTPFlow) -> None:
        # mitmproxy otherwise holds every response until it is complete, which stalls model event streams and
        # buffers large downloads in memory. Only responses that will be checked are held.
        try:
            held = self.policy is not None and self.policy.inspects(flow.request.host) \
                and inspectable(flow.response.headers)
        except Exception:  # noqa: BLE001 - Fail closed: hold the response so it is still checked.
            held = True
        if not held:
            flow.response.stream = True

    def response(self, flow: HTTPFlow) -> None:
        if flow.response is None or flow.response.stream:
            return
        try:
            if self.policy is None or not self.policy.inspects(flow.request.host):
                return
            rule = self.policy.match_content(flow.response.get_text(strict=False) or "")
            if rule is None:
                return
        except Exception:  # noqa: BLE001 - Fail closed: an unreadable inspected response is blocked.
            rule = "policy_error"
        self.deny(flow, rule)

    @staticmethod
    def deny(flow: HTTPFlow, rule: int | str) -> None:
        from mitmproxy import http

        logger.warning(json.dumps({"event": "network_denied", "rule": rule,
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
    print(f"Valid network policy: {len(policy.patterns)} blacklist rules, {len(policy.inspect_hosts)} inspected "
          f"hosts, {len(policy.content_patterns)} content rules")
    return 0


if __name__ == "__main__":
    sys.exit(main())
