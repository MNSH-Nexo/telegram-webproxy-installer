#!/usr/bin/env bash
# DX WebProxy Panel manager.
# The install flow, the SSL setup (Let's Encrypt for a domain / for the server IP / custom files / skip),
# the final credentials summary and the control menu are ported from the 3x-ui installer (install.sh)
# and x-ui.sh. One adaptation: the Caddy gateway of this project owns ports 80/443, so Caddy is paused
# for a few seconds only while acme.sh needs port 80, then started again.
set -Eeuo pipefail
umask 022
# systemd starts the panel (and so every job it runs) without HOME; acme.sh and the paths below need it
export HOME="${HOME:-/root}"
# box drawing needs a UTF-8 locale to measure widths correctly
if locale -a 2>/dev/null | grep -qi '^c\.utf-\?8$'; then export LC_ALL=C.UTF-8; fi

PANEL_DIR=/opt/dxwp-panel
CONF_DIR=/etc/dxwp-panel
SERVICE=dxwp-panel
PY="$PANEL_DIR/dxwp_panel.py"
ACME="$HOME/.acme.sh/acme.sh"
CERT_DIR=/root/cert
CADDY_FLAG=/run/dxwp-caddy-paused
TARBALL="${DXWP_TARBALL:-https://github.com/MNSH-Nexo/telegram-webproxy-installer/archive/refs/heads/main.tar.gz}"
NONINT="${DXWP_NONINTERACTIVE:-0}"
SELF_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" 2>/dev/null && pwd)" || SELF_DIR=""

red=$'\033[0;31m'; green=$'\033[0;32m'; yellow=$'\033[0;33m'; blue=$'\033[0;34m'; plain=$'\033[0m'
SSL_SCHEME=https; SSL_HOST=""; SSL_ISSUED_DOMAIN=""; WEBPORT=80
info() { echo -e "${green}$*${plain}"; }
warn() { echo -e "${yellow}$*${plain}"; }
err() { echo -e "${red}$*${plain}" >&2; }
need_root() { [[ $EUID -eq 0 ]] || { err "run as root"; exit 1; }; }
interactive() { [[ "$NONINT" != 1 && -t 0 ]]; }

# ------------------------------------------------------------------ console look (menu + command list)
W=62
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
	C_B=$'\033[38;5;244m'; C_T=$'\033[1;38;5;255m'; C_S=$'\033[1;38;5;80m'; C_K=$'\033[1;38;5;179m'
	C_OK=$'\033[1;38;5;78m'; C_BAD=$'\033[1;38;5;203m'; C_WARN=$'\033[1;38;5;221m'; C_D=$'\033[38;5;245m'; C_0=$'\033[0m'
else
	C_B=""; C_T=""; C_S=""; C_K=""; C_OK=""; C_BAD=""; C_WARN=""; C_D=""; C_0=""
