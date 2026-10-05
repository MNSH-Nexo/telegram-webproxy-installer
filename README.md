# Telegram WebProxy

> 🇮🇷 [فارسی](README.fa.md) · 🇬🇧 English

One-click, multi-instance Telegram **web / webapp proxy** (MTProxy + Caddy) for Debian / Ubuntu servers, with an optional **web management panel** (HTTPS by domain or by server IP, modeled on the 3x-ui setup flow).

## Install

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/MNSH-Nexo/telegram-webproxy-installer/main/panel.sh) install
```

This installs the **web panel** (same flow as 3x-ui): it asks for a panel port, offers HTTPS (Let's Encrypt for a domain or for the server IP, or your own certificate), then prints the **username, password and access URL once**, so save them. Open the panel and create proxies from **Dashboard → New instance**.

- Needs: `root` on Debian 11/12 or Ubuntu 20.04+ (x86_64), ports `80` and `443` free, and the panel port open in your firewall.
- Running the same command again **updates** the panel and keeps your settings, users and instances.
- Afterwards manage it with the `dxwp` command (menu, SSL, settings).

---

- **Proxy installer** (`install-tproxy.sh`): installs Go, Caddy, MTProxy and systemd services; every instance has its own domain, ports and profile, with automatic HTTPS.
- **Web panel** (`panel.sh`): install, list, health-check, change carrier, restart, remove and monitor instances from the browser, behind a secret path, a login and a Let's Encrypt certificate.

---

## Features

**Proxy**
- **One-command install**: downloads and configures Go, Caddy, MTProxy and systemd services. No manual prerequisites.
- **Multi-instance**: as many instances as you want on one server; each one is isolated (own domain, ports, profile, services).
- **Automatic HTTPS**: Let's Encrypt certificates issued and renewed by Caddy for every instance.
- **Cloudflare-ready**: `direct` mode, or `cf` mode (proxy off while the certificate is issued, then on).
- **Carrier profiles**: `https`, `https-lanes`, `websocket`, `websocket-lanes`; pick the transport that works best in your region.
- **Site mask**: serve a static site or a loopback upstream on the front domain.
- **Hardened by default**: relay and MTProxy ports and their admin ports are blocked from the internet; the public entry point is Caddy on `443`.
- **Robust and idempotent**: `set -euo pipefail`, download / apt retries, disk and architecture checks, safe re-runs.

**Web panel**
- **Dashboard**: install a new instance with a form (domain, mode, carrier, email, secret, site mask, advanced options) and a live install log; list instances, check health, copy the Telegram link, change carrier, restart, remove, wipe all.
- **Pre-install check**: before every install the panel resolves the domain and blocks the install if it has no DNS record, points to another IP, or the Cloudflare proxy is already on (Cloudflare IPs). Multiple IPs or an IPv6 record only warn, and you can continue.
- **SSL page**: get a Let's Encrypt certificate for the **panel** by **domain** or by **server IP**, or use your own certificate files; see status and expiry.
- **Panel page**: change username, password, port and secret path; copy the panel address; sign out.
- **Traffic page**: live sessions and bytes up / down per instance.
- **Persian and English**, light / dark / auto theme and several color palettes.
- **Secure by design**: random credentials, secret URL path, PBKDF2-SHA256 password hash, `HttpOnly` + `SameSite=Strict` cookie, login lockout after 5 failures, no shell injection (installer arguments are validated and run without a shell).
- **3x-ui style management**: the `dxwp` command gives a menu and CLI for settings and SSL (get, revoke, renew, list).
- **No extra dependencies**: the panel is a single Python 3 file (standard library only).

---

## Requirements

| Requirement  | Value                                                       |
| ------------ | ----------------------------------------------------------- |
| OS           | Debian 11/12 or Ubuntu 20.04+ (systemd + apt)               |
| Architecture | x86_64 / amd64                                              |
| User         | `root`                                                     |
| Free ports   | `80` and `443` (Caddy); one more port for the panel         |
| DNS          | a domain (or subdomain) pointing to this server's public IP |

> **Note:** this installs Caddy on port `443`. Do not run it on a server where something else (for example an xray / SSH-TLS tunnel) already uses ports `80` / `443`.

---

## Web panel

### What you need before installing

| Check | Why |
| ----- | --- |
| Run as `root` on Debian / Ubuntu | the installer uses `apt` and `systemd` |
| Port **80** reachable from the internet | Let's Encrypt validates domain and IP certificates over HTTP on port 80 |
| The **panel port** open in your firewall / cloud security group | you open the panel at `https://HOST:PORT/PATH/` (the port is random unless you choose one) |
| For a **domain** certificate: an `A` record pointing to this server | validation fails if DNS does not resolve to this server yet |
| If the domain is on Cloudflare: record set to **DNS only** (gray cloud) while issuing | Cloudflare would otherwise answer the validation request |

