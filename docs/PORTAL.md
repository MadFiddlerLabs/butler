# The portal

The portal is a small web app on your Mac: Today, Week, Last month, Money, Budget, Projects,
Lists and Info. It listens on 127.0.0.1 only. Tailscale makes it reachable from your own
devices, over HTTPS, and from nowhere else.

```
./run.sh portal        # or: launchd/install.sh, which keeps it running
open http://127.0.0.1:8787
```

## On your phones, with Tailscale

1. Install [Tailscale](https://tailscale.com) on the Mac and on each phone, signed in to the
   same tailnet. Invite the other adults from the Tailscale admin console.
2. In the admin console, turn on MagicDNS and HTTPS certificates (DNS page).
3. On the Mac:
   ```
   tailscale serve --bg 8787
   ```
   It prints the address, like `https://your-mac.your-tailnet.ts.net`.
4. Put that address in `portal.url` in `config/settings.yaml`. The emails link to it.
5. List who may enter in `portal.allowed_logins` (their Tailscale login emails), and map each
   to a name in `portal.people` so updates say who made them.

`tailscale serve` tells the portal who is visiting (the `Tailscale-User-Login` header), and the
portal turns away anyone not in `allowed_logins`. On iPhone, open the address in Safari and
use Share > Add to Home Screen for an app icon.

The portal works while the Mac is awake.

## Under your own domain (advanced)

You can serve the portal as `https://casa.example.com` to your tailnet only: the DNS record
points at the Mac's Tailscale address, which nobody outside your tailnet can reach. macOS
won't let an unprivileged process bind port 443 on one address, so Tailscale forwards raw TCP
to Caddy on 127.0.0.1:8443, and Caddy learns the visitor's address through the PROXY protocol.

You need Caddy, lego and a DNS provider where you can add records:

```
brew install caddy lego
```

1. Add an `A` record for `casa.example.com` pointing at the Mac's Tailscale IPv4 address
   (`tailscale ip -4`).
2. Register with [acme-dns](https://github.com/joohoi/acme-dns) and get the certificate once.
   lego prints a `CNAME` record to add for `_acme-challenge.casa.example.com`:
   ```
   ACME_DNS_API_BASE=https://auth.acme-dns.io ACME_DNS_STORAGE_PATH=certs/acme-dns.json \
     lego run --accept-tos --email you@example.com --dns acmedns \
     --dns.resolvers 1.1.1.1:53 --domains casa.example.com --path certs
   ```
3. Install Caddy and the daily renewal:
   ```
   launchd/install.sh --domain casa.example.com --tailnet-host your-mac.your-tailnet.ts.net --email you@example.com
   ```
4. Forward the tailnet's port 443 to Caddy, replacing the plain `tailscale serve` from above:
   ```
   tailscale serve reset
   tailscale serve --bg --tcp 443 --proxy-protocol 2 tcp://127.0.0.1:8443
   ```
5. Set `portal.url` to `https://casa.example.com`.

With this setup the portal identifies visitors with `tailscale whois` on the address Caddy
passes on, using the Tailscale app at `/Applications/Tailscale.app`.