fi
HR="$(printf '─%.0s' $(seq 1 $W))"
vlen() { local s; s="$(printf '%s' "$1" | sed 's/\x1b\[[0-9;]*m//g')"; printf '%s' "${#s}"; }
bl() { local n pad; n="$(vlen "$1")"; pad=$((W - n)); ((pad < 0)) && pad=0; printf '%s│%s%s%*s%s│%s\n' "$C_B" "$C_0" "$1" "$pad" "" "$C_B" "$C_0"; }
bc() { local n l; n="$(vlen "$1")"; l=$(((W - n) / 2)); bl "$(printf '%*s' "$l" '')$1"; }
btop() { printf '%s╭%s╮%s\n' "$C_B" "$HR" "$C_0"; }
bmid() { printf '%s├%s┤%s\n' "$C_B" "$HR" "$C_0"; }
bbot() { printf '%s╰%s╯%s\n' "$C_B" "$HR" "$C_0"; }
bhead() { bl "  ${C_S}$1${C_0}"; }
brule() { bl "  ${C_B}$(printf '─%.0s' $(seq 1 $((W - 4))))${C_0}"; }
cell() { local k="$1" lab="$2" w="${3:-32}" t pad; t="${C_K}[$(printf '%2s' "$k") ]${C_0}  $lab"; pad=$((w - $(vlen "$t"))); ((pad < 0)) && pad=0; printf '%s%*s' "$t" "$pad" ''; }
mrow() { if [[ $# -ge 4 ]]; then bl "   $(cell "$1" "$2")$(cell "$3" "$4" 0)"; else bl "   $(cell "$1" "$2" 0)"; fi; }

# ------------------------------------------------------------------ helpers (same semantics as 3x-ui)
is_ipv4() { [[ "$1" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] && ! [[ "$1" =~ (^|\.)(25[6-9]|2[6-9][0-9]|[3-9][0-9]{2})(\.|$) ]]; }
is_ipv6() { [[ "$1" =~ : ]]; }
is_domain() { [[ "$1" =~ ^([A-Za-z0-9](-*[A-Za-z0-9])*\.)+(xn--[a-z0-9]{2,}|[A-Za-z]{2,})$ ]]; }
is_port() { [[ "$1" =~ ^[0-9]{1,5}$ ]] && (("$1" >= 1 && "$1" <= 65535)); }
gen_random_string() { openssl rand -base64 $(($1 * 2)) | tr -dc 'a-zA-Z0-9' | head -c "$1"; }
# acme.sh standalone binds IPv4; only force v6 when the host has no global IPv4 at all
acme_listen_flag() { if ip -4 addr show scope global 2>/dev/null | grep -q "inet "; then echo ""; else echo "--listen-v6"; fi; }
port_in_use() { ss -ltn 2>/dev/null | awk '{print $4}' | grep -q ":$1\$"; }
# busy by something that is NOT caddy (Caddy is paused automatically while a certificate is issued)
port_busy_other() {
	local line
	line="$(ss -ltnp 2>/dev/null | awk -v p=":$1\$" '$4 ~ p')"
	[[ -n "$line" ]] && ! grep -q '"caddy"' <<<"$line"
}
prompt_or_default() { # var prompt default [envname]
	local __var="$1" __prompt="$2" __default="$3" __env="${4:-$1}"
	if interactive; then read -rp "$__prompt" "$__var" || true; else printf -v "$__var" '%s' "${!__env:-$__default}"; fi
}
panel_cfg() { python3 "$PY" show 2>/dev/null | awk -F': ' -v k="$1" '$1==k{print $2}' || true; }
has_cert() { local c; c="$(panel_cfg certFile | tr -d '[:space:]')"; [[ -n "$c" && "$c" != "-" ]]; }
is_installed() { [[ -f "$PY" && -f /etc/systemd/system/$SERVICE.service ]]; }
is_configured() { is_installed && [[ -n "$(panel_cfg username | tr -d '[:space:]-')" ]]; }
svc_running() { systemctl is-active --quiet "$SERVICE" 2>/dev/null; }

get_server_ip() { # same provider list as 3x-ui
	local u r code ip
	for u in https://api4.ipify.org https://ipv4.icanhazip.com https://v4.api.ipinfo.io/ip https://ipv4.myexternalip.com/raw https://4.ident.me https://check-host.net/ip; do
		r="$(curl -s -w '\n%{http_code}' --max-time 3 "$u" 2>/dev/null || true)"
		code="$(tail -n1 <<<"$r")"; ip="$(head -n-1 <<<"$r" | tr -d '[:space:]"')"
		if [[ "$code" == 200 ]] && is_ipv4 "$ip"; then echo "$ip"; return 0; fi
	done
	return 0
}
ask_ipv4() { # prompts until a valid IPv4 is typed -> stdout
	local v=""
	while [[ -z "$v" ]]; do
		read -rp "Please enter your server's public IPv4 address: " v || true; v="${v// /}"
		if ! is_ipv4 "$v"; then echo -e "${red}Invalid IPv4 address. Please try again.${plain}" >&2; v=""; fi
	done
	echo "$v"
}

install_acme() {
	[[ -x "$ACME" ]] && return 0
	echo -e "${green}Installing acme.sh for SSL certificate management...${plain}"
	(cd ~ && curl -s https://get.acme.sh | sh >/dev/null 2>&1) || { echo -e "${red}Failed to install acme.sh${plain}"; return 1; }
	echo -e "${green}acme.sh installed successfully${plain}"
}

# ------------------------------------------------------------------ port 80 handling
# Let's Encrypt must reach port 80. If a service (Caddy, nginx...) holds it, it is stopped for the request and
# started again afterwards; acme.sh keeps these hooks so every automatic renewal does the same.
P80_STATE=/run/dxwp-port-stopped
HOOK_PRE='/usr/local/bin/dxwp port-free 80'
HOOK_POST='/usr/local/bin/dxwp port-restore'
RELOAD='true' # the panel re-reads a renewed certificate on its own (no restart needed)
port_holders() { ss -H -ltnp "sport = :$1" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u || true; }
pid_unit() { grep -o '[^/]*\.service' "/proc/$1/cgroup" 2>/dev/null | tail -1 || true; }
free_port() { # port: stop the systemd service(s) listening on it, remember them for port-restore
	local port="${1:-80}" pid unit i
	for pid in $(port_holders "$port"); do
		unit="$(pid_unit "$pid")"
		if [[ -z "$unit" || "$unit" == dxwp-panel.service ]]; then
			echo -e "${red}Port ${port} is used by '$(cat "/proc/$pid/comm" 2>/dev/null)' (pid ${pid}), which is not a stoppable systemd service. Stop it manually and retry.${plain}"
			return 1
		fi
		echo -e "${yellow}Port ${port} is open (${unit}); closing it for the certificate request, it will be restored afterwards...${plain}"
		systemctl stop "$unit" 2>/dev/null || true
		grep -qx "$unit" "$P80_STATE" 2>/dev/null || echo "$unit" >>"$P80_STATE"
	done
	for i in 1 2 3 4 5 6 7 8 9 10; do [[ -z "$(port_holders "$port")" ]] && return 0; sleep 1; done
	echo -e "${red}Port ${port} is still busy.${plain}"; return 1
}
restore_ports() {
	[[ -s "$P80_STATE" ]] || return 0
	local u
	while read -r u; do echo -e "${green}Restoring ${u} ...${plain}"; systemctl start "$u" 2>/dev/null || true; done <"$P80_STATE"
	rm -f "$P80_STATE"
}

apply_cert() { # cert key  (3x-ui: x-ui cert -webCert ... -webCertKey ...)
	python3 "$PY" cert -webCert "$1" -webCertKey "$2" >/dev/null
	if [[ "${DXWP_NO_RESTART:-0}" != 1 ]]; then systemctl restart "$SERVICE" 2>/dev/null || true; fi
}

# port used by the standalone HTTP-01 listener (3x-ui prompts + busy-port loop)
choose_acme_port() { # $1 = prompt (CLI only; the web panel always uses port 80)
	prompt_or_default WEBPORT "$1" "80" DXWP_ACME_PORT
	WEBPORT="${WEBPORT// /}"; WEBPORT="${WEBPORT:-80}"
	if ! is_port "$WEBPORT"; then echo -e "${yellow}Your input ${WEBPORT} is invalid, will use default port 80.${plain}"; WEBPORT=80; fi
	echo -e "${green}Will use port: ${WEBPORT} to issue certificates. Please make sure this port is open.${plain}"
	if [[ "$WEBPORT" -ne 80 ]]; then echo -e "${yellow}Reminder: Let's Encrypt still connects on port 80; forward external port 80 to ${WEBPORT}.${plain}"; fi
	if port_in_use "$WEBPORT"; then
		echo -e "${yellow}Port ${WEBPORT} is in use; it will be closed only while the certificate is issued.${plain}"
	else
		echo -e "${green}Port ${WEBPORT} is free and ready for standalone validation.${plain}"
	fi
}

# ------------------------------------------------------------------ certificates
# 3x-ui ssl_cert_issue: Let's Encrypt for a domain (90 days, auto-renews). No e-mail is asked.
ssl_domain() {
	local domain="${1:-${DXWP_SSL_DOMAIN:-}}" extra="${DXWP_SSL_EXTRA:-}" dargs cert_exists=0 acmeCertDir="" certInfo
	install_acme || return 1
	if [[ -z "$domain" ]]; then
		if ! interactive; then echo -e "${red}A valid domain is required (got: '').${plain}"; return 1; fi
		while true; do
			read -rp "Please enter your domain name: " domain || true; domain="${domain// /}"
			if [[ -z "$domain" ]]; then echo -e "${red}Domain name cannot be empty. Please try again.${plain}"; continue; fi
			if ! is_domain "$domain"; then echo -e "${red}Invalid domain format: ${domain}. Please enter a valid domain name.${plain}"; continue; fi
			break
		done
	fi
	is_domain "$domain" || { echo -e "${red}Invalid domain format: ${domain}.${plain}"; return 1; }
	echo -e "${green}Your domain is: ${domain}, checking it...${plain}"
	SSL_ISSUED_DOMAIN="$domain"
	if "$ACME" --list 2>/dev/null | awk '{print $1}' | grep -Fxq "$domain"; then
		if [[ -s "$HOME/.acme.sh/${domain}_ecc/fullchain.cer" && -s "$HOME/.acme.sh/${domain}_ecc/${domain}.key" ]]; then acmeCertDir="$HOME/.acme.sh/${domain}_ecc"
		elif [[ -s "$HOME/.acme.sh/${domain}/fullchain.cer" && -s "$HOME/.acme.sh/${domain}/${domain}.key" ]]; then acmeCertDir="$HOME/.acme.sh/${domain}"; fi
		if [[ -n "$acmeCertDir" ]]; then
			cert_exists=1; certInfo="$("$ACME" --list 2>/dev/null | grep -F "$domain" || true)"
			echo -e "${yellow}Existing certificate found for ${domain}, will reuse it.${plain}"; [[ -n "$certInfo" ]] && echo "$certInfo"
		else
			echo -e "${yellow}Found incomplete acme.sh state for ${domain} (no valid certificate files); cleaning it up and re-issuing.${plain}"
			rm -rf "$HOME/.acme.sh/${domain}" "$HOME/.acme.sh/${domain}_ecc"
		fi
	fi
	[[ $cert_exists -eq 0 ]] && echo -e "${green}Your domain is ready for issuing certificates now...${plain}"
	local dir="$CERT_DIR/$domain"; rm -rf "$dir"; mkdir -p "$dir"
	choose_acme_port "Please choose which port to use (default is 80): " || { rm -rf "$dir"; return 1; }
	dargs=(-d "$domain"); [[ -n "$extra" ]] && is_domain "$extra" && dargs+=(-d "$extra")
	if [[ $cert_exists -eq 0 ]]; then
		"$ACME" --set-default-ca --server letsencrypt --force >/dev/null 2>&1 || true
		free_port "$WEBPORT" || { restore_ports; rm -rf "$dir"; return 1; }
		if ! "$ACME" --issue "${dargs[@]}" $(acme_listen_flag) --standalone --httpport "$WEBPORT" \
			--pre-hook "$HOOK_PRE" --post-hook "$HOOK_POST" --force; then
			restore_ports
			echo -e "${red}Issuing certificate failed, please check logs.${plain}"
			rm -rf "$HOME/.acme.sh/${domain}" "$HOME/.acme.sh/${domain}_ecc" "$dir"
			return 1
		fi
		restore_ports
		echo -e "${green}Issuing certificate succeeded, installing certificates...${plain}"
	fi
	"$ACME" --installcert -d "$domain" --force --key-file "$dir/privkey.pem" --fullchain-file "$dir/fullchain.pem" \
		--reloadcmd "$RELOAD" >/dev/null 2>&1 || true
	if [[ ! -s "$dir/fullchain.pem" || ! -s "$dir/privkey.pem" ]]; then
		echo -e "${red}Installing certificate failed, exiting.${plain}"
		rm -rf "$HOME/.acme.sh/${domain}" "$HOME/.acme.sh/${domain}_ecc" "$dir"; return 1
	fi
	"$ACME" --upgrade --auto-upgrade >/dev/null 2>&1 || true
	chmod 600 "$dir/privkey.pem"; chmod 644 "$dir/fullchain.pem"
	echo -e "${green}Certificate installed successfully, setting the certificate for the panel...${plain}"
	apply_cert "$dir/fullchain.pem" "$dir/privkey.pem"
	echo -e "${green}Domain certificate installed (90 days, auto-renews).${plain}"
}

# 3x-ui setup_ip_certificate: Let's Encrypt IP certificate (shortlived profile, ~6 days)
ssl_ip() {
	local ipv4="${1:-}" ipv6="${2:-}" dir="$CERT_DIR/ip" dargs
	echo -e "${green}Setting up Let's Encrypt IP certificate (shortlived profile)...${plain}"
	echo -e "${yellow}Note: IP certificates are valid for ~6 days and will auto-renew.${plain}"
	echo -e "${yellow}Default listener is port 80. If you choose another port, ensure external port 80 forwards to it.${plain}"
	install_acme || { echo -e "${red}Failed to install acme.sh${plain}"; return 1; }
	if [[ -z "$ipv4" ]]; then echo -e "${red}IPv4 address is required${plain}"; return 1; fi
	is_ipv4 "$ipv4" || { echo -e "${red}Invalid IPv4 address: $ipv4${plain}"; return 1; }
	mkdir -p "$dir"
	dargs=(-d "$ipv4")
	if [[ -n "$ipv6" ]] && is_ipv6 "$ipv6"; then dargs+=(-d "$ipv6"); echo -e "${green}Including IPv6 address: ${ipv6}${plain}"; fi
	choose_acme_port "Port to use for ACME HTTP-01 listener (default 80): " || { rm -rf "$dir"; return 1; }
	echo -e "${green}Issuing IP certificate for ${ipv4}...${plain}"
	"$ACME" --set-default-ca --server letsencrypt --force >/dev/null 2>&1 || true
	free_port "$WEBPORT" || { restore_ports; rm -rf "$dir"; return 1; }
	if ! "$ACME" --issue "${dargs[@]}" --standalone --server letsencrypt --certificate-profile shortlived --days 6 \
		--httpport "$WEBPORT" --pre-hook "$HOOK_PRE" --post-hook "$HOOK_POST" --force; then
		restore_ports
		echo -e "${red}Failed to issue IP certificate${plain}"
		echo -e "${yellow}Please ensure port ${WEBPORT} is reachable (or forwarded from external port 80)${plain}"
		rm -rf "$HOME/.acme.sh/${ipv4}" "$HOME/.acme.sh/${ipv4}_ecc" "$dir" 2>/dev/null || true
		[[ -n "$ipv6" ]] && rm -rf "$HOME/.acme.sh/${ipv6}" "$HOME/.acme.sh/${ipv6}_ecc" 2>/dev/null || true
		return 1
	fi
	restore_ports
	echo -e "${green}Certificate issued successfully, installing...${plain}"
	# acme.sh may exit non-zero on a reload error although the files are installed, so check the files
	"$ACME" --installcert --force -d "$ipv4" --key-file "$dir/privkey.pem" --fullchain-file "$dir/fullchain.pem" \
		--reloadcmd "$RELOAD" 2>&1 || true
	if [[ ! -s "$dir/fullchain.pem" || ! -s "$dir/privkey.pem" ]]; then
		echo -e "${red}Certificate files not found after installation${plain}"
		rm -rf "$HOME/.acme.sh/${ipv4}" "$HOME/.acme.sh/${ipv4}_ecc" "$dir" 2>/dev/null || true; return 1
	fi
	echo -e "${green}Certificate files installed successfully${plain}"
	"$ACME" --upgrade --auto-upgrade >/dev/null 2>&1 || true
	chmod 600 "$dir/privkey.pem"; chmod 644 "$dir/fullchain.pem"
	echo -e "${green}Setting certificate paths for the panel...${plain}"
	apply_cert "$dir/fullchain.pem" "$dir/privkey.pem"
	echo -e "${green}IP certificate installed and configured successfully!${plain}"
	echo -e "${green}Certificate valid for ~6 days, auto-renews via acme.sh cron job.${plain}"
	echo -e "${yellow}acme.sh will automatically renew (Caddy is paused for a few seconds) and the panel reloads the new certificate.${plain}"
}

# 3x-ui option 3: custom certificate files (loops until the files are readable)
ssl_custom() { # [cert key]  non-interactive when both are given
	local c="${1:-}" k="${2:-}"
	if [[ -n "$c" && -n "$k" ]]; then
		[[ -s "$c" && -s "$k" ]] || { err "certificate or key file not found"; return 1; }
		openssl x509 -noout -in "$c" >/dev/null 2>&1 || { err "not a valid certificate: $c"; return 1; }
		apply_cert "$c" "$k"; info "Custom certificate paths applied."; return 0
	fi
	while true; do
		read -rp "Input certificate path (keywords: .crt / fullchain): " c || true
		c="$(echo "$c" | tr -d '"' | tr -d "'")"
		if [[ -f "$c" && -r "$c" && -s "$c" ]]; then break
		elif [[ ! -f "$c" ]]; then echo -e "${red}Error: File does not exist! Try again.${plain}"
		elif [[ ! -r "$c" ]]; then echo -e "${red}Error: File exists but is not readable (check permissions)!${plain}"
		else echo -e "${red}Error: File is empty!${plain}"; fi
	done
	while true; do
		read -rp "Input private key path (keywords: .key / privatekey): " k || true
		k="$(echo "$k" | tr -d '"' | tr -d "'")"
		if [[ -f "$k" && -r "$k" && -s "$k" ]]; then break
		elif [[ ! -f "$k" ]]; then echo -e "${red}Error: File does not exist! Try again.${plain}"
		elif [[ ! -r "$k" ]]; then echo -e "${red}Error: File exists but is not readable (check permissions)!${plain}"
		else echo -e "${red}Error: File is empty!${plain}"; fi
	done
	apply_cert "$c" "$k"
}

# the install-time prompt, text copied from 3x-ui prompt_and_setup_ssl
prompt_and_setup_ssl() { # panel_port web_base_path server_ip
	local panel_port="$1" web_base_path="$2" server_ip="$3" ssl_choice="" cert_domain="" ip_confirm="" ipv6_addr="" custom_domain="" bind_local=""
	SSL_SCHEME="https"
	echo -e "${yellow}Choose SSL certificate setup method:${plain}"
	echo -e "${green}1.${plain} Let's Encrypt for Domain (90-day validity, auto-renews)"
	echo -e "${green}2.${plain} Let's Encrypt for IP Address (6-day validity, auto-renews)"
	echo -e "${green}3.${plain} Custom SSL Certificate (Path to existing files)"
	echo -e "${green}4.${plain} Skip SSL (advanced — behind reverse proxy / SSH tunnel only)"
	echo -e "${blue}Note:${plain} Options 1 & 2 require port 80 open. Option 3 requires manual paths."
	echo -e "${blue}Note:${plain} Option 4 serves the panel over plain HTTP — only safe behind nginx/Caddy or an SSH tunnel."
	if ! interactive; then
		case "${DXWP_SSL_MODE:-none}" in domain) ssl_choice=1 ;; ip) ssl_choice=2 ;; custom) ssl_choice=3 ;; *) ssl_choice=4 ;; esac
	else
		read -rp "Choose an option (default 2 for IP): " ssl_choice || true
		ssl_choice="${ssl_choice// /}"
		if [[ "$ssl_choice" != 1 && "$ssl_choice" != 3 && "$ssl_choice" != 4 ]]; then ssl_choice=2; fi
	fi
	case "$ssl_choice" in
	1)
		echo -e "${green}Using Let's Encrypt for domain certificate...${plain}"
		if ssl_domain; then
			cert_domain="$SSL_ISSUED_DOMAIN"
			SSL_HOST="${cert_domain:-$server_ip}"
			echo -e "${green}✓ SSL certificate configured successfully with domain: ${cert_domain}${plain}"
		else
			echo -e "${red}SSL certificate setup failed for domain mode.${plain}"
			SSL_HOST="$server_ip"
		fi
		;;
	2)
		echo -e "${green}Using Let's Encrypt for IP certificate (shortlived profile)...${plain}"
		if interactive; then
			read -rp "Is ${server_ip} the correct incoming public IPv4 address for this server? [Default y]: " ip_confirm || true
			if [[ -n "$ip_confirm" && "$ip_confirm" != y && "$ip_confirm" != Y ]]; then server_ip="$(ask_ipv4)"; fi
		fi
		prompt_or_default ipv6_addr "Do you have an IPv6 address to include? (leave empty to skip): " "" DXWP_SSL_IPV6
		ipv6_addr="${ipv6_addr// /}"
		if ssl_ip "$server_ip" "$ipv6_addr"; then
			SSL_HOST="$server_ip"
			echo -e "${green}✓ Let's Encrypt IP certificate configured successfully${plain}"
		else
			echo -e "${red}✗ IP certificate setup failed. Please check port 80 is open.${plain}"
			SSL_HOST="$server_ip"
		fi
		;;
	3)
		echo -e "${green}Using custom existing certificate...${plain}"
		if interactive; then
			read -rp "Please enter domain name certificate issued for: " custom_domain || true; custom_domain="${custom_domain// /}"
			ssl_custom
		else
			custom_domain="${DXWP_SSL_DOMAIN:-}"; ssl_custom "${DXWP_SSL_CERT:-}" "${DXWP_SSL_KEY:-}" || true
		fi
		SSL_HOST="${custom_domain:-$server_ip}"
		echo -e "${green}✓ Custom certificate paths applied.${plain}"
		echo -e "${yellow}Note: You are responsible for renewing these files externally.${plain}"
		systemctl restart "$SERVICE" >/dev/null 2>&1 || true
		;;
	4)
		echo ""
		echo -e "${red}⚠ Panel will be installed WITHOUT SSL/TLS.${plain}"
		echo -e "${yellow}Login credentials and cookies will travel as plain HTTP.${plain}"
		echo -e "${yellow}Only safe when:${plain}"
		echo -e "${yellow}  • A reverse proxy (nginx, Caddy, Traefik) terminates TLS for you, or${plain}"
		echo -e "${yellow}  • You access the panel exclusively via SSH tunnel${plain}"
		echo ""
		SSL_SCHEME="http"; SSL_HOST="$server_ip"
		if interactive; then read -rp "Bind the panel to 127.0.0.1 only? (recommended — forces SSH tunnel / reverse-proxy access) [y/N]: " bind_local || true; else bind_local=n; fi
		if [[ "$bind_local" == y || "$bind_local" == Y ]]; then
			python3 "$PY" setting -listenIP 127.0.0.1 >/dev/null 2>&1
			SSL_HOST="127.0.0.1"
			echo -e "${green}✓ Panel bound to 127.0.0.1 only. It is now unreachable from the public internet.${plain}"
			echo ""
			echo -e "${green}SSH Port Forwarding — open the panel from your local machine via:${plain}"
			echo -e "  Standard SSH command:"
			echo -e "  ${yellow}ssh -L 2222:127.0.0.1:${panel_port} root@${server_ip}${plain}"
			echo -e "  If using an SSH key:"
			echo -e "  ${yellow}ssh -i <sshkeypath> -L 2222:127.0.0.1:${panel_port} root@${server_ip}${plain}"
			echo -e "  Then open in your browser:"
			echo -e "  ${yellow}http://localhost:2222/${web_base_path}${plain}"
			echo ""
			echo -e "${yellow}Alternative: point a reverse proxy (nginx/Caddy) at 127.0.0.1:${panel_port} and let it terminate TLS.${plain}"
		else
			echo -e "${yellow}Panel will listen on all interfaces over plain HTTP. Make sure something else is terminating TLS in front of it.${plain}"
		fi
		systemctl restart "$SERVICE" >/dev/null 2>&1 || true
		echo -e "${green}✓ SSL setup skipped.${plain}"
		;;
	esac
	# the URL must match what the panel really serves: https only when a certificate is configured
	if has_cert; then SSL_SCHEME=https; else SSL_SCHEME=http; fi
}