### Install

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/MNSH-Nexo/telegram-webproxy-installer/main/panel.sh) install
```

The setup follows the same flow as 3x-ui:

1. Installs dependencies (`python3`, `curl`, `openssl`, `socat`, `cron`) and the `dxwp-panel` systemd service.
2. Generates a random **username**, **password** and **secret path** (the part after the port in the URL).
3. Asks *"Would you like to customize the Panel Port settings?"*. Answer `y` to choose a port, or `n` for a random one.
4. Shows the **SSL menu** (below).
5. Prints the credentials and the access URL **once**. Save them: the password is stored only as a hash.

```
Username:    f81H93SEuU
Password:    L9U0XCnADp
Port:        18321
WebBasePath: 1DrN2xzMfq0tNQi20V
Access URL:  https://203.0.113.10:18321/1DrN2xzMfq0tNQi20V/
```

> Without HTTPS the login password travels in clear text, so choose one of the first three SSL options unless the panel sits behind a tunnel or reverse proxy.

### SSL options: domain, server IP or your own certificate

```
1. Let's Encrypt for a domain (90 days, auto-renews)
2. Let's Encrypt for this server's IP (~6 days, auto-renews)      <- default
3. Custom certificate (paths to existing files)
4. Skip (plain HTTP)
```

#### Option 1: with a domain (recommended)

1. Create an `A` record, e.g. `panel.example.com → <server IP>` (Cloudflare: **DNS only**).
2. Choose `1` and enter the domain. The installer issues the certificate with `acme.sh` and applies it to the panel.
3. Open `https://panel.example.com:<port>/<path>/`.

The certificate is valid for 90 days and renews automatically. The same domain can also be used by a proxy instance; the panel listens on its own port, not `443`.

#### Option 2: with the server IP (no domain)

1. Choose `2`. The installer detects the public IPv4 and lets you confirm or change it; you can add an IPv6 address too.
2. A Let's Encrypt **IP certificate** (short-lived profile, about 6 days) is issued and renewed automatically.
3. Open `https://<server IP>:<port>/<path>/`.

> IP certificates are short-lived, so they renew often. Prefer a domain when you have one.

#### Option 3: your own certificate

Give the full paths of an existing `fullchain.pem` and `privkey.pem`. The panel reloads the file by itself when it changes.

#### Option 4: skip

The panel runs on plain HTTP. Use it only behind a tunnel or reverse proxy, for example over SSH:

```bash
ssh -L 8443:127.0.0.1:<panel-port> root@<server>
# then open http://127.0.0.1:8443/<path>/
```

#### What about Caddy and port 80?

Caddy (the proxy gateway) owns port 80. During certificate issuance and every renewal, `acme.sh` **stops Caddy for a few seconds**, validates on port 80, and starts it again (registered as pre / post hooks). During that moment the proxy on `443` is briefly unreachable. Domain certificates renew about every 60 days; IP certificates much more often.

### Using the panel

Open the Access URL and sign in. The bottom dock has four pages:

| Page | What it does |
| ---- | ------------ |
| **Dashboard** | Install a proxy instance with the form (a live log appears while it installs); list, check health, copy the Telegram link, change carrier, restart or remove an instance; wipe everything. |
| **SSL** | Get a certificate for the panel by domain or IP, or set custom files; view status and expiry; turn HTTPS off. The panel restarts and redirects you to the new address. |
| **Panel** | Change username, password, port and secret path (generate a random path with the key button); copy the address; sign out. |
| **Traffic** | Live sessions and uploaded / downloaded bytes per instance, plus totals. |

Notes:

- **Cloudflare instances**: turn the Cloudflare proxy **off** before pressing *Install* in `cf` mode, and on again afterwards. The panel never waits for a keypress.
- **Change carrier** edits the instance profile and restarts its relay. Ports, secret and site mask stay untouched.
- Only one install / wipe job runs at a time.

### The `dxwp` command

After installation the `dxwp` command manages the panel from the server shell. Run `dxwp` alone for a menu, or use it as a CLI:

| Command | Description |
| ------- | ----------- |
| `dxwp` | open the interactive menu |
| `dxwp show` | print the panel URL, username and SSL state |
| `dxwp ssl` | run the SSL menu (domain / IP / custom / skip) |
| `dxwp ssl-domain example.com` | get a certificate for a domain |
| `dxwp ssl-ip 203.0.113.10 [IPv6]` | get a certificate for an IP |
| `dxwp ssl-custom CERT KEY` | use existing certificate files |
| `dxwp ssl-list` | list certificates |
| `dxwp ssl-renew NAME` | force renew a certificate |
| `dxwp ssl-revoke NAME` | revoke and remove a certificate |
| `dxwp setting -username U -password P -port N -webBasePath PATH` | change settings |
| `dxwp setting -reset` | reset credentials, port and path to random values |
| `dxwp start` / `stop` / `restart` / `status` / `log` | control the service |
| `dxwp install` | update the panel files and keep your settings |
| `dxwp uninstall` | remove the panel (instances and certificates stay) |

Automation: `DXWP_NONINTERACTIVE=1 DXWP_SSL_MODE=ip bash panel.sh install` (also `domain` with `DXWP_SSL_DOMAIN`, `custom` with `DXWP_SSL_CERT` / `DXWP_SSL_KEY`; optional `DXWP_USERNAME`, `DXWP_PASSWORD`, `DXWP_PANEL_PORT`, `DXWP_WEB_BASE_PATH`).

### Panel troubleshooting

- **Certificate fails**: check that the domain resolves to this server, port 80 is open (cloud firewall / `ufw`), nothing else listens on port 80, and the Cloudflare record is DNS only. Then run `dxwp ssl` again.
- **Cannot open the panel**: open the panel port in your firewall; check `dxwp status` and `dxwp log`; the URL must include the secret path with the trailing `/`.
- **Forgot the password or path**: run `dxwp setting -reset` (random values) or `dxwp setting -password NEW`.
- **Browser warns about the certificate**: you are on plain HTTP or an expired certificate; run `dxwp ssl` or `dxwp ssl-renew NAME`.
- **Locked out after failed logins**: wait 5 minutes.

### Security notes

Keep the secret path private, use HTTPS, and keep the panel port restricted if you can (for example to your own IP in the firewall). Passwords are stored as PBKDF2-SHA256 hashes; sessions are `HttpOnly` + `SameSite=Strict`; five failed logins lock an IP for five minutes.

---

## CLI usage

For automation or re-runs you can pass arguments instead of using the wizard:

```bash
# Direct instance
bash install-tproxy.sh --hostname proxy.example.com --mode direct --carrier https-lanes --email admin@example.com

# Cloudflare instance (turn the proxy OFF before installing, ON after it finishes)
bash install-tproxy.sh --hostname cdn.example.com --mode cf
```

### Actions

| Action       | Description                                       |
|--------------|---------------------------------------------------|
| *(default)*  | Install the base system, then add/refresh an instance |
| `--list`     | list installed instances                          |
| `--verify`   | show health status of all instances               |
| `--wipe`     | remove every instance and the shared components   |

### Instance options

| Flag | Description |
|------|-------------|
| `--hostname HOST` | the domain for this proxy (required) |
| `--name ID` | instance id (default: the hostname) |
| `--email EMAIL` | Let's Encrypt contact email |
| `--mode direct\|cf` | front mode (default: `direct`) |
| `--secret HEX` | proxy secret (default: random) |
| `--carrier MODE` | `https` \| `https-lanes` \| `websocket` \| `websocket-lanes` |
| `--site-dir DIR` | mask the front with a local static site |
| `--site-upstream URL` | mask the front with a loopback upstream origin |

Cover site: unless `--site-dir` / `--site-upstream` is given, every domain gets a random realistic website (3 templates in `sites/`, personalised per domain with its own brand name, colours and contact address, with inner pages and a 404 page). `--site-template edgewire|cdncrest|tesla|plain` forces one. MTProxy workers default to 10.

