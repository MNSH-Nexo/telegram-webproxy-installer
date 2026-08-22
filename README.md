# Telegram WebProxy Installer

One-click, multi-instance Telegram **web / webapp proxy** installer (MTProxy + Caddy) for Debian/Ubuntu servers. Install and run multiple isolated proxy instances on a single host, each with its own domain, automatic HTTPS via Let's Encrypt, and optional Cloudflare fronting.

---

## Features

- **One-command install** — downloads and configures Go, Caddy, MTProxy and systemd services automatically. No manual prerequisites.
- **Multi-instance** — run as many instances as you want on one server; each is fully isolated (own domain, ports, profiles and services).
- **Automatic HTTPS** — Let's Encrypt certificates issued and renewed by Caddy for every instance.
- **Cloudflare-ready** — direct mode by default, or Cloudflare-fronted with a 15-year Origin CA certificate when you provide an API token, or a Let's Encrypt certificate issued while the Cloudflare proxy is off.
- **Interactive wizard** — when run without arguments it walks you through domain, mode, carrier and email step by step.
- **Carrier profiles** — choose the best transport (e.g. `https-lanes`) for your region.
- **Hardened by default** — the firewall only exposes port 443; MTProxy admin panels stay LAN-only.
- **Robust and idempotent** — `set -euo pipefail`, download/apt retries, disk and architecture checks, and safe re-runs.

---

## Requirements

| Requirement | Value |
|-------------|-------|
| OS          | Debian 11/12 or Ubuntu 20.04+ (systemd + apt) |
| Architecture | x86_64 / amd64 |
| User        | `root` (or `sudo`) |
| Free ports  | `80` and `443` |
| DNS         | a domain (or subdomain) pointing to this server's public IP |

> **Note:** this installs Caddy on port `443`. Do not run it on a server where something else (for example an xray / SSH-TLS tunnel) already uses ports `80`/`443`.

---

## Quick start

Download and run the installer directly, without cloning the repository:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/MNSH-Nexo/telegram-webproxy-installer/main/install-tproxy.sh)
```

Or, if you prefer to save it first and inspect it:

```bash
curl -fsSL -o install-tproxy.sh https://raw.githubusercontent.com/MNSH-Nexo/telegram-webproxy-installer/main/install-tproxy.sh
bash install-tproxy.sh
```

The interactive wizard will ask for:

1. **Domain** (or subdomain) pointed to this server, e.g. `proxy.example.com`
2. **Mode** — `direct` or `cf` (Cloudflare)
3. **Carrier** — transport profile, e.g. `https-lanes`
4. **Email** — for Let's Encrypt (leave blank for an auto-generated one)

When it finishes it prints the proxy link and a QR code ready to share.

> Running the wizard again installs an **additional** instance (another domain). Each run equals one instance.

---

## CLI usage

For automation or re-runs you can pass arguments instead of using the wizard:

```bash
# Direct instance
bash install-tproxy.sh --hostname proxy.example.com --mode direct --carrier https-lanes --email admin@example.com

# Cloudflare instance WITHOUT API token (proxy stays OFF until you enable it)
bash install-tproxy.sh --hostname cdn.example.com --mode cf

# Cloudflare instance WITH API token (Origin CA, 15-year, no manual steps)
bash install-tproxy.sh --hostname cdn.example.com --mode cf --cf-token CF_API_TOKEN
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
| `--cf-token TOKEN` | Cloudflare API token (Origin CA, cf mode only) |
| `--cf-zone ZONE_ID` | Cloudflare zone id (optional, auto-detected) |
| `--secret HEX` | proxy secret (default: random) |
| `--carrier MODE` | `https` \| `https-lanes` \| `websocket` \| `websocket-lanes` |
| `--site-dir DIR` | mask the front with a local static site |
| `--site-upstream URL` | mask the front with a loopback upstream origin |

Shared options: `--email-account`, `--mtproxy-workers`, `--mtproxy-max-connections`, `--caddy-version`, `--keep-log`, `--clean`, `--no-dns-check`, `--help`.

---

## Cloudflare setup

Two ways to front an instance with Cloudflare:

**Without a token (manual):**
1. In Cloudflare, set the subdomain record to **DNS only** (proxy off).
2. Run the installer in `cf` mode — it issues a Let's Encrypt certificate.
3. Flip the record to **Proxied** (orange cloud).

**With an API token (automatic):**
1. Create a Cloudflare API token with **Zone.DNS edit** and **SSL and Certificates edit** permissions.
2. Pass it via `--cf-token`. The installer requests an **Origin CA** certificate (15 years) with no manual steps.

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

---

## Uninstall

Every instance registers itself in `/etc/tproxy-server/registry.json`.

- List installed instances: `bash install-tproxy.sh --list`
- Wipe everything (all instances and shared components): `bash install-tproxy.sh --wipe`

To remove just a single instance, stop and disable its systemd services, then delete its profile under `/etc/tproxy-server/<instance>/` and its entry in `registry.json`.

---

## Troubleshooting

- **Install aborts with a clear message** — the script stops on any failed command instead of leaving a half-broken install; read the printed error and check the log file it points to.
- **HTTPS origin not ready yet** — DNS may not have propagated, or the Cloudflare proxy is still off. Wait a few minutes and re-check.
- **Port 443 busy** — something already listens there; free it before installing.

---

## Files

| File                | Purpose                                   |
|---------------------|-------------------------------------------|
| `install-tproxy.sh` | Main installer (interactive + CLI, multi-instance) |
| `README.md`         | This documentation                        |

---

## License

Released under the [MIT License](LICENSE).
