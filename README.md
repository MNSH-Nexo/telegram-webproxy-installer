<div align="center">

# Telegram WebProxy Installer

**One-click, multi-instance Telegram web proxy installer — MTProxy + Caddy gateway for Debian/Ubuntu servers.**

Deploy a resilient Telegram **Web / WebApp proxy** on your own server in minutes. Runs multiple isolated proxy instances on a single host, each with its own domain, automatic HTTPS via Let's Encrypt, and optional **Cloudflare** fronting.

</div>

---

## ✨ Features

- 🚀 **One-command install** — downloads and configures Go, Caddy, MTProxy and systemd services automatically. No manual prerequisites.
- 🧩 **Multi-instance** — run as many proxy instances as you want on one server; each is fully isolated (own domain, ports, profiles and services).
- 🔐 **Automatic HTTPS** — Let's Encrypt certificates issued and renewed by Caddy for every instance.
- ☁️ **Cloudflare-ready** — two modes: direct (default) or Cloudflare-fronted with **Origin CA 15-year certificate** when you provide a token, or auto-issued Let's Encrypt when proxying is off.
- 🧭 **Interactive wizard** — if you run it without arguments, it walks you through domain, mode, carrier and email step by step.
- 💬 **Carrier profiles** — choose the best transport (e.g. `https-lanes`) for your region.
- 🛡️ **Hardened by default** — firewall only exposes port 443; MTProxy admin panels stay LAN-only.
- 🔁 **Robust & idempotent** — `set -euo pipefail`, retries for downloads/apt locks, disk & architecture checks, and safe re-runs.

---

## 📋 Requirements

| Requirement | Value |
|-------------|-------|
| **OS** | Debian 11/12 or Ubuntu 20.04+ (systemd + `apt`) |
| **Architecture** | x86_64 / amd64 only |
| **User** | `root` (or `sudo`) |
| **Free ports** | `80` and `443` must be free |
| **DNS** | a domain (or subdomain) pointing at this server's public IP |

> ⚠️ This installs Caddy on port `443`. **Do not** run it on a server where something else (like an xray/SSH-TLS tunnel) already uses `80`/`443`.

---

## 🚀 Quick Start

Copy the script to your server, then run it as **root** (no `sudo` needed):

```bash
bash install-tproxy.sh
```

The interactive wizard will ask you for:

1. **Domain** (or subdomain) pointed to this server — e.g. `proxy.example.com`
2. **Mode** — `direct` or `cf` (Cloudflare)
3. **Carrier** — transport profile, e.g. `https-lanes`
4. **Email** — for Let's Encrypt (leave blank for an auto-generated one)

When it finishes, it prints your **proxy link + QR code** ready to share.

> 💡 Running the wizard again installs an **additional** instance (another domain). Each run = one instance.

---

## 🧑‍💻 CLI / Scripted Usage

For automation or re-runs you can pass arguments instead of using the wizard:

```bash
# Direct instance
bash install-tproxy.sh --hostname proxy.example.com --mode direct --carrier https-lanes --email admin@example.com

# Cloudflare instance WITHOUT API token (proxy stays OFF until you enable it)
bash install-tproxy.sh --hostname cdn.example.com --mode cf

# Cloudflare instance WITH API token (Origin CA, 15-year, no manual steps)
bash install-tproxy.sh --hostname cdn.example.com --mode cf --cf-token CF_API_TOKEN
```

**Actions:**

| Action | Description |
|--------|-------------|
| *(default)* | Install the base system, then add/refresh an instance |
| `--list` | list installed instances |
| `--verify` | show health status of all instances |
| `--wipe` | remove every instance and the shared components |

**Instance options:**

| Flag | Description |
|------|-------------|
| `--hostname HOST` | the domain for this proxy (required) |
| `--name ID` | instance id (default: the hostname) |
| `--email EMAIL` | Let's Encrypt contact email |
| `--mode direct\|cf` | front mode (default: `direct`) |
| `--cf-token TOKEN` | Cloudflare API token → Origin CA (cf mode only) |
| `--cf-zone ZONE_ID` | Cloudflare zone id (optional; auto-detected) |
| `--secret HEX` | proxy secret (default: random) |
| `--carrier MODE` | `https` \| `https-lanes` \| `websocket` \| `websocket-lanes` |
| `--site-dir DIR` | mask the front with a local static site |
| `--site-upstream URL` | mask the front with a loopback upstream origin |

**Shared options:** `--email-account`, `--mtproxy-workers`, `--mtproxy-max-connections`, `--caddy-version`, `--keep-log`, `--clean`, `--no-dns-check`, `--help`.

---

## ☁️ Cloudflare Setup

There are two ways to front an instance with Cloudflare:

**Without a token (manual, recommended for one-time DNS):**
1. In your Cloudflare dashboard, set the record for the subdomain to **DNS only** (proxy **off**).
2. Run the installer in `cf` mode — it issues a Let's Encrypt certificate.
3. Flip the record to **Proxied** (orange cloud).

**With an API token (fully automatic):**
1. Create a Cloudflare API token with **Zone.DNS edit** + **SSL and Certificates edit** permissions.
2. Pass it via `--cf-token`. The installer requests an **Origin CA** certificate (15 years) and needs no manual steps.

---

## 🧩 Architecture (per instance)

A single **Caddy** gateway fronts all instances and routes by **SNI**. Each instance is isolated:

| Component | Port (instance *n*) | Exposure |
|-----------|---------------------|----------|
| Caddy (HTTPS gateway) | `443` | Public |
| Relay | `8080 + 2n` | via Caddy |
| Relay admin | `+1` | LAN-only |
| MTProxy | `2398 + n` | LAN-only (blocked from WAN) |
| MTProxy admin | `8888 + n` | LAN-only |

Profiles live under `/etc/tproxy-server/<instance>/`; every instance has its own systemd services.

---

## 🧹 Uninstall

Every instance registers itself in `/etc/tproxy-server/registry.json`.

- **List** installed instances: `bash install-tproxy.sh --list`
- **Wipe everything** (all instances + shared components): `bash install-tproxy.sh --wipe`

To remove just a single instance, stop and disable its systemd services and delete its profile under `/etc/tproxy-server/<instance>/` and its entry in `registry.json`.

---

## 🐛 Troubleshooting

- **Install aborts with a clear message** — the script stops on any failed command instead of leaving a half-broken install; read the printed error and check the log file it points to.
- **HTTPS origin not ready yet** — DNS may not have propagated, or Cloudflare proxy is still off. Wait a few minutes and re-check.
- **Port 443 busy** — something already listens there; free it before installing.

---

## 📄 Files

| File | Purpose |
|------|---------|
| `install-tproxy.sh` | Main installer (interactive + CLI, multi-instance) |
| `README.md` | This documentation |

---

## 📜 License

Released under the [MIT License](LICENSE).
