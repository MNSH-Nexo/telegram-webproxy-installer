#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

LOG="${TPROXY_LOG:-/root/tproxy-install.log}"
LOG_TTY="$(test -t 1 && echo yes || echo no)"
: > "$LOG"
exec 3>&1
export TPROXY_LOG="$LOG"
run(){ "$@" > >(tee -a "$LOG" >&3) 2>&1; }
log(){ printf '\033[1;34m[*]\033[0m %s\n' "$*" | tee -a "$LOG" >&3; }
ok(){ printf '\033[1;32m[+] \033[0m %s\n' "$*" | tee -a "$LOG" >&3; }
warn(){ printf '\033[1;33m[!] \033[0m %s\n' "$*" | tee -a "$LOG" >&3; }
die(){ printf '\033[1;31m[x] \033[0m %s\n' "$*" | tee -a "$LOG" >&3; exit 1; }
section(){ printf '\n\033[1;35m== %s ==\033[0m\n' "$*" | tee -a "$LOG" >&3; }

declare -a STEP_TITLES=("System" "Caddy gateway" "MTProxy backend" "Go + relay" "Instance config" "Services & certs" "Verify")
STEP_IDX=0
banner(){ { printf '\033[1;36m\n'; printf '  ╔══════════════════════════════════════════════════════════════╗\n'; printf '  ║   Telegram WEB Proxy  ·  Multi-Instance Installer          ║\n'; printf '  ║   MTProxy + Caddy HTTPS + Relay — N domains, one command    ║\n'; printf '  ╚══════════════════════════════════════════════════════════════╝\n'; printf '\033[0m\n'; } | tee -a "$LOG" >&3; }
draw_progress(){ local i n="${#STEP_TITLES[@]}"; (( STEP_IDX >= n )) && STEP_IDX=$((n-1)); printf '\033[1;90m  ────────────────────────────────────────────────\033[0m\n' | tee -a "$LOG" >&3; printf '  \033[1;37mSteps:\033[0m ' | tee -a "$LOG" >&3; for ((i=0;i<n;i++)); do if ((i<STEP_IDX)); then printf '\033[1;32m●\033[0m '; elif ((i==STEP_IDX)); then printf '\033[1;33m◉\033[0m '; else printf '\033[1;90m○\033[0m '; fi; done | tee -a "$LOG" >&3; printf '\n  \033[1;33m▶ Step %d/%d:\033[0m \033[1;37m%s\033[0m\n' "$((STEP_IDX+1))" "$n" "${STEP_TITLES[STEP_IDX]}" | tee -a "$LOG" >&3; }
step_begin(){ draw_progress; printf '  \033[1;35m%s\033[0m\n' "$1" | tee -a "$LOG" >&3; }
step_done(){ STEP_IDX=$((STEP_IDX+1)); printf '  \033[1;32m✔ %s\033[0m\n' "${STEP_TITLES[STEP_IDX-1]}" | tee -a "$LOG" >&3; }
spinner_on(){ SPIN_MSG="$1"; ( while :; do for c in '⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏'; do printf '\r  %s %s ' "$SPIN_MSG" "$c"; sleep 0.1; done; done ) & SPIN_PID=$!; }
spinner_off(){ kill "$SPIN_PID" 2>/dev/null; wait "$SPIN_PID" 2>/dev/null; printf '\r  %s \033[1;32m✔\033[0m\n' "$SPIN_MSG"; }
[[ "$LOG_TTY" == yes ]] && banner