Shared options: `--email-account`, `--mtproxy-workers`, `--mtproxy-max-connections`, `--caddy-version`, `--keep-log`, `--clean`, `--no-dns-check`, `--help`.

---

---

## Carrier modes

> **Allowed carriers per mode:** `direct` uses `https` or `https-lanes`; `cf` (behind the Cloudflare proxy) uses `websocket` or `websocket-lanes`. The panel only enables the matching pair and the installer rejects other combinations.

The `--carrier` option picks the **transport** used between the browser and the proxy. It combines two independent choices:

**Transport shell: `https` vs `websocket`**

- **`https`** — traffic runs as a normal HTTPS (TLS) connection. Highest compatibility, lowest overhead; ideal for **direct** mode.
- **`websocket`** — traffic is wrapped inside a **WebSocket** connection (which itself runs over HTTPS), so it looks like ordinary web traffic. It sails through firewalls and reverse proxies more easily, and is especially friendly to **Cloudflare** (which proxies WebSocket well).

**Connection count: no `lanes` vs `lanes`**

- **no `lanes`** — a single connection per session. Simple, low connection count.
- **`lanes`** — the client opens **several parallel connections** ("fast lanes"). This reduces latency spikes and raises throughput, which helps most on high-latency or unstable connections, at the cost of a few more connections.

### Choosing a mode

| Mode | Shell | Connections | Best for |
|------|-------|-------------|----------|
| `https` | HTTPS | single | direct mode; simplest and lowest overhead |
| `https-lanes` | HTTPS | parallel | direct mode, when you want more speed |
| `websocket` | WebSocket | single | behind Cloudflare / a reverse proxy; looks like normal web traffic |
| `websocket-lanes` | WebSocket | parallel | behind Cloudflare **and** want more speed |

> **Recommended:** use `websocket-lanes` when the subdomain is fronted by Cloudflare, and `https` or `https-lanes` for a direct (non-Cloudflare) setup.

You can switch the carrier later from the web panel (**Dashboard → Change carrier**) or by re-running the installer with the same hostname and a different `--carrier`. Ports, secret and site mask are kept.

---

---

## Cloudflare setup

To front an instance with Cloudflare:

1. In Cloudflare, set the subdomain record to **DNS only** (proxy off / gray cloud) **before** installing.
2. Run the installer in `cf` mode. It issues a **Let's Encrypt** certificate while the proxy is off.
3. After the install finishes, flip the record to **Proxied** (orange cloud).

No Cloudflare API token is required.

---

---

## Architecture (per instance)

A single **Caddy** gateway fronts all instances and routes by SNI. Each instance is isolated:

| Component       | Port (instance *n*) | Exposure     |
|-----------------|---------------------|--------------|
| Caddy (HTTPS)   | `443`               | Public       |
| Relay           | `8080 + 2n`         | via Caddy    |
| Relay admin     | `+1`                | LAN-only     |
| MTProxy         | `2398 + n`          | LAN-only (blocked from WAN) |
| MTProxy admin   | `8888 + n`          | LAN-only     |

Profiles live under `/etc/tproxy-server/<instance>/`; every instance has its own systemd services.

