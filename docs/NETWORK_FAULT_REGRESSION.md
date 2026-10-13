# Audience address discovery and operator Origin regression (#82)

Scope: one independently reviewable slice of #82, not completion of its
preflight, runtime installer, translation timeout, websocket backpressure,
SQLite I/O and desktop resilience requirements.

## Behavior

Audience IPv4 address **suggestions** are restricted to RFC 1918
(`10/8`, `172.16/12`, `192.168/16`) and RFC 6598 shared
(`100.64/10`, used by Tailscale/CGNAT). The old
`IPv4Address.is_private` condition included unusable endpoints such as
link-local `169.254/16`, unspecified `0.0.0.0`,
benchmark-only `198.18/15` and broadcast `255.255.255.255`.
Those addresses are not valid audience links.

Important: this function is **not** the host firewall, CORS policy or
authorization gate. It only selects discoverable IPs for audience URLs.
The operator API continues to require loopback clients and explicitly trusted
loopback browser Origins. Do not infer a firewall restriction from these tests.

## Deterministic fault-injection tests

| Risk | Synthetic test | Expected behavior |
| --- | --- | --- |
| Bad URL advertised | Table of non-LAN/link-local/broadcast/unspecified IPv4, IPv6, invalid strings | Reject all |
| Tailscale not shown | In-range 100.64/10 and edge boundaries | Accept entire allowed range only |
| DNS/route ambiguity | Mock mixed A records plus fake UDP route | Return sorted, deduplicated LAN/VPN IPs |
| Offline network | Mock DNS `OSError` and UDP connect failure | Return empty list without crash |
| Browser on LAN requests operator control | Trusted loopback + LAN/CGNAT/spoofed Origin fixtures | Reject nonloopback and forged Origins |
| Native client without Origin | Explicit None path | Preserve intentionally supported policy |

## Not accepted as resolved

- General HTTP authentication, firewall, mDNS, IPv6 audience access,
  or Tailscale ACL correctness
- Real-machine multi-NIC test (Windows/macOS/Linux); synthetic tests
  must run in existing CI first
- The rest of #82 (preflight, runtime installation cancellation, provider
  exceptions, DB exhaustion and WebSocket pressure)
