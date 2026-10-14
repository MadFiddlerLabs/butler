# The portal

The portal is a small web app on your Mac: Today, Week, Last month, Money, Budget, Projects,
Lists, Info and Devices. It listens on 127.0.0.1 only. Tailscale makes it reachable from your
own devices, over HTTPS, and from nowhere else. It opens only in browsers you've paired.

```
./run.sh portal        # or: launchd/install.sh, which keeps it running
butler portal          # opens it in this Mac's browser, and pairs that browser
```

## Paired browsers

Every account on the Mac can reach 127.0.0.1, and any program there can send the headers
Tailscale and Caddy use to say who is visiting. So a login alone doesn't get in: each browser
also needs a device key, a cookie it gets by pairing once.

- **This Mac:** run `butler portal`. It opens the portal in your browser and pairs it. The
  one-time code goes through a file only your Mac account can read, never a command line.
- **A phone or another browser:** in a paired browser, open **Devices** and choose **Add a
  phone or browser**. It shows a code. On the phone, open the portal's address and type the
  code. A code works once, within 10 minutes, and five wrong tries cancel every open code.
- **An iPhone Home Screen icon** keeps its own cookies, apart from Safari. Add the icon first,
  open the portal from it, and pair it there.
- **Forget a device** on the Devices page, for a lost phone or an old browser.

The key lasts 400 days and renews on every visit. `data/portal_devices.json` keeps only hashes
of keys and codes, readable by your Mac account only. Butler's folder is readable by your
account only too (`scripts/bootstrap.sh` sets it), because other accounts on a Mac can open a
home folder's subfolders.

## On your phones, with Tailscale

1. Install [Tailscale](https://tailscale.com) on the Mac and on each phone, signed in to the
   same tailnet with personal logins, not work ones. Invite the other adults from the
   Tailscale admin console. An iPhone runs one VPN at a time, so Tailscale replaces any other
   VPN while it's on. The Mac app doesn't put a `tailscale` command on the PATH: the commands
   below use its full path, `/Applications/Tailscale.app/Contents/MacOS/Tailscale` (as
   `portal.py` does). Elsewhere on this page, `tailscale` means that path.
2. Rename the Mac before HTTPS: the address and its certificate use the machine's name, and
   certificate names are public. Use Edit machine name in the admin console, or
   `/Applications/Tailscale.app/Contents/MacOS/Tailscale set --hostname=butler`.
3. In the admin console, turn on MagicDNS and HTTPS certificates (DNS page).
4. On the Mac:
   ```
   /Applications/Tailscale.app/Contents/MacOS/Tailscale serve --bg 8787
   ```
   It prints the address, like `https://butler.your-tailnet.ts.net`.
5. Put that address in `portal.url` in `config/settings.yaml`. The emails link to it.
6. List who may enter in `portal.allowed_logins` (their Tailscale login emails), and map each
   to a name in `portal.people` so updates say who made them.
7. Pair each phone: in a paired browser on the Mac, open Devices > Add a phone or browser, then
   type the code on the phone (see Paired browsers above).

`tailscale serve` tells the portal who is visiting (the `Tailscale-User-Login` header), and the
portal turns away anyone not in `allowed_logins`. It also refuses any request for a name it
doesn't serve (`portal.url`, `portal.hosts`, or 127.0.0.1 and localhost), and any request
without a login unless it comes straight from the Mac itself. On top of all that, every
request needs a paired browser's device key. Only the pairing page, the web app manifest and
the icons answer without one.

On iPhone, open the address in Safari and use Share > Add to Home Screen for an app icon.

The portal works while the Mac is awake.

## Under your own domain (advanced)

You can serve the portal as `https://butler.example.com` to your tailnet only: the DNS record
points at the Mac's Tailscale address, which nobody outside your tailnet can reach. macOS
won't let an unprivileged process bind port 443 on one address, so Tailscale forwards raw TCP
to Caddy on 127.0.0.1:8443, and Caddy learns the visitor's address through the PROXY protocol.

You need Caddy, lego and a DNS provider where you can add records:

```
brew install caddy lego
```

1. Add an `A` record for `butler.example.com` pointing at the Mac's Tailscale IPv4 address
   (`tailscale ip -4`).
2. Register with [acme-dns](https://github.com/joohoi/acme-dns) and get the certificate once.
   lego prints a `CNAME` record to add for `_acme-challenge.butler.example.com`:
   ```
   ACME_DNS_API_BASE=https://auth.acme-dns.io ACME_DNS_STORAGE_PATH=certs/acme-dns.json \
     lego run --accept-tos --email you@example.com --dns acmedns \
     --dns.resolvers 1.1.1.1:53 --domains butler.example.com --path certs
   ```
3. Install Caddy and the daily renewal:
   ```
   launchd/install.sh --domain butler.example.com --tailnet-host butler.your-tailnet.ts.net --email you@example.com
   ```
4. Forward the tailnet's port 443 to Caddy, replacing the plain `tailscale serve` from above:
   ```
   tailscale serve reset
   tailscale serve --bg --tcp 443 --proxy-protocol 2 tcp://127.0.0.1:8443
   ```
5. Set `portal.url` to `https://butler.example.com`, and add the `.ts.net` name to
   `portal.hosts` if you also want to use that one.

With this setup the portal identifies visitors with `tailscale whois` on the address Caddy
passes on, using the Tailscale app at `/Applications/Tailscale.app`.