# ------------------------------------------------------------------ SSL management menu (x-ui.sh ssl_cert_issue_main)
ssl_list() { install_acme >/dev/null 2>&1 || true; [[ -x "$ACME" ]] && "$ACME" --list || echo "acme.sh is not installed"; ls -1 "$CERT_DIR" 2>/dev/null | sed 's/^/  files: /' || true; }
ssl_pick() { # asks for a name from $CERT_DIR (or takes $1)
	local n="${1:-}"
	if [[ -z "$n" ]]; then
		echo "Existing domains:"; ls -1 "$CERT_DIR" 2>/dev/null || true
		read -rp "Please enter a domain from the list: " n || true
	fi
	[[ -n "$n" && "$n" != *"/"* && "$n" != *".."* && -d "$CERT_DIR/$n" ]] && printf '%s' "$n"
}
ssl_revoke() {
	local n; n="$(ssl_pick "${1:-}")" || { err "No such certificate."; return 1; }
	[[ -x "$ACME" ]] || { err "acme.sh is not installed"; return 1; }
	local ids="$n"
	if [[ "$n" == ip && -s "$CERT_DIR/ip/fullchain.pem" ]]; then
		ids="$(openssl x509 -noout -ext subjectAltName -in "$CERT_DIR/ip/fullchain.pem" 2>/dev/null | grep -oE 'IP Address:[0-9a-fA-F:.]+' | cut -d: -f2- || true)"
	fi
	local i; for i in $ids; do "$ACME" --revoke -d "$i" >/dev/null 2>&1 || true; "$ACME" --remove -d "$i" >/dev/null 2>&1 || true; rm -rf "$HOME/.acme.sh/$i" "$HOME/.acme.sh/${i}_ecc"; done
	if [[ "$(panel_cfg certFile)" == "$CERT_DIR/$n/"* ]]; then
		python3 "$PY" cert -remove >/dev/null; systemctl restart "$SERVICE" 2>/dev/null || true
		warn "That certificate was used by the panel; it now runs on plain HTTP."
	fi
	rm -rf "$CERT_DIR/$n"
	info "Certificate $n revoked and removed."
}
ssl_renew() {
	local n; n="$(ssl_pick "${1:-}")" || { err "No such certificate."; return 1; }
	[[ -x "$ACME" ]] || { err "acme.sh is not installed"; return 1; }
	free_port "$WEBPORT" || { restore_ports; return 1; }
	"$ACME" --renew -d "$n" --force --ecc || "$ACME" --renew -d "$n" --force || { restore_ports; err "renew failed"; return 1; }
	restore_ports
	"$ACME" --installcert -d "$n" --force --key-file "$CERT_DIR/$n/privkey.pem" --fullchain-file "$CERT_DIR/$n/fullchain.pem" --reloadcmd "$RELOAD" >/dev/null 2>&1 || true
	info "Renewed. The panel picks up the new certificate automatically."
}
ssl_setpaths() {
	local n; n="$(ssl_pick "${1:-}")" || { err "No such certificate."; return 1; }
	apply_cert "$CERT_DIR/$n/fullchain.pem" "$CERT_DIR/$n/privkey.pem"; info "Certificate paths set for the panel."
}
ssl_manage() {
	local choice ip ipv6 d
	while true; do
		echo -e "${green}\t1.${plain} Get SSL (Domain)"
		echo -e "${green}\t2.${plain} Revoke & Remove"
		echo -e "${green}\t3.${plain} Force Renew"
		echo -e "${green}\t4.${plain} Show Existing Domains"
		echo -e "${green}\t5.${plain} Set Cert paths for the panel"
		echo -e "${green}\t6.${plain} Get SSL for IP Address (6-day cert, auto-renews)"
		echo -e "${green}\t7.${plain} Custom certificate files"
		echo -e "${green}\t0.${plain} Back to Main Menu"
		read -rp "Choose an option: " choice || return 0
		case "$choice" in
		1) ssl_domain || true ;;
		2) ssl_revoke || true ;;
		3) ssl_renew || true ;;
		4) ssl_list || true ;;
		5) ssl_setpaths || true ;;
		6) d="$(get_server_ip)"; [[ -n "$d" ]] || d="$(ask_ipv4)"
			read -rp "Is ${d} the correct incoming public IPv4 address for this server? [Default y]: " ip || true
			if [[ -n "$ip" && "$ip" != y && "$ip" != Y ]]; then d="$(ask_ipv4)"; fi
			read -rp "Do you have an IPv6 address to include? (leave empty to skip): " ipv6 || true
			ssl_ip "$d" "${ipv6// /}" || true ;;
		7) ssl_custom || true ;;
		0) return 0 ;;
		*) echo -e "${red}Invalid option.${plain}" ;;
		esac
		echo
	done
}