The relay (`tproxy-server`) is built from its upstream source at
[`telegramdesktop/tproxy-server`](https://github.com/telegramdesktop/tproxy-server).
The installer compiles it at install time; if that build is not possible it falls
back to a prebuilt binary published with the original project's release assets.

---

The panel runs separately as `dxwp-panel` (files in `/opt/dxwp-panel`, settings in `/etc/dxwp-panel/config.json`, certificates in `/root/cert`).

---

## Uninstall

Every instance registers itself in `/etc/tproxy-server/instances.json`.

- List instances: `bash install-tproxy.sh --list`
- Remove one instance: web panel → **Dashboard → Remove**
- Wipe everything (all instances and shared components): `bash install-tproxy.sh --wipe`
- Remove the panel only: `dxwp uninstall` (instances and certificates are left untouched)

---

## Troubleshooting

- **Install aborts with a clear message**: the script stops on any failed command instead of leaving a half-broken install; read the printed error and check the log file it points to.
- **HTTPS origin not ready yet**: DNS may not have propagated, or the Cloudflare proxy is still off. Wait a few minutes and re-check (`--verify` or the panel's health button).
- **Port 443 busy**: something already listens there; free it before installing.
- **Panel problems**: see *Panel troubleshooting* above.

---

## Files

| File | Purpose |
| ---- | ------- |
| `install-tproxy.sh` | Main installer (interactive + CLI, multi-instance) |
| `panel.sh` | Panel installer, SSL (domain / IP / custom) and `dxwp` manager |
| `panel/dxwp_panel.py` | Panel backend (Python standard library only) |
| `panel/web/` | Login page and main page (UI) |
| `README.md` / `README.fa.md` | Documentation (English / فارسی) |

---

## License

Released under the [MIT License](LICENSE).

---

## Web panel (3x-ui style)

Running the one-liner **without arguments** now installs the panel instead of a single proxy instance:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/MNSH-Nexo/telegram-webproxy-installer/main/install-tproxy.sh)
```

The flow is copied from the 3x-ui installer: it asks whether to customise the panel port (otherwise a random one is used), then offers
SSL — Let's Encrypt for a domain, Let's Encrypt for the server IP, custom certificate files, or skip. No e-mail address is asked.
Caddy (which owns ports 80/443) is paused for a few seconds only while acme.sh validates, then started again.
At the end it prints Username, Password, Port, WebBasePath, Database, Access URL and API Token
(also saved to `/etc/dxwp-panel/install-result.env`, mode 600).

Manage the panel afterwards with `dxwp` (menu), or `dxwp settings | start | stop | restart | status | log | ssl | token | update | uninstall`.
`--wizard` keeps the old interactive "add one proxy instance" flow; every other flag works as before.

---

## Users, inbounds and live traffic

- **Inbound = instance** (domain + carrier, created on the Dashboard). **Users** are separate: each user has their own secret and is attached to one or more inbounds.
- For every (user, inbound) the panel adds a profile with the user's secret to that instance's `profiles.json`, and runs a dedicated MTProxy backend (`mtproxy@dxu-<port>`) on its own loopback port. That is what makes per-user traffic and limits possible.
- Traffic is counted by nftables counters on those loopback ports (table `inet dxwp_acct`, re-applied at boot by a `tproxy-firewall` drop-in) and sampled **every second**. Users, the Status page (CPU / RAM / swap / disk and overall speed) and the Dashboard counters refresh by themselves every 1-3 s, no refresh button needed.
- A user who reaches the traffic limit or the expiry date, or is disabled, has their backend stopped automatically. Statuses: all / online / ended / disabled / active.
- Menu and panel share one config: `dxwp setting ...`, the menu and the web *Panel settings* page all write `/etc/dxwp-panel/config.json` and keep `install-result.env` in sync.
- Certificates: the web page only asks for the domain or the IP (IPv4 pre-filled with the server's IP). Whatever holds port 80 is stopped for the request and started again afterwards; the log of the request is streamed under the button.

---

## Subscription (sub) service

- **Automatic update check:** every time the panel opens it checks the latest GitHub **Release** (`/releases/latest`) and compares its tag (e.g. `v2.1.5`) with the panel's `VERSION`. When you publish a release, set `VERSION` in `panel/dxwp_panel.py` to the same number first, otherwise the button stays visible. Only when one exists, an **Update** button appears in the top corner of the Settings page; it runs `dxwp update` (settings, users and instances are kept) and reloads the panel.
- Panel > **Settings > Sub settings** (between panel settings and appearance): enable, listen IP, domain, port (default 2096), URI path (`/sub/`) and an optional reverse-proxy URI. Same fields as 3x-ui.
- A user's sub address is `<base><user secret>`. Apps get a base64 list of that user's proxy links (with a `Subscription-Userinfo` header for usage / limit / expiry); browsers get the DX sub page.
- Users > QR: first row is the sub link with a copy button, then a copy-all button, then one QR per inbound with a small corner copy button.
- A busy port is refused and the previous working settings are kept. CLI: `dxwp setting -subEnable on -subPort 2096 -subPath /sub/`.

- `dxwp doctor` checks Caddy, every inbound (relay, MTProxy, DNS) and every user (profile + backend unit) and says what to fix.
