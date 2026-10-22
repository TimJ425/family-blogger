"""Believe ``X-Forwarded-*`` headers only from trusted reverse proxies.

Behind a reverse proxy (e.g. Nginx Proxy Manager doing HTTPS) every
request reaches the app from the proxy's address. The proxy says who
the real client is in ``X-Forwarded-For`` (and the original scheme
in ``X-Forwarded-Proto``). ``X-Forwarded-Host``/``-Port`` are never
honoured, so a forwarded header cannot change ``request.host``.
Anyone can send those headers, so they are only honoured when the
request comes *directly from* an address listed in
``FB_TRUSTED_PROXIES``. Then
``request.remote_addr`` is the real client, which log-in throttling
relies on to tell family members' devices apart.
"""
import ipaddress

from werkzeug.middleware.proxy_fix import ProxyFix


def parse_networks(
    entries: list[str],
) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Turn ``FB_TRUSTED_PROXIES`` entries into networks.

    Args:
        entries: IP addresses (``"192.168.1.2"``) or CIDR networks
            (``"172.18.0.0/16"``).

    Returns:
        One network per entry; a single address becomes a /32 or
        /128 network. An IPv4-mapped IPv6 network
        (``::ffff:10.8.0.0/112``) becomes the equivalent IPv4 one.

    Raises:
        ValueError: If an entry is not an address or network, so a
            typo stops the app at start-up instead of silently
            trusting nobody. Also raised for an IPv6 network that
            only partly overlaps the IPv4-mapped range (such as
            ``::ffff:0:0/80`` or ``::/0``), which is ambiguous.
    """
    networks = []
    for entry in entries:
        try:
            net = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            raise ValueError(
                f"FB_TRUSTED_PROXIES: {entry!r} is not an IP address "
                "or network (e.g. 172.18.0.0/16)") from None
        if net.version == 6:
            net = _unmap(entry, net)
        networks.append(net)
    return networks


def _unmap(
    entry: str, net: ipaddress.IPv6Network,
) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    """Turn an IPv4-mapped IPv6 network into an IPv4 network.

    Args:
        entry: The original text, for the error message.
        net: The parsed IPv6 network.

    Returns:
        The equivalent IPv4 network if ``net`` lies inside
        ``::ffff:0:0/96``; ``net`` unchanged if it does not touch
        that range at all.

    Raises:
        ValueError: If ``net`` is wider than ``::ffff:0:0/96`` but
            overlaps it, or is a mapped network with fewer than 96
            bits (so it cannot be an IPv4 prefix).
    """
    mapped = ipaddress.ip_network("::ffff:0:0/96")
    if net.subnet_of(mapped):
        v4_addr = ipaddress.IPv4Address(
            int(net.network_address) & 0xFFFFFFFF)
        return ipaddress.IPv4Network(
            (v4_addr, net.prefixlen - 96), strict=False)
    if net.overlaps(mapped):
        raise ValueError(
            f"FB_TRUSTED_PROXIES: {entry!r} is ambiguous because it "
            "mixes IPv4-mapped and other IPv6 addresses; write the "
            "IPv4 network instead (e.g. 10.8.0.0/16)")
    return net


def is_trusted(
    addr: str | None,
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
) -> bool:
    """Say whether a peer address belongs to a trusted proxy.

    Args:
        addr: The direct peer's address (``REMOTE_ADDR``).
        networks: The trusted networks.

    Returns:
        ``True`` if the address is inside one of the networks. An
        IPv4 address written as IPv6 (``::ffff:10.0.0.1``) is
        compared as IPv4.
    """
    try:
        ip = ipaddress.ip_address(addr or "")
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in net for net in networks
               if net.version == ip.version)


class TrustedProxyMiddleware:
    """WSGI middleware applying ``ProxyFix`` only for trusted peers.

    One proxy hop is honoured: the last ``X-Forwarded-For`` entry,
    which the trusted proxy added itself, becomes ``remote_addr``,
    and ``X-Forwarded-Proto`` sets the scheme. The forwarded host
    and port are ignored.

    Attributes:
        app: The wrapped WSGI application.
        networks: The trusted proxy networks.
        trust_any: Legacy ``FB_TRUST_PROXY=1`` mode, which believes
            any direct peer.
        fixed: ``app`` wrapped in ``ProxyFix``.
    """

    def __init__(self, app: object, trusted: list[str],
                 trust_any: bool = False) -> None:
        """Wrap a WSGI app.

        Args:
            app: The WSGI application (``Flask.wsgi_app``).
            trusted: ``FB_TRUSTED_PROXIES`` entries.
            trust_any: Believe every direct peer (legacy).

        Raises:
            ValueError: If an entry in ``trusted`` is malformed.
        """
        self.app = app
        self.networks = parse_networks(trusted)
        self.trust_any = trust_any
        self.fixed = ProxyFix(app, x_for=1, x_proto=1, x_host=0,
                              x_port=0)

    def __call__(self, environ: dict, start_response: object) -> object:
        """Handle one request, honouring forwarded headers if trusted.

        Args:
            environ: The WSGI environment.
            start_response: The WSGI ``start_response`` callable.

        Returns:
            The WSGI response iterable.
        """
        if self.trust_any or is_trusted(environ.get("REMOTE_ADDR"),
                                        self.networks):
            return self.fixed(environ, start_response)
        return self.app(environ, start_response)
