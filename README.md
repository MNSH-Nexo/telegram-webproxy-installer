# Telegram WebProxy Installer

One-click, multi-instance Telegram **web / webapp proxy** installer (MTProxy + Caddy) for Debian/Ubuntu servers. Install and run multiple isolated proxy instances on a single host, each with its own domain, automatic HTTPS via Let's Encrypt, and optional Cloudflare fronting.

---

## Features

- **One-command install** — downloads and configures Go, Caddy, MTProxy and systemd services automatically. No manual prerequisites.
- **Multi-instance** — run as many instances as you want on one server; each is fully isolated (own domain, ports, profiles and services).
- **Automatic HTTPS** — Let's Encrypt certificates issued and renewed by Caddy for every instance.
- **Cloudflare-ready** — direct mode by default, or Cloudflare-fronted: turn the proxy off, the installer issues a Let's Encrypt certificate, then you turn the proxy on.
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

Shared options: `--email-account`, `--mtproxy-workers`, `--mtproxy-max-connections`, `--caddy-version`, `--keep-log`, `--clean`, `--no-dns-check`, `--help`.

---

## Carrier modes

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

You can switch the mode later by re-running the installer with the same hostname and a different `--carrier` (the instance is refreshed in place).

---

## Cloudflare setup

To front an instance with Cloudflare:

1. In Cloudflare, set the subdomain record to **DNS only** (proxy off / gray cloud) **before** installing.
2. Run the installer in `cf` mode. It issues a **Let's Encrypt** certificate while the proxy is off.
3. After the install finishes, flip the record to **Proxied** (orange cloud).

No Cloudflare API token is required.

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

Every instance registers itself in `/etc/tproxy-server/instances.json`.

- List installed instances: `bash install-tproxy.sh --list`
- Wipe everything (all instances and shared components): `bash install-tproxy.sh --wipe`

To remove just a single instance, stop and disable its systemd services, then delete its profile under `/etc/tproxy-server/<instance>/` and its entry in `instances.json`.

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