retry(){ local n=1; until "$@"; do ((n>=6)) && return 1; sleep $((n*2)); ((n++)); done; }
wait_locks(){ local n=0; while fuser /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock >/dev/null 2>&1; do ((n++)); ((n>60)) && return 1; sleep 1; done; return 0; }
apt_update(){ wait_locks && retry env DEBIAN_FRONTEND=noninteractive apt-get update; }
apt_install(){ local p; p="$1"; shift; wait_locks && retry env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$p" "$@"; }
check_disk(){ local need="$1" avail; avail="$(df -Pk / | awk 'NR==2{print $4}')"; [[ -n "$avail" && "$avail" -ge "$need" ]] || die "only ${avail}KB free on /; need ${need}KB"; }
port_busy(){ ss -ltn 2>/dev/null | awk '{print $4}' | grep -q ":${1//\//}$"; }
dl(){ local url="$1" out="$2" i; for i in 1 2 3; do if curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' --tlsv1.2 --output "$out" "$url"; then return 0; fi; sleep 3; done; return 1; }
pick_goproxy(){
	local dir bases i t b out line gp
	dir="$(mktemp -d /tmp/gp.XXXXXX)" || return 1
	bases=("https://mirrors.aliyun.com/goproxy/" "https://proxy.golang.com.cn" "https://goproxy.cn" "https://goproxy.io" "https://proxy.golang.org")
	out=""
	for i in "${!bases[@]}"; do : >"$dir/$i"; done
	for i in "${!bases[@]}"; do
		b="${bases[$i]%%/}"
		( t="$(curl -s -o /dev/null -m 4 -w '%{time_total}' "$b/github.com/gorilla/websocket/@v/v1.5.3.mod" 2>/dev/null)"; [[ "$t" =~ ^[0-9.]+$ ]] && echo "$t $b" || echo "999 $b" ) >"$dir/$i" &
	done
	wait
	for i in "${!bases[@]}"; do out="$out
$(<"$dir/$i")"; done
	rm -rf "$dir"
	gp="$(printf '%s\n' "$out" | sed '/^$/d' | sort -n | awk '$1<500{printf ",%s",$2}' | sed 's/^,//')"
	[[ -n "$gp" ]] || gp="https://proxy.golang.org"
	printf '%s\n' "$gp"
}

usage(){
	cat <<'USAGE'
Usage: sudo ./install-tproxy.sh [action] [options]

Actions:
  (default)                    ensure the base system, then add/refresh an instance
  --list                       list installed instances
  --verify                     show health status of all instances
  --wipe                       remove every instance and the shared components
  (no arguments)               install the web panel (asks port + SSL like 3x-ui) or open its menu
  --panel [args]               install/manage the web panel (SSL via domain or IP, see panel.sh)
  --wizard                     old interactive wizard: add one proxy instance without the panel

Instance options:
  --hostname HOST              the domain for this proxy (required for install)
  --name ID                    instance id (default: the hostname)
  --email EMAIL                ACME/Let's Encrypt contact email
  --mode direct|cf             front mode (default: direct)
  --secret HEX                 proxy secret (default: random)
  --carrier MODE               https|https-lanes|websocket|websocket-lanes
  --site-dir DIR               mask the front with a local static site
  --site-upstream URL          mask the front with a loopback upstream origin

Shared options:
  --email-account EMAIL        ACME account email used in the Caddyfile
  --mtproxy-workers N          default 1
  --mtproxy-max-connections N  default 4096
  --caddy-version X.Y.Z        default 2.11.4
  --keep-log                   keep the install log after success
  --clean                      reset resume-state and reinstall the base
  --no-dns-check               skip the DNS preflight warning
  --help                       show this help

Cloudflare note: in cf mode the script issues a Let's Encrypt certificate.
Turn OFF the Cloudflare proxy (orange cloud) for the subdomain BEFORE
installing, then turn it ON (Proxied) after the install completes.
USAGE
}

panel_main(){
	local here ps; here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" 2>/dev/null && pwd)" || here=""
	if [[ -f "$here/panel.sh" ]]; then exec bash "$here/panel.sh" "${@:-install}"; fi
	ps="$(mktemp)"; dl "https://raw.githubusercontent.com/COD-DEXTER/Telegram-WebProxy/main/panel.sh" "$ps" || die "could not download panel.sh"
	exec bash "$ps" "${@:-install}"
}

REPO=/opt/tproxy-multi
REG=/etc/tproxy-server/instances.json
CADDY_DIR=/etc/caddy
SITES_DIR=/etc/caddy/sites
MTPROXY_SRC=/opt/MTProxy

reg_load(){ [[ -f "$REG" ]] && python3 -c 'import json,sys
d=json.load(open(sys.argv[1])); [print(i["name"],i["hostname"],i["mode"],i.get("backend"),i.get("admin"),i.get("mtproxy"),i.get("mtproxy_admin"),i.get("secret_present","0")) for i in d.get("instances",[])]' "$REG" 2>/dev/null || true; }
reg_count(){ [[ -f "$REG" ]] && python3 -c 'import json,sys
print(len(json.load(open(sys.argv[1])).get("instances",[])))' "$REG" 2>/dev/null || echo 0; }
reg_next_idx(){ python3 - "$REG" "$1" <<'PY'
import json,sys,os
reg,name=sys.argv[1:]
used=set(); own=None
try: inst=json.load(open(reg)).get("instances",[])
except Exception: inst=[]
def n(v):
    try: return int(v)
    except Exception: return None
for i in inst:
    b=n(i.get("backend"))
    if i.get("name")==name and b is not None and b>=8080: own=(b-8080)//2
    elif b is not None: used.update([b,b+1,n(i.get("mtproxy")),n(i.get("mtproxy_admin"))])
if own is not None: print(own); raise SystemExit
k=0
while {8080+2*k,8081+2*k,2398+k,8888+k}&used: k+=1
print(k)
PY
}
reg_add(){ python3 - "$1" "$2" "$3" "$4" "$5" "$6" "$7" "$8" "$9" <<'PY'
import json,sys,os
reg,path,host,mode,backend,admin,mtp,mtpa,secret_present=sys.argv[1:]
d={"instances":[]}
if os.path.exists(reg):
    try: d=json.load(open(reg))
    except Exception: d={"instances":[]}
    d.setdefault("instances",[]).__delitem__ if False else None
d["instances"]=[i for i in d.get("instances",[]) if i.get("name")!=path]
d["instances"].append({"name":path,"hostname":host,"mode":mode,"backend":backend,"admin":admin,"mtproxy":mtp,"mtproxy_admin":mtpa,"secret_present":secret_present})
os.makedirs(os.path.dirname(reg),exist_ok=True)
json.dump(d,open(reg,"w"),indent=2)
PY
}
reg_rm(){ python3 - "$1" "$2" <<'PY'
import json,sys,os
reg,name=sys.argv[1:]
if not os.path.exists(reg): raise SystemExit
d=json.load(open(reg)); d["instances"]=[i for i in d.get("instances",[]) if i.get("name")!=name]
json.dump(d,open(reg,"w"),indent=2)
PY
}

action=install
hostname= name= email= mode=direct secret= carrier=
site_dir= site_upstream= email_account= keep_log=0 clean=0 check_dns=1
mtproxy_workers=1 mtproxy_max_connections=4096 caddy_version=2.11.4

[[ $# -eq 0 ]] && { panel_main auto; exit $?; }
while [[ $# -gt 0 ]]; do
	case "$1" in
		--wizard) shift ;;
		--list) action=list; shift ;;
		--verify) action=verify; shift ;;
		--wipe) action=wipe; shift ;;
		--panel) shift; panel_main "$@"; exit $? ;;
		--hostname) hostname="${2:-}"; shift 2 ;;
		--name) name="${2:-}"; shift 2 ;;
		--email) email="${2:-}"; shift 2 ;;
		--email-account) email_account="${2:-}"; shift 2 ;;
		--mode) mode="${2:-}"; shift 2 ;;
		--secret) secret="${2:-}"; shift 2 ;;
		--carrier) carrier="${2:-}"; shift 2 ;;
		--site-dir) site_dir="${2:-}"; shift 2 ;;
		--site-upstream) site_upstream="${2:-}"; shift 2 ;;
		--mtproxy-workers) mtproxy_workers="${2:-}"; shift 2 ;;
		--mtproxy-max-connections) mtproxy_max_connections="${2:-}"; shift 2 ;;
		--caddy-version) caddy_version="${2:-}"; shift 2 ;;
		--keep-log) keep_log=1; shift ;;
		--clean) clean=1; shift ;;
		--no-dns-check) check_dns=0; shift ;;
		--help) usage; exit 0 ;;
		*) usage; die "unknown option: $1" ;;
	esac