# ------------------------------------------------------------------ settings
panel_path() { panel_cfg basePath | sed 's#^/##; s#/$##'; }
panel_url() {
	local port base scheme host cf listen
	port="$(panel_cfg port)"; base="$(panel_path)"; cf="$(panel_cfg certFile)"; listen="$(panel_cfg listen)"
	if has_cert; then scheme=https; else scheme=http; fi
	host="${DXWP_HOST:-}"
	if [[ -z "$host" && "$cf" =~ ^$CERT_DIR/([^/]+)/ && "${BASH_REMATCH[1]}" != ip ]]; then host="${BASH_REMATCH[1]}"; fi
	if [[ -z "$host" && "$listen" == 127.0.0.1 ]]; then host=127.0.0.1; fi
	[[ -n "$host" ]] || host="$(get_server_ip)"; [[ -n "$host" ]] || host="<server-ip>"
	printf '%s://%s:%s/%s' "$scheme" "$host" "$port" "$base"
}
show_info() { # x-ui settings
	echo -e "${green}Username    :${plain} $(panel_cfg username)"
	echo -e "${green}Password    :${plain} (stored hashed; reset with the menu option or: dxwp setting -password NEW)"
	echo -e "${green}Port        :${plain} $(panel_cfg port)"
	echo -e "${green}WebBasePath :${plain} $(panel_path)"
	echo -e "${green}Listen      :${plain} $(panel_cfg listen)"
	if has_cert; then echo -e "${green}SSL         :${plain} enabled ($(panel_cfg certFile))"; else echo -e "${yellow}SSL         : off (plain HTTP)${plain}"; fi
	echo -e "${green}Access URL  :${plain} $(panel_url)"
	if [[ "$(panel_cfg sub)" == on ]]; then echo -e "${green}Sub service :${plain} on  (${plain}$(panel_cfg subBase)<secret>)"; else echo -e "${yellow}Sub service : off${plain}  (enable it in the panel: Settings > Sub settings)"; fi
}
restart_svc() { systemctl restart "$SERVICE"; sleep 1; if svc_running; then info "Panel is running."; else err "Panel failed to start: journalctl -u $SERVICE -n 30"; fi; }
reset_credentials() {
	local u p
	read -rp "Please set the login username [default is a random username]: " u || true; u="${u// /}"; [[ -n "$u" ]] || u="$(gen_random_string 10)"
	read -rp "Please set the login password [default is a random password]: " p || true; p="${p// /}"; [[ -n "$p" ]] || p="$(gen_random_string 10)"
	python3 "$PY" setting -username "$u" -password "$p" >/dev/null; restart_svc
	echo -e "${green}Username:${plain} $u\n${green}Password:${plain} $p"
}
reset_basepath() {
	local b; read -rp "Please set the web base path [leave empty for a random path]: " b || true; b="${b//\//}"; b="${b// /}"; [[ -n "$b" ]] || b="$(gen_random_string 18)"
	python3 "$PY" setting -webBasePath "$b" >/dev/null; restart_svc; echo -e "${green}New WebBasePath:${plain} $b"
	echo -e "${green}Access URL:${plain} $(panel_url)"
}
change_port() {
	local p; read -rp "Enter the port number [1-65535]: " p || true; p="${p// /}"
	is_port "$p" || { err "invalid port"; return 1; }
	if port_in_use "$p" && [[ "$p" != "$(panel_cfg port)" ]]; then err "port $p is already in use"; return 1; fi
	python3 "$PY" setting -port "$p" >/dev/null; restart_svc; info "Port is now $p"; echo -e "${green}Access URL:${plain} $(panel_url)"
}
reset_all() {
	local c; read -rp "Are you sure you want to reset all panel settings (username, password, port, base path, certificate)? [y/n]: " c || true
	[[ "$c" == y || "$c" == Y ]] || return 0
	python3 "$PY" cert -remove >/dev/null; python3 "$PY" setting -reset; python3 "$PY" setting -listenIP 0.0.0.0 >/dev/null; restart_svc
}
api_token() { # show, or reset with "reset"
	if [[ "${1:-}" == reset ]]; then
		python3 "$PY" setting -resetApiToken | awk -F': ' '$1=="apiToken"{print "API Token: "$2}'; systemctl restart "$SERVICE" 2>/dev/null || true
	else python3 "$PY" setting -getApiToken | awk -F': ' '$1=="apiToken"{print "API Token: "$2}'; fi
}
instances_cmd() { need_root; local ins="$PANEL_DIR/install-tproxy.sh"; [[ -f "$ins" ]] || ins="$SELF_DIR/install-tproxy.sh"; bash "$ins" "$@"; }

