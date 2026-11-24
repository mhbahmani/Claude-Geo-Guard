# What the network checks measure

Each launch, supported hook and active monitor check requests `/cdn-cgi/trace` over HTTPS on both `claude.ai` and `api.anthropic.com`. The guard verifies the returned hostname (`h`), public client address (`ip`) and client country (`loc`). The edge data center (`colo`) is not the client country. An unavailable endpoint, invalid response, missing address family, or disagreement with country or exact IP policy denies the checkpoint. `claude-geo-guard check` reports observations by destination and address family.

By default ifconfig.io must also pass. For a routed split tunnel, install with `--split-tunnel` or set `"split_tunnel": true` in config.json. To restore the general egress check, use `--no-split-tunnel`. Both Claude destinations remain mandatory in either mode. Each IP can differ as long as each satisfies policy. Application-specific proxies, separate network namespaces and other application endpoints may follow other routes.

The probes launch curl with its startup config disabled, no redirects, no proxy, no cookies, no account headers and no inherited credential variables. The endpoint necessarily sees the source IP. These are ordinary unauthenticated HTTPS requests, independent of any logged-in Claude session.

## DNS and local overrides

An IP in a DNS answer is the **server** address. A trace reports the **client** address observed at the edge. A recursive resolver in the UK can legitimately return the same CDN address as a resolver elsewhere. Comparing your answers with a remote DNS propagation checker cannot establish where your own DNS queries traveled. Pinning CDN addresses can break service as records change.

Before and after probing, the guard reads /etc/hosts and denies active overrides for claude.ai, claude.com, anthropic.com and their subdomains, including aliases on a shared line. Remove the local override to resume checks. The guard never writes hosts entries. It does not detect every DNS leak. To enforce resolver privacy, the OS or VPN must route DNS through the intended tunnel and prevent fallback, including application-managed encrypted DNS. A trace cannot prove that configuration. Periodic process checks do not block individual network packets.

Sources: [Net.Coffee’s Claude checker implementation](https://ip.net.coffee/claude/claude-page.js), [Cloudflare’s managed trace endpoint](https://developers.cloudflare.com/fundamentals/reference/cdn-cgi-endpoint/), [how anycast can serve one IP from multiple locations](https://www.cloudflare.com/learning/cdn/glossary/anycast-network/), and [DNSChecker’s description of propagation checks](https://dnschecker.org/).