done

[[ "${EUID}" -eq 0 ]] || die "run as root"
[[ "$(uname -m)" == "x86_64" ]] || die "the stock MTProxy build requires x86_64"

if [[ "$action" == "list" ]]; then
	if [[ -f "$REG" ]] && [[ -s "$REG" ]]; then
		echo "Installed instances:"
		reg_load | while read -r nm hn m bk ad mt mta sp; do printf '  %-20s %-28s mode=%s backend=%s mtproxy=%s\n' "$nm" "$hn" "$m" "$bk" "$mt"; done
	else
		echo "No instances installed yet."
	fi
	exit 0
fi

if [[ "$action" == "verify" ]]; then
	echo "Checking instances:"
	while read -r nm hn m bk ad mt mta sp; do
		[[ -n "$nm" ]] || continue
		tup="systemctl is-active tproxy@$nm.service mtproxy@$nm.service caddy.service 2>/dev/null | tr '\n' ' '"
		relay="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:$ad/readyz" 2>/dev/null || echo down)"
		https="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 5 --resolve "$hn:443:127.0.0.1" "https://$hn/" 2>/dev/null || echo down)"
		met="$(curl -s --max-time 3 "http://127.0.0.1:$ad/metrics" 2>/dev/null || true)"
		live="$(printf '%s\n' "$met" | awk '$1=="tproxy_sessions_live"{print $2}')"; [[ -n "$live" ]] || live="-"
		up="$(printf '%s\n' "$met" | awk '$1=="tproxy_bytes_up_total"{print $2}')"; down="$(printf '%s\n' "$met" | awk '$1=="tproxy_bytes_down_total"{print $2}')"; fl="$(printf '%s\n' "$met" | awk '$1=="tproxy_backend_dial_failures_total"{print $2}')"; [[ -n "$fl" ]] || fl="-"
		printf '  %-20s relay=%-14s https=%-8s live=%-6s up=%-12s down=%-12s dial_failures=%s\n' "$nm" "$relay" "$https" "$live" "$up" "$down" "$fl"
		journalctl -u "tproxy@$nm.service" -n 3 --no-pager 2>/dev/null | sed 's/^/      | /'
	done < <(reg_load)
	echo "(caddy/systemd status not shown here; run 'systemctl status caddy' for detail)"
	exit 0
fi

if [[ "$action" == "wipe" ]]; then
	section "Wiping all instances"
	while read -r nm hn m bk ad mt mta sp; do
		[[ -n "$nm" ]] || continue
		systemctl disable --now "tproxy@$nm.service" "mtproxy@$nm.service" 2>/dev/null || true
		rm -rf "/etc/tproxy-server/$nm" "/etc/mtproxy/$nm.env" "$CADDY_DIR/$nm-origin.pem" "$CADDY_DIR/$nm-origin.key"
		rm -f "$SITES_DIR/$nm.caddy"
		ok "removed instance $nm"
	done < <(reg_load)
	systemctl disable --now caddy.service tproxy-firewall.service refresh-mtproxy-config.timer 2>/dev/null || true
	rm -f /etc/caddy/Caddyfile /etc/systemd/system/tproxy@.service /etc/systemd/system/mtproxy@.service "$REG"
	rm -rf "$SITES_DIR" /srv/tproxy-site-*
	systemctl daemon-reload
	ok "wipe complete (binaries left under $REPO / /usr/local/bin). Re-run to reinstall."
	exit 0
fi

if [[ "$action" == install && -z "$hostname" ]]; then
	printf '\n  \033[1;36mInteractive install\033[0m — just answer a few questions.\n'
	while [[ -z "$hostname" ]]; do
		read -rp "  Domain for this proxy (e.g. proxy.example.com): " hostname
		[[ -n "$hostname" ]] || echo "  (domain is required)"
	done
	read -rp "  Front mode [direct/cf] (default: direct): " _m
	case "${_m,,}" in cf) mode=cf;; *) mode=direct;; esac
	if [[ "$mode" == cf ]]; then
		printf '\n  \033[1;33mImportant:\033[0m before continuing, turn OFF the Cloudflare proxy\n  (orange cloud) for %s. Run the install now; after it finishes,\n  you will turn the proxy back ON.\n' "$hostname"
	fi
	read -rp "  Carrier [https-lanes/https/websocket/websocket-lanes] (default: https-lanes): " _c
	case "${_c,,}" in https|websocket|websocket-lanes) carrier="${_c,,}";; *) carrier=https-lanes;; esac
	printf '\n'
fi
[[ -z "$hostname" ]] && die "--hostname is required (or use --list / --verify / --wipe)"
mode="${mode:-direct}"
[[ "$mode" == direct || "$mode" == cf ]] || die "--mode must be direct or cf"
[[ -n "$email" ]] || email="$email_account"
if [[ -z "$email" ]]; then email="admin@${hostname#*.}"; [[ "$email" == *.* ]] || email="admin@$(hostname)"; fi
[[ -n "$name" ]] || name="$hostname"
name="${name//[^a-zA-Z0-9_.-]/_}"
[[ -n "$site_dir" && -n "$site_upstream" ]] && die "--site-dir and --site-upstream are mutually exclusive"
if [[ -n "$site_dir" ]]; then [[ -d "$site_dir" && -f "$site_dir/index.html" ]] || die "site dir must contain index.html"; fi

if [[ "$check_dns" == 1 ]] && command -v getent >/dev/null 2>&1; then
	if [[ "$mode" == cf ]]; then
		printf '\n  \033[1;33mImportant:\033[0m before continuing, turn OFF the Cloudflare proxy\n  (orange cloud) for %s, then press any key to proceed.\n' "$hostname"
		read -rsn1 -p "  Press Enter when the proxy is OFF... " _
		printf '\n'
		ok "Cloudflare mode — issuing a Let's Encrypt cert while the proxy is OFF; you will turn it ON after install."
	elif [[ -z "$(getent ahosts "$hostname" 2>/dev/null | awk 'NR==1{print $1}')" ]]; then
		warn "DNS does not yet resolve ${hostname}; Let's Encrypt will wait for it."
	fi
fi

idx="$(reg_next_idx "$name")"
backend=$((8080 + 2 * idx)); admin=$((backend + 1))
mtp=$((2398 + idx)); mtpa=$((8888 + idx))

step_begin "7  System packages, users, directories"
export DEBIAN_FRONTEND=noninteractive
apt_update
apt_install ca-certificates curl nftables openssl git build-essential libssl-dev util-linux zlib1g-dev psmisc qrencode python3 jq
if ! id caddy  >/dev/null 2>&1; then useradd --system --home /var/lib/caddy  --shell /usr/sbin/nologin caddy; fi
if ! id tproxy >/dev/null 2>&1; then useradd --system --home /nonexistent   --shell /usr/sbin/nologin tproxy; fi
if ! id mtproxy>/dev/null 2>&1; then useradd --system --home /nonexistent   --shell /usr/sbin/nologin mtproxy; fi
install -d -o root -g caddy -m 0750 /etc/caddy
install -d -o caddy -g caddy -m 0750 /var/lib/caddy
install -d -o root -g tproxy -m 0750 /etc/tproxy-server
install -d -o root -g mtproxy -m 0750 /etc/mtproxy
mkdir -p "$SITES_DIR"
chown root:caddy "$SITES_DIR"
chmod 0755 "$SITES_DIR"
ok "System packages and users ready"
step_done

step_begin "7  Caddy HTTPS gateway"
if [[ ! -x /usr/local/bin/caddy ]] || [[ "$clean" == 1 ]]; then
	check_disk 60000
	caddy_checksum=8220d1f013b6f27510247b2360c9e0ca9f018feebd82515f07635318b34ff9777ccc8fd0b6e6f2486ce3a33fe389fbb7db12d05baa474f4587509fb4f5ebf1c9
	tmp="$(mktemp -d /tmp/caddy.XXXXXX)"
	dl "https://github.com/caddyserver/caddy/releases/download/v${caddy_version}/caddy_${caddy_version}_linux_amd64.tar.gz" "$tmp/c.tar.gz"
	echo "$caddy_checksum  $tmp/c.tar.gz" | sha512sum -c - >/dev/null || die "caddy checksum mismatch"
	tar -C "$tmp" -xzf "$tmp/c.tar.gz"
	install -m 0755 "$tmp/caddy" /usr/local/bin/caddy
	rm -rf "$tmp"
fi
rm -rf /etc/systemd/system/caddy.service.d
install -d -m 0755 /etc/systemd/system/caddy.service.d
cat > /etc/caddy/Caddyfile <<'CADDY'
{
	admin off
	servers {
		protocols h1 h2
		timeouts {
			read_header 10s
			read_body 60s
		}
	}
}

import /etc/caddy/sites/*.caddy
CADDY
chown root:caddy /etc/caddy/Caddyfile
chmod 0644 /etc/caddy/Caddyfile
cat > /etc/systemd/system/caddy.service <<'UNIT'
[Unit]
Description=Caddy HTTPS web server
After=network-online.target
Wants=network-online.target
[Service]
Type=notify
User=caddy
Group=caddy
Environment=HOME=/var/lib/caddy
Environment=XDG_DATA_HOME=/var/lib/caddy
Environment=XDG_CONFIG_HOME=/etc/caddy
ExecStart=/usr/local/bin/caddy run --environ --config /etc/caddy/Caddyfile
Restart=on-failure
RestartSec=3s
TimeoutStopSec=10s
LimitNOFILE=1048576
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
PrivateDevices=true
PrivateTmp=true
ProtectHome=true
ProtectProc=invisible
ProtectSystem=full
ProcSubset=pid
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
RestrictNamespaces=true
RestrictRealtime=true
LockPersonality=true
[Install]
WantedBy=multi-user.target
UNIT
ok "Caddy ready"
step_done

step_begin "7  Telegram MTProxy backend"
check_disk 300000
if [[ ! -x "$MTPROXY_SRC/objs/bin/mtproto-proxy" ]]; then
	mtproxy_commit=f36d8af769ffaeac36978d38c2c0f6d1104c2137
	mtproxy_checksum=919795c416b870670841a21d1930ad97a24c7b84b9eb8c6f9e3de32f2fdf4655
	mtproxy_prebuilt_url=https://github.com/MNSH-Nexo/telegram-webproxy-installer/releases/latest/download/mtproto-proxy-x86_64
	mtproxy_prebuilt_sha=409a684c2427d2ab4040839a7c1b8f6fa0e9a9040e12f44807bf870d8a2214ed
	mtproxy_ram_gb="$(awk '/MemTotal/{printf "%d", $2/1024/1024}' /proc/meminfo 2>/dev/null || echo 0)"
	mtproxy_jobs="$(nproc 2>/dev/null || echo 1)"; [[ "$mtproxy_ram_gb" -ge 1 ]] && mtproxy_jobs="$(( mtproxy_jobs < mtproxy_ram_gb ? mtproxy_jobs : mtproxy_ram_gb ))"; [[ "$mtproxy_jobs" -lt 1 ]] && mtproxy_jobs=1
	mkdir -p "$MTPROXY_SRC/objs/bin"
	tmp="$(mktemp -d /tmp/mtproxy.XXXXXX)"; chmod 0711 "$tmp"
	dl "$mtproxy_prebuilt_url" "$tmp/mtproto-proxy" || true
	if [[ -s "$tmp/mtproto-proxy" ]] && echo "$mtproxy_prebuilt_sha  $tmp/mtproto-proxy" | sha256sum -c - >/dev/null 2>&1; then
		chmod 0755 "$tmp/mtproto-proxy"
		ok "using prebuilt mtproto-proxy binary (source compile skipped)"
		cp -f "$tmp/mtproto-proxy" "$MTPROXY_SRC/objs/bin/mtproto-proxy"
		rm -rf "$tmp"
	else
		rm -f "$tmp/mtproto-proxy"
		dl "https://github.com/TelegramMessenger/MTProxy/archive/${mtproxy_commit}.tar.gz" "$tmp/MTProxy.tar.gz" || die "could not download MTProxy source"
		echo "$mtproxy_checksum  $tmp/MTProxy.tar.gz" | sha256sum -c - >/dev/null || die "MTProxy source checksum mismatch"
		mkdir -p "$tmp/MTProxy"; tar -C "$tmp/MTProxy" --strip-components=1 -xzf "$tmp/MTProxy.tar.gz"
		chown -R mtproxy:mtproxy "$tmp/MTProxy"
		printf '  Compiling MTProxy from source (-j%s, log: %s)\n' "$mtproxy_jobs" "$LOG" | tee -a "$LOG" >&3
		if ! runuser -u mtproxy -- make -C "$tmp/MTProxy" -j"$mtproxy_jobs" >>"$LOG" 2>&1; then
			warn "first compile attempt failed; retrying once"
			if ! runuser -u mtproxy -- make -C "$tmp/MTProxy" -j1 >>"$LOG" 2>&1; then
				printf '  Last 25 lines of the build log (%s):\n' "$LOG" | tee -a "$LOG" >&3
				tail -n 25 "$LOG" | tee -a "$LOG" >&3
				die "MTProxy source compile failed; see the log above"
			fi
		fi
		test -x "$tmp/MTProxy/objs/bin/mtproto-proxy" || die "mtproto-proxy binary was not built"
		cp -f "$tmp/MTProxy/objs/bin/mtproto-proxy" "$MTPROXY_SRC/objs/bin/mtproto-proxy"
		rm -rf "$tmp"
	fi
	chown root:root "$MTPROXY_SRC/objs/bin/mtproto-proxy"; chmod 0755 "$MTPROXY_SRC/objs/bin/mtproto-proxy"
	chmod 0755 "$MTPROXY_SRC" "$MTPROXY_SRC/objs" "$MTPROXY_SRC/objs/bin"
fi
if [[ ! -f /etc/mtproxy/proxy-secret ]] || [[ ! -f /etc/mtproxy/proxy-multi.conf ]]; then
	s="$(mktemp /etc/mtproxy/proxy-secret.XXXXXX)"; c="$(mktemp /etc/mtproxy/proxy-multi.conf.XXXXXX)"
	dl https://core.telegram.org/getProxySecret "$s" || die "could not fetch proxy secret"
	dl https://core.telegram.org/getProxyConfig "$c" || die "could not fetch proxy config"
	[[ "$(wc -c < "$s")" -eq 128 ]] && [[ "$(wc -c < "$c")" -ge 100 ]] || die "bad proxy secret/config"
	chown root:mtproxy "$s" "$c"; chmod 0640 "$s" "$c"; mv -f "$s" /etc/mtproxy/proxy-secret; mv -f "$c" /etc/mtproxy/proxy-multi.conf
fi
cat > /etc/systemd/system/mtproxy@.service <<'UNIT'
[Unit]
Description=Telegram MTProxy backend instance %i
After=network-online.target tproxy-firewall.service
Wants=network-online.target
Requires=tproxy-firewall.service
[Service]
Type=simple
User=mtproxy
Group=mtproxy
EnvironmentFile=/etc/mtproxy/%i.env
WorkingDirectory=/opt/MTProxy
ExecStart=/opt/MTProxy/objs/bin/mtproto-proxy -u mtproxy -p ${MTPROXY_ADMIN} -H ${MTPROXY_PORT} -S ${MTPROXY_SECRET} --aes-pwd /etc/mtproxy/proxy-secret /etc/mtproxy/proxy-multi.conf -M ${MTPROXY_WORKERS} -C ${MTPROXY_MAX_CONNECTIONS}
Restart=on-failure
RestartSec=3s
LimitNOFILE=1048576
NoNewPrivileges=true
PrivateDevices=true
PrivateTmp=true
ProtectHome=true
ProtectProc=invisible
ProtectSystem=strict
ProcSubset=pid
ReadOnlyPaths=/etc/mtproxy
RestrictAddressFamilies=AF_INET AF_INET6
RestrictNamespaces=true
RestrictRealtime=true
LockPersonality=true
[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/tproxy@.service <<'UNIT'
[Unit]
Description=Browser HTTPS transport relay instance %i
After=network-online.target mtproxy@%i.service tproxy-firewall.service
Wants=network-online.target mtproxy@%i.service
Requires=tproxy-firewall.service
[Service]
Type=simple
User=tproxy
Group=tproxy
LoadCredential=profiles.json:/etc/tproxy-server/%i/profiles.json
ExecStart=/usr/local/bin/tproxy-server -config /etc/tproxy-server/%i/config.json
Restart=on-failure
RestartSec=3s
TimeoutStopSec=20s
LimitNOFILE=1048576
NoNewPrivileges=true
PrivateDevices=true
PrivateTmp=true
ProtectClock=true
ProtectControlGroups=true
ProtectHome=true
ProtectHostname=true
ProtectKernelLogs=true
ProtectKernelModules=true
ProtectKernelTunables=true
ProtectProc=invisible
ProtectSystem=strict
ProcSubset=pid
RestrictAddressFamilies=AF_INET AF_INET6
RestrictNamespaces=true
RestrictRealtime=true
RestrictSUIDSGID=true
LockPersonality=true
MemoryDenyWriteExecute=true
CapabilityBoundingSet=
IPAddressDeny=any
IPAddressAllow=localhost
SystemCallArchitectures=native
SystemCallFilter=@system-service
UMask=0077
[Install]
WantedBy=multi-user.target
UNIT
ok "MTProxy backend ready"
step_done

step_begin "7  Go toolchain + relay"
if [[ ! -x /usr/local/bin/tproxy-server ]] || [[ "$clean" == 1 ]]; then
	check_disk 400000
	tproxy_commit=2873a08806d6e4d84830b9b5c4b0ec0f46af91f8
	tproxy_prebuilt_url=https://github.com/MNSH-Nexo/telegram-webproxy-installer/releases/latest/download/tproxy-server-x86_64
	tproxy_prebuilt_sha=c0fd44a8c554e11a82e0aa18cc73ffab4c224d4a51663a4653b7e3ad767dd8d8
	tproxy_got=
	go_version=1.26.5; go_checksum=5c2c3b16caefa1d968a94c1daca04a7ca301a496d9b086e17ad77bb81393f053
	go_binary=
	if command -v go >/dev/null 2>&1; then minor="$(go env GOVERSION | sed -E 's/^go1\.([0-9]+).*/\1/')"; [[ "$minor" =~ ^[0-9]+$ && "$minor" -ge 20 ]] && go_binary="$(command -v go)"; fi
	if [[ -z "$go_binary" ]]; then
		tmp="$(mktemp -d /tmp/go.XXXXXX)"; dl "https://go.dev/dl/go${go_version}.linux-amd64.tar.gz" "$tmp/go.tar.gz"
		echo "$go_checksum  $tmp/go.tar.gz" | sha256sum -c - >/dev/null || die "go checksum mismatch"
		tar -C "$tmp" -xzf "$tmp/go.tar.gz"
		if [[ -e "/opt/go${go_version}" ]]; then rm -rf "$tmp/go"; else mv "$tmp/go" "/opt/go${go_version}"; fi
		go_binary="/opt/go${go_version}/bin/go"; rm -rf "$tmp"
	fi
	if [[ -d "$REPO" && ! -d "$REPO/.git" ]]; then rm -rf "$REPO"; fi
	if [[ ! -d "$REPO/.git" ]]; then
		dl "https://github.com/telegramdesktop/tproxy-server/archive/${tproxy_commit}.tar.gz" "/tmp/tproxy-src.tar.gz" || true
		if [[ -s /tmp/tproxy-src.tar.gz ]]; then
			mkdir -p "$REPO"
			tar -C "$REPO" --strip-components=1 -xzf /tmp/tproxy-src.tar.gz 2>/dev/null || rm -rf "$REPO"/*
			rm -f /tmp/tproxy-src.tar.gz
		fi
	fi
	if [[ -f "$REPO/go.mod" ]] && (cd "$REPO" && GOPROXY="$(pick_goproxy),direct" "$go_binary" build -trimpath -ldflags='-s -w' -o /usr/local/bin/tproxy-server ./cmd/tproxy-server); then
		ok "built tproxy-server relay from source"
		tproxy_got=1
	else
		warn "relay source build failed; falling back to prebuilt binary"
	fi
	if [[ -z "$tproxy_got" ]]; then
		ptmp="$(mktemp -d /tmp/tproxy.XXXXXX)"; chmod 0711 "$ptmp"
		dl "$tproxy_prebuilt_url" "$ptmp/tproxy-server" || true
		if [[ -s "$ptmp/tproxy-server" ]] && echo "$tproxy_prebuilt_sha  $ptmp/tproxy-server" | sha256sum -c - >/dev/null 2>&1; then
			chmod 0755 "$ptmp/tproxy-server"
			cp -f "$ptmp/tproxy-server" /usr/local/bin/tproxy-server
			tproxy_got=1
		fi
		rm -rf "$ptmp"
	fi
	[[ "$tproxy_got" == 1 ]] || die "relay could not be built from source or fetched as a prebuilt binary"
	chown root:root /usr/local/bin/tproxy-server; chmod 0755 /usr/local/bin/tproxy-server
fi
ok "Relay ready"
step_done

cat > /etc/systemd/system/tproxy-firewall.service <<'UNIT'
[Unit]
Description=Configure local proxy backend ports
After=nftables.service
PartOf=nftables.service
Before=mtproxy@.service tproxy@.service
[Service]
Type=oneshot
RemainAfterExit=true
ExecStart=-/usr/sbin/nft delete table inet tproxy_backend
ExecStart=/usr/sbin/nft -f /etc/tproxy-server/firewall.nft
ExecReload=-/usr/sbin/nft delete table inet tproxy_backend
ExecReload=/usr/sbin/nft -f /etc/tproxy-server/firewall.nft
ExecStop=-/usr/sbin/nft delete table inet tproxy_backend
[Install]
WantedBy=multi-user.target
UNIT

step_begin "7  Instance config ($hostname, mode=$mode)"
inst_dir="/etc/tproxy-server/$name"
install -d -o root -g tproxy -m 0750 "$inst_dir"
install -d -o root -g tproxy -m 0755 "/srv/tproxy-site-$name"

if [[ -z "$secret" ]]; then
	raw="$(openssl rand -hex 16)"
	secret="${raw}"
fi
if [[ "$secret" != dd* && ${#secret} -eq 34 ]]; then secret="dd${secret}"; fi
backend_secret="$secret"; [[ "$backend_secret" == dd* ]] && backend_secret="${backend_secret:2}"
if [[ ! "$secret" =~ ^(dd)?[0-9a-f]{32}$ ]]; then die "secret must be 32 hex chars, optionally prefixed with dd"; fi

if [[ -n "$site_dir" ]]; then
	cp -a "$site_dir/." "/srv/tproxy-site-$name/" 2>/dev/null || true
	public_source="  \"public_dir\": \"/srv/tproxy-site-$name\","
elif [[ -n "$site_upstream" ]]; then
	public_source="  \"public_upstream\": \"$site_upstream\","
else
	cat > "/srv/tproxy-site-$name/index.html" <<'HTML'
<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Service</title><style>body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#f6f7f9;color:#222;display:flex;align-items:center;justify-content:center;min-height:90vh;margin:0}
.card{background:#fff;padding:40px;border-radius:14px;box-shadow:0 8px 30px rgba(0,0,0,.08);text-align:center;max-width:420px}</style></head>
<body><div class="card"><h1>Service is online</h1><p>This site is operated by the network team. If you reached it directly, everything is working normally.</p></div></body></html>
HTML
	public_source="  \"public_dir\": \"/srv/tproxy-site-$name\","
fi
chown -R root:tproxy "/srv/tproxy-site-$name" 2>/dev/null || true
find "/srv/tproxy-site-$name" -type f -exec chmod 0644 {} + 2>/dev/null || true

cat > "$inst_dir/config.json" <<EOF
{
  "public_hostname": "$hostname",
  "listen": "127.0.0.1:$backend",
  "admin_listen": "127.0.0.1:$admin",
$public_source
  "profiles_file": "/run/credentials/tproxy@$name.service/profiles.json"
}
EOF
cat > "$inst_dir/profiles.json" <<EOF
{"profiles":[{"name":"default","secret":"$secret","backend":"127.0.0.1:$mtp","carrier_mode":"${carrier:-https}"}]}
EOF
chown root:tproxy "$inst_dir/config.json" "$inst_dir/profiles.json"
chmod 0640 "$inst_dir/config.json"; chmod 0400 "$inst_dir/profiles.json"

cat > "/etc/mtproxy/$name.env" <<EOF
MTPROXY_SECRET=$backend_secret
MTPROXY_PORT=$mtp
MTPROXY_ADMIN=$mtpa
MTPROXY_WORKERS=$mtproxy_workers
MTPROXY_MAX_CONNECTIONS=$mtproxy_max_connections
EOF
chown root:mtproxy "/etc/mtproxy/$name.env"; chmod 0640 "/etc/mtproxy/$name.env"

/usr/local/bin/tproxy-server -config "$inst_dir/config.json" -profiles-file "$inst_dir/profiles.json" -check
ok "instance config written ($backend / admin $admin / mtproxy $mtp)"
step_done

step_begin "7  Certificate ($hostname)"
use_le=1
{
	cat <<CADDY
$hostname {
	encode zstd gzip
	header Strict-Transport-Security "max-age=31536000; includeSubDomains"
CADDY
	cat <<CADDY
	reverse_proxy 127.0.0.1:$backend {
		transport http {
			response_header_timeout 40s
		}
	}
	handle_errors {
		header {
			Cache-Control "no-store"
			Content-Security-Policy "default-src 'self'; style-src 'self'; img-src 'self'; worker-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
			Permissions-Policy "camera=(), microphone=(), geolocation=()"
			Referrer-Policy "strict-origin-when-cross-origin"
			X-Content-Type-Options "nosniff"
			X-Frame-Options "DENY"
			Strict-Transport-Security "max-age=31536000; includeSubDomains"
		}
		respond "{http.error.status_code} {http.error.status_text}" {http.error.status_code}
	}
}
CADDY
} > "$SITES_DIR/$name.caddy"
chown root:caddy "$SITES_DIR/$name.caddy"
chmod 0644 "$SITES_DIR/$name.caddy"

if [[ "$use_le" == 1 && -n "$email" ]]; then
	grep -q '^\s*email ' /etc/caddy/Caddyfile 2>/dev/null || sed -i "s/\tadmin off/\tadmin off\n\temail $email/" /etc/caddy/Caddyfile
fi
cert_desc="Let's Encrypt via Caddy"
ok "certificate prepared ($cert_desc)"
step_done

step_begin "7  Services & firewall"
systemctl daemon-reload
reg_add "$REG" "$name" "$hostname" "$mode" "$backend" "$admin" "$mtp" "$mtpa" "0"
{
	cat <<'NFT'
table inet tproxy_backend {
	chain local_backend {
		type filter hook input priority -10; policy accept;
		iifname != "lo" tcp dport { 
NFT
	while read -r _ _ _ _ _ mt mta _; do [[ -n "$mt" ]] && printf '%s, %s, ' "$mt" "$mta"; done < <(reg_load)
	printf ' } drop\n}\n}\n'
} > /etc/tproxy-server/firewall.nft
systemctl enable tproxy-firewall.service >/dev/null 2>&1 || true
systemctl restart tproxy-firewall.service
systemctl enable --now "mtproxy@$name.service"
systemctl restart "mtproxy@$name.service"
sleep 1
systemctl enable --now "tproxy@$name.service"
systemctl enable --now caddy.service
systemctl reload caddy.service 2>/dev/null || systemctl restart caddy.service
ok "services started"
step_done

step_begin "7  Final verification"
relay_ok=0
for ((a=0;a<20;a++)); do
	if curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$admin/readyz"; then relay_ok=1; break; fi
	sleep 1
done
[[ "$relay_ok" == 1 ]] || die "relay did not become ready (check: journalctl -u tproxy@$name -n 50)"
ok "relay ready (admin port $admin)"

https_ok=0
for ((a=0;a<40;a++)); do
	if curl -sk -o /dev/null --max-time 6 --resolve "$hostname:443:127.0.0.1" "https://$hostname/"; then https_ok=1; break; fi
	sleep 3
done
if [[ "$https_ok" == 1 ]]; then
	ok "origin serves HTTPS for $hostname"
else
	warn "origin not yet serving HTTPS for $hostname — check DNS & caddy logs:"
	journalctl -u caddy --no-pager -n 30 2>/dev/null | tail -n 30 | tee -a "$LOG" >&3
fi
step_done

[[ "$keep_log" == 1 ]] || rm -f "$LOG"

if [[ "$mode" == cf ]]; then front_desc="Cloudflare front (origin hidden)"; else front_desc="Direct HTTPS (Let's Encrypt)"; fi
share_url="https://t.me/webproxy?server=$hostname&secret=$secret"
{
	printf '\033[1;32m\n  ╔══════════════════════════════════════════════════════════════╗\n'
	printf '\033[1;32m  ║          ✅  INSTALL COMPLETE — PROXY IS LIVE          ║\n'
	printf '\033[1;32m  ╠══════════════════════════════════════════════════════════════╣\n'
	printf '\033[1;32m  ║\033[0m  \033[1;37mWeb site:\033[0m   %-48s\033[1;32m║\n' "https://$hostname/"
	printf '\033[1;32m  ║\033[0m  \033[1;37mFront:\033[0m     %-48s\033[1;32m║\n' "$front_desc"
	printf '\033[1;32m  ║\033[0m  \033[1;37mHostname:\033[0m  %-48s\033[1;32m║\n' "$hostname"
	printf '\033[1;32m  ║\033[0m  \033[1;37mPort:\033[0m      %-48s\033[1;32m║\n' "443"
	printf '\033[1;32m  ║\033[0m  \033[1;37mSecret:\033[0m    %-48s\033[1;32m║\n' "$secret"
	printf '\033[1;32m  ╚══════════════════════════════════════════════════════════════╝\n\033[0m\n'
	printf '\033[1;36m  ═══════════════════════════════════════════════════════════════\n'
	printf '\033[1;36m   SHARE LINK  —  open it in Telegram to connect\033[0m\n'
	printf '\033[1;36m  ═══════════════════════════════════════════════════════════════\n'
	printf '\033[1;97;44m  %s  \033[0m\n\n' "$share_url"
} | tee -a /dev/null >&3
if command -v qrencode >/dev/null 2>&1; then
	printf '  Scan this QR to open the link in Telegram:\n\n'
	qrencode -t ANSIUTF8 -m 1 -s 1 -o - "$share_url" 2>/dev/null | sed 's/^/  /'
	printf '\n'
fi
if [[ "$mode" == cf ]]; then
	printf '\n\033[1;33m  ⭐ ACTION REQUIRED:\033[0m turn ON the Cloudflare proxy (orange/Proxy)\n'
	printf '  for \033[1;37m%s\033[0m now. Then re-run to confirm:\n' "$hostname"
	printf '      sudo ./install-tproxy.sh --verify\n'
fi
echo