# ------------------------------------------------------------------ doctor: why does a proxy not connect?
doctor() {
	need_root
	TPDIR_="${DXWP_TPDIR:-/etc/tproxy-server}" USERS_="${DXWP_USERS:-$CONF_DIR/users.json}" PUBIP_="$(get_server_ip)" PYPANEL_="$PY" \
		C_OK_="$C_OK" C_BAD_="$C_BAD" C_WARN_="$C_WARN" C_0_="$C_0" python3 - <<'DOCPY'
import json, os, re, socket, subprocess, urllib.request
E = os.environ
G, R, Y, Z = E["C_OK_"], E["C_BAD_"], E["C_WARN_"], E["C_0_"]
bad = 0
def sh(*a):
    try:
        r = subprocess.run(a, capture_output=True, text=True, timeout=15)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:
        return 1, str(e)
import importlib.util
sp_ = importlib.util.spec_from_file_location("dxp", E["PYPANEL_"])
P = importlib.util.module_from_spec(sp_)
sp_.loader.exec_module(P)
notes = 0
def ok(m):
    print("  %s✔%s %s" % (G, Z, m))
def note(m, hint=""):
    global notes
    notes += 1
    print("  %s!%s %s" % (Y, Z, m))
    if hint:
        print("      → " + hint)
def no(m, hint=""):
    global bad
    bad += 1
    print("  %s✘%s %s" % (R, Z, m))
    if hint:
        print("      → " + hint)
def active(u):
    return sh("systemctl", "is-active", u)[1].split("\n")[0] == "active"
def listening(p):
    return bool(re.search(r":%d\b" % p, sh("ss", "-Hltn")[1]))
print("\n%sGateway%s" % (Y, Z))
if active("caddy.service"): ok("caddy is running")
else: no("caddy is NOT running", "systemctl start caddy   (a stopped Caddy kills every proxy; a certificate request that was interrupted is the usual reason)")
if listening(443): ok("port 443 is listening")
else: no("nothing listens on port 443", "ss -ltnp | grep :443")
try: inst = json.load(open(E["TPDIR_"] + "/instances.json")).get("instances", [])
except Exception: inst = []
if not inst: no("no inbound is registered", "create one in the panel (Dashboard)")
try: users = json.load(open(E["USERS_"])).get("users", [])
except Exception: users = []
ip = E["PUBIP_"]
for i in inst:
    nm, host = i["name"], i.get("hostname", "")
    print("\n%sInbound %s%s  (%s)" % (Y, nm, Z, host))
    for u in ("tproxy@%s.service" % nm, "mtproxy@%s.service" % nm):
        if active(u): ok(u + " active")
        else: no(u + " is not active", "journalctl -u %s -n 30 --no-pager" % u)
    try:
        m = urllib.request.urlopen("http://127.0.0.1:%s/metrics" % i.get("admin"), timeout=3).read().decode()
        ok("relay answers (live sessions: %s)" % (re.search(r"tproxy_sessions_live (\S+)", m) or [0, "?"])[1])
    except Exception:
        no("relay metrics unreachable on 127.0.0.1:%s" % i.get("admin"), "journalctl -u tproxy@%s -n 30 --no-pager" % nm)
    mode = i.get("mode", "direct")
    r = P.cfcheck({"hostname": host})
    st, ips = r["state"], ", ".join(r["ips"])
    has_ip = bool(ip) and ip in r["ips"]
    if st == "unknown":
        no("%s does not resolve" % host, "create an A record for it (Cloudflare: DNS only first, proxy after the install)")
    elif mode == "cf":
        if st == "proxied": ok("%s is behind Cloudflare (proxy on): %s" % (host, ips))
        elif st == "mixed": note("%s: only some records are proxied by Cloudflare (%s)" % (host, ips), "turn the proxy on for every record of this name")
        elif has_ip or not ip: note("%s points straight to this server (%s), the Cloudflare proxy is still off" % (host, ips), "mode is cloudflare: turn the orange cloud (Proxied) on for this record")
        else: no("%s resolves to %s but this server is %s" % (host, ips, ip), "fix the A record, or the domain is not yours any more")
    else:
        if st == "direct" and (has_ip or not ip): ok("%s resolves to this server (%s)" % (host, ips))
        elif st == "direct": no("%s resolves to %s but this server is %s" % (host, ips, ip), "DNS must point to this server")
        else: note("%s is behind Cloudflare (%s) but this inbound was installed as direct" % (host, ips), "reinstall it in Cloudflare mode, or turn the proxy off (DNS only)")
    try: prof = json.load(open("%s/%s/profiles.json" % (E["TPDIR_"], nm))).get("profiles", [])
    except Exception:
        prof = []
        no("cannot read profiles.json of %s" % nm)
    names = {p.get("name") for p in prof}
    for u in users:
        if nm not in u.get("inst", []): continue
        pr = (u.get("ports") or {}).get(nm)
        tag = "user %s" % u.get("name")
        if "dxu" + u["id"] not in names:
            no("%s: profile missing in profiles.json" % tag, "edit & save the user in the panel, or: systemctl restart tproxy@%s" % nm); continue
        if not pr:
            no("%s: no backend port" % tag); continue
        if not u.get("enabled", True):
            ok("%s: disabled (stopped on purpose)" % tag); continue
        if active("mtproxy@dxu-%d.service" % pr[0]): ok("%s: backend mtproxy@dxu-%d active, profile loaded" % (tag, pr[0]))
        else: no("%s: backend mtproxy@dxu-%d is NOT active" % (tag, pr[0]), "journalctl -u mtproxy@dxu-%d -n 30 --no-pager ; ls -l /etc/mtproxy/dxu-%d.env" % (pr[0], pr[0]))
print("\n%sTraffic counters%s" % (Y, Z))
if sh("nft", "list", "table", "inet", "dxwp_acct")[0] == 0: ok("nft table dxwp_acct loaded")
else: no("nft table dxwp_acct missing (usage will not count; proxies still work)", "systemctl restart dxwp-panel")
extra = " (%d note%s)" % (notes, "" if notes == 1 else "s") if notes else ""
print("\n%s%s%s" % (R if bad else G, ("%d problem(s) found" % bad if bad else "all checks passed") + extra, Z))
DOCPY
}

# ------------------------------------------------------------------ install / update
write_install_result() { # user pass port path scheme host token
	install -d -m 0700 "$CONF_DIR"
	( umask 077; {
		printf 'DXWP_USERNAME=%q\nDXWP_PASSWORD=%q\nDXWP_PORT=%q\nDXWP_WEB_BASE_PATH=%q\nDXWP_SCHEME=%q\nDXWP_HOST=%q\nDXWP_API_TOKEN=%q\nDXWP_DB_TYPE=json\n' "$1" "$2" "$3" "$4" "$5" "$6" "$7"
		printf 'DXWP_ACCESS_URL=%q\n' "$5://$6:$3/$4"
	} >"$CONF_DIR/install-result.env" )
	chmod 600 "$CONF_DIR/install-result.env"
}
usage_line() { bl "  ${C_K}$(printf '%-18s' "$1")${C_0}${C_B}│${C_0} $2"; }
show_usage() {
	echo
	btop; bc "${C_T}DX WebProxy Control${C_0}"; bmid; bl ""
	usage_line "dxwp" "Open Management Console"
	usage_line "dxwp start" "Start Panel"
	usage_line "dxwp stop" "Stop Panel"
	usage_line "dxwp restart" "Restart Panel"
	usage_line "dxwp status" "Show Current Status"
	usage_line "dxwp settings" "Show Current Settings"
	usage_line "dxwp enable" "Enable Autostart"
	usage_line "dxwp disable" "Disable Autostart"
	usage_line "dxwp log" "View Logs"
	usage_line "dxwp token" "Show / Reset API Token"
	usage_line "dxwp users" "List Panel Users"
	usage_line "dxwp doctor" "Check why proxies fail"
	usage_line "dxwp ssl" "SSL Certificate Management"
	usage_line "dxwp update" "Update Panel"
	usage_line "dxwp uninstall" "Uninstall Panel"
	bl ""; bbot
}
# the project root inside a downloaded archive: the repo root, or a folder inside it (e.g. when a whole folder was uploaded)
locate_src() {
	local hit; hit="$(find "$1" -maxdepth 4 -type f -path '*/panel/dxwp_panel.py' 2>/dev/null | head -1)"
	if [[ -n "$hit" ]]; then dirname "$(dirname "$hit")"; else printf '%s' "$1"; fi
}
install_files() {
	info "Installing dependencies ..."
	export DEBIAN_FRONTEND=noninteractive
	apt-get update -qq >/dev/null 2>&1 || true
	apt-get install -y -qq --no-install-recommends python3 curl openssl socat cron iproute2 ca-certificates >/dev/null
	local src="${DXWP_SRC:-$SELF_DIR}" tmp="" f missing=()
	if [[ -z "$src" || ! -f "$src/panel/dxwp_panel.py" ]]; then
		tmp="$(mktemp -d)"; info "Downloading panel files ..."
		curl -fsSL --proto '=https' --tlsv1.2 "$TARBALL" | tar -xz -C "$tmp" --strip-components=1 || { err "download failed"; exit 1; }
		src="$(locate_src "$tmp")"
	fi
	for f in panel/dxwp_panel.py panel/web/index.html panel/web/login.html panel.sh install-tproxy.sh; do [[ -f "$src/$f" ]] || missing+=("$f"); done
	if ((${#missing[@]})); then
		err "These files are missing from the downloaded project: ${missing[*]}"
		echo "What the download contains:"; (cd "$src" && find . -maxdepth 3 -type f | head -25 | sed 's/^/   /')
		err "Upload them to the repository (keep the folder layout: panel/dxwp_panel.py, panel/web/index.html, panel/web/login.html, panel.sh, install-tproxy.sh at the top level) and run the update again."
		exit 1
	fi
	install -d -m 0755 "$PANEL_DIR"; install -d -m 0700 "$CONF_DIR"
	install -m 0755 "$src/panel/dxwp_panel.py" "$PY"
	rm -rf "$PANEL_DIR/web"; cp -r "$src/panel/web" "$PANEL_DIR/web"; chmod -R a+rX "$PANEL_DIR/web"
	install -m 0755 "$src/install-tproxy.sh" "$PANEL_DIR/install-tproxy.sh"
	install -m 0755 "$src/panel.sh" "$PANEL_DIR/panel.sh"
	ln -sf "$PANEL_DIR/panel.sh" /usr/local/bin/dxwp
	[[ -z "$tmp" ]] || rm -rf "$tmp"
	cat >/etc/systemd/system/$SERVICE.service <<UNIT
[Unit]
Description=DX WebProxy Panel
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=DXWP_SYSTEMD=1
Environment=HOME=/root
WorkingDirectory=$PANEL_DIR
ExecStart=/usr/bin/python3 $PY run
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
	install -d -m 0755 /etc/systemd/system/tproxy-firewall.service.d
	cat >/etc/systemd/system/tproxy-firewall.service.d/dxwp.conf <<DROPIN
[Service]
ExecStart=-/usr/sbin/nft -f $CONF_DIR/acct.nft
ExecReload=-/usr/sbin/nft -f $CONF_DIR/acct.nft
ExecStop=-/usr/sbin/nft delete table inet dxwp_acct
DROPIN
	systemctl daemon-reload
}
install_panel() {
	need_root
	install_files
	if is_configured; then # update: keep credentials, port, path and certificate
		systemctl enable "$SERVICE" >/dev/null 2>&1; restart_svc
		info "Panel updated. Your settings were kept."; show_info; echo; show_usage; return 0
	fi
	local server_ip="" user pass path port confirm token
	server_ip="$(get_server_ip)"
	if [[ -z "$server_ip" ]]; then
		if [[ "$NONINT" == 1 ]]; then server_ip="${DXWP_SERVER_IP:-}"
		else echo -e "${yellow}Could not auto-detect server IP from any provider.${plain}"; server_ip="$(ask_ipv4)"; fi
	fi
	user="${DXWP_USERNAME:-$(gen_random_string 10)}"
	pass="${DXWP_PASSWORD:-$(gen_random_string 10)}"
	path="${DXWP_WEB_BASE_PATH:-$(gen_random_string 18)}"
	if ! interactive; then
		if [[ -n "${DXWP_PANEL_PORT:-}" ]]; then port="$DXWP_PANEL_PORT"; echo -e "${yellow}Your Panel Port is: ${port}${plain}"
		else port="$(shuf -i 1024-62000 -n 1)"; echo -e "${yellow}Generated random port: ${port}${plain}"; fi
	else
		read -rp "Would you like to customize the Panel Port settings? (If not, a random port will be applied) [y/n]: " confirm || true
		if [[ "$confirm" == y || "$confirm" == Y ]]; then
			while true; do
				read -rp "Please set up the panel port: " port || true; port="${port// /}"
				if ! is_port "$port"; then echo -e "${red}Invalid port. Enter a number between 1 and 65535.${plain}"; continue; fi
				if port_in_use "$port"; then echo -e "${red}Port ${port} is already in use. Choose another one.${plain}"; continue; fi
				break
			done
			echo -e "${yellow}Your Panel Port is: ${port}${plain}"
		else
			port="$(shuf -i 1024-62000 -n 1)"; while port_in_use "$port"; do port="$(shuf -i 1024-62000 -n 1)"; done
			echo -e "${yellow}Generated random port: ${port}${plain}"
		fi
	fi
	is_port "$port" || { err "invalid panel port: $port"; exit 1; }
	python3 "$PY" setting -username "$user" -password "$pass" -port "$port" -webBasePath "$path" >/dev/null
	python3 "$PY" setting -getApiToken >/dev/null
	systemctl enable --now "$SERVICE" >/dev/null 2>&1
	echo ""
	echo -e "${green}═══════════════════════════════════════════${plain}"
	echo -e "${green}     SSL Certificate Setup (RECOMMENDED)   ${plain}"
	echo -e "${green}═══════════════════════════════════════════${plain}"
	echo -e "${yellow}SSL is strongly recommended. Skip only if a reverse proxy${plain}"
	echo -e "${yellow}or SSH tunnel handles TLS for you.${plain}"
	echo -e "${yellow}Let's Encrypt now supports both domains and IP addresses!${plain}"
	echo ""
	prompt_and_setup_ssl "$port" "$path" "$server_ip"
	systemctl restart "$SERVICE" >/dev/null 2>&1 || true
	token="$(python3 "$PY" setting -getApiToken | awk -F': ' '$1=="apiToken"{print $2}')"
	echo ""
	echo -e "${green}═══════════════════════════════════════════${plain}"
	echo -e "${green}     Panel Installation Complete!         ${plain}"
	echo -e "${green}═══════════════════════════════════════════${plain}"
	echo -e "${green}Username:    ${user}${plain}"
	echo -e "${green}Password:    ${pass}${plain}"
	echo -e "${green}Port:        ${port}${plain}"
	echo -e "${green}WebBasePath: ${path}${plain}"
	echo -e "${green}Database:    JSON (${CONF_DIR}/config.json)${plain}"
	echo -e "${green}Access URL:  ${SSL_SCHEME}://${SSL_HOST}:${port}/${path}${plain}"
	echo -e "${green}API Token:   ${token}${plain}"
	echo -e "${green}═══════════════════════════════════════════${plain}"
	echo -e "${yellow}⚠ IMPORTANT: Save these credentials securely!${plain}"
	if [[ "$SSL_SCHEME" == https ]]; then echo -e "${yellow}⚠ SSL Certificate: Enabled and configured${plain}"
	else echo -e "${yellow}⚠ SSL Certificate: Not configured — panel is HTTP-only. Use a reverse proxy or SSH tunnel, or run: dxwp ssl${plain}"; fi
	write_install_result "$user" "$pass" "$port" "$path" "$SSL_SCHEME" "$SSL_HOST" "$token"
	echo -e "${yellow}Install result written to ${CONF_DIR}/install-result.env (mode 600).${plain}"
	echo -e "${green}dxwp installation finished, it is running now...${plain}"
	echo ""
	show_usage
}
uninstall_panel() {
	need_root
	local c; read -rp "Are you sure you want to uninstall the panel? [y/n]: " c || true
	[[ "$c" == y || "$c" == Y ]] || return 0
	systemctl disable --now "$SERVICE" 2>/dev/null || true
	rm -f /etc/systemd/system/$SERVICE.service /usr/local/bin/dxwp
	rm -f /etc/systemd/system/tproxy-firewall.service.d/dxwp.conf
	rm -rf "$PANEL_DIR" "$CONF_DIR"; systemctl daemon-reload
	info "Panel removed (proxy instances and certificates were left untouched)."
}

# ------------------------------------------------------------------ menu (x-ui.sh show_menu style)
menu_url() { # from the file both the menu and the web panel keep in sync (no network wait)
	local u=""
	[[ -r "$CONF_DIR/install-result.env" ]] && u="$( (set +u; . "$CONF_DIR/install-result.env"; printf '%s' "${DXWP_ACCESS_URL:-}") 2>/dev/null)"
	[[ -n "$u" ]] || u="$(panel_url 2>/dev/null)"
	printf '%s' "$u"
}
need_inst() { is_installed || { err "Please install the panel first."; return 1; }; }
draw_menu() {
	local st au url
	if ! is_installed; then st="${C_WARN}NOT INSTALLED${C_0}"; au="${C_D}-${C_0}"; url="-"
	else
		if svc_running; then st="${C_OK}RUNNING${C_0}"; else st="${C_BAD}STOPPED${C_0}"; fi
		if systemctl is-enabled --quiet "$SERVICE" 2>/dev/null; then au="${C_OK}ENABLED${C_0}"; else au="${C_BAD}DISABLED${C_0}"; fi
		url="$(menu_url)"
	fi
	((${#url} > W - 4)) && url="${url:0:$((W - 5))}…"
	echo
	btop; bc "${C_T}DX WebProxy Panel${C_0}"; bc "${C_D}Management Console${C_0}"; bmid; bl ""
	bhead "Status"; brule
	bl "  ${C_OK}●${C_0} ${C_D}Panel State${C_0}        : $st"
	bl "  ${C_OK}●${C_0} ${C_D}Auto Start${C_0}         : $au"
	bl ""
	bhead "Access URL"; brule
	bl "  ${C_T}${url}${C_0}"
	bl ""; bmid
	bhead "MANAGEMENT"; bl ""
	mrow 1 Install 2 Update; mrow 3 Uninstall 4 "Reset Login"; mrow 5 "Reset Base Path" 6 "Reset Settings"
	mrow 7 "Change Port" 8 "Current Settings"; mrow 9 "API Token"
	bl ""; bmid
	bhead "SERVICE"; bl ""
	mrow 10 Start 11 Stop; mrow 12 Restart 13 "Check Status"; mrow 14 "Logs Management"
	bl ""; bmid
	bhead "SYSTEM"; bl ""
	mrow 15 "Enable Autostart" 16 "Disable Autostart"
	bl ""; bmid
	bhead "NETWORK & SECURITY"; bl ""
	mrow 17 "SSL Certificate" 18 "Proxy Instances"; mrow 19 "Verify Proxy Instances" 20 "Panel Users"
	bl ""; bmid; bl ""
	mrow 0 Exit h Commands
	bl ""; bbot
	echo
}
menu() {
	local num c
	while true; do
		draw_menu
		read -rp "  Select an option [0-20]: " num || exit 0
		echo
		case "$num" in
		0) exit 0 ;;
		h | H) show_usage ;;
		1) if is_installed; then warn "Panel is already installed (use Update)."; else install_panel; fi ;;
		2) need_inst && install_panel ;;
		3) need_inst && uninstall_panel ;;
		4) need_inst && reset_credentials ;;
		5) need_inst && reset_basepath ;;
		6) need_inst && reset_all ;;
		7) need_inst && change_port ;;
		8) need_inst && show_info ;;
		9) need_inst && { api_token; read -rp "Reset the API token? [y/N]: " c || true; [[ "$c" == y || "$c" == Y ]] && api_token reset || true; } ;;
		10) need_inst && { systemctl start "$SERVICE" && info "Panel started."; } ;;
		11) need_inst && { systemctl stop "$SERVICE" && warn "Panel stopped."; } ;;
		12) need_inst && restart_svc ;;
		13) need_inst && { systemctl status "$SERVICE" --no-pager || true; } ;;
		14) need_inst && { journalctl -u "$SERVICE" -n 100 --no-pager || true; } ;;
		15) need_inst && { systemctl enable "$SERVICE" >/dev/null 2>&1 && info "Autostart enabled."; } ;;
		16) need_inst && { systemctl disable "$SERVICE" >/dev/null 2>&1 && warn "Autostart disabled."; } ;;
		17) need_inst && ssl_manage ;;
		18) instances_cmd --list || true ;;
		19) instances_cmd --verify || true ;;
		20) need_inst && { python3 "$PY" users || true; } ;;
		*) echo -e "${red}Please enter a correct number [0-20]${plain}" ;;
		esac
		echo; read -rp "  Press ENTER to return..." _ || exit 0
	done
}

main() {
	case "${1:-menu}" in
	auto) need_root; if is_configured; then menu; else install_panel; fi ;;
	install) install_panel ;;
	update) need_root; need_inst && install_panel ;;
	menu) need_root; menu ;;
	ssl | ssl-manage) need_root; ssl_manage ;;
	ssl-domain) need_root; ssl_domain "${2:-}" ;;
	ssl-ip) need_root; ssl_ip "${2:-}" "${3:-}" ;;
	ssl-custom) need_root; ssl_custom "${2:-}" "${3:-}" ;;
	ssl-revoke) need_root; ssl_revoke "${2:-}" ;;
	ssl-renew) need_root; ssl_renew "${2:-}" ;;
	ssl-list) ssl_list ;;
	port-free) need_root; free_port "${2:-80}" ;;
	port-restore) need_root; restore_ports ;;
	users) python3 "$PY" users ;;
	doctor | check) doctor ;;
	help | -h | --help) show_usage ;;
	setting) shift; need_root; python3 "$PY" setting "$@"; restart_svc ;;
	settings | show | info) show_info ;;
	token) need_root; api_token "${2:-}" ;;
	start) need_root; systemctl start "$SERVICE" ;;
	stop) need_root; systemctl stop "$SERVICE" ;;
	restart) need_root; restart_svc ;;
	status) systemctl status "$SERVICE" --no-pager || true ;;
	enable) need_root; systemctl enable "$SERVICE" ;;
	disable) need_root; systemctl disable "$SERVICE" ;;
	log | logs) journalctl -u "$SERVICE" -n 100 --no-pager ;;
	uninstall) uninstall_panel ;;
	*) show_usage; exit 1 ;;
	esac
}
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi
