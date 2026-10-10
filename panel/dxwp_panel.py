#!/usr/bin/env python3
"""DX WebProxy Panel - stdlib-only management panel for telegram-webproxy-installer.

Server:  dxwp_panel.py run
CLI:     dxwp_panel.py setting|cert|show|init   (same idea as `x-ui setting` / `x-ui cert`)
"""
import argparse, base64, hashlib, hmac, json, os, re, secrets, shutil, ssl, subprocess, sys, threading, time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from urllib.request import urlopen, Request

VERSION = "2.1.5"
HOME = os.environ.get("DXWP_HOME", os.path.dirname(os.path.abspath(__file__)))
CONF = os.environ.get("DXWP_CONF", "/etc/dxwp-panel/config.json")
WEB = os.path.join(HOME, "web")
INSTALLER = os.environ.get("DXWP_INSTALLER", os.path.join(HOME, "install-tproxy.sh"))
PANELSH = os.environ.get("DXWP_PANELSH", os.path.join(HOME, "panel.sh"))
TPDIR = os.environ.get("DXWP_TPDIR", "/etc/tproxy-server")
REG = os.environ.get("DXWP_REG", TPDIR + "/instances.json")
CADDY_DIR = os.environ.get("DXWP_CADDY_DIR", "/etc/caddy")
MTPROXY_DIR = os.environ.get("DXWP_MTPROXY_DIR", "/etc/mtproxy")

DEF = {"port": 0, "basePath": "/", "username": "", "passHash": "", "certFile": "", "keyFile": "", "listen": "0.0.0.0", "apiToken": "",
       "subEnable": False, "subListen": "", "subDomain": "", "subPort": 2069, "subPath": "/sub/", "subURI": ""}
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
HOST_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$", re.I)
IPV4_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
CARRIERS = ("https", "https-lanes", "websocket", "websocket-lanes")
# direct mode talks plain HTTPS to this server; cf mode is behind the Cloudflare proxy, which needs WebSocket
MODE_CARRIERS = {"direct": ("https", "https-lanes"), "cf": ("websocket", "websocket-lanes")}
DRY = os.environ.get("DXWP_DRY") == "1"   # tests only: no systemctl / nft / ss
NFT_NAME = "nft"


# ---------------------------------------------------------------- config
def load():
    c = dict(DEF)
    try:
        with open(CONF) as f:
            c.update(json.load(f))
    except Exception:
        pass
    return c


def save(c):
    os.makedirs(os.path.dirname(CONF), mode=0o700, exist_ok=True)
    tmp = CONF + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(c, f, indent=2)
    os.replace(tmp, CONF)


def norm_base(p):
    p = "/" + (p or "").strip().strip("/")
    if p == "/":
        return "/"
    if not re.match(r"^(/[A-Za-z0-9._~-]{1,64}){1,4}$", p):
        raise ValueError("invalid base path")
    return p + "/"


def hpw(pw, salt=None, it=200000):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), it).hex()
    return "pbkdf2$%d$%s$%s" % (it, salt, h)


def vpw(pw, stored):
    try:
        _, it, salt, h = stored.split("$")
        return hmac.compare_digest(hpw(pw, salt, int(it)).split("$")[3], h)
    except Exception:
        return False


def rnd(n, alpha="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"):
    return "".join(secrets.choice(alpha) for _ in range(n))


def free_port():
    import socket
    for _ in range(50):
        p = 10000 + secrets.randbelow(52000)
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return 20443


def init_cfg(c):
    """First run: random credentials like x-ui. Returns (cfg, printed_password|None)."""
    pw = None
    if not c["username"] or not c["passHash"]:
        c["username"] = rnd(8)
        pw = rnd(12)
        c["passHash"] = hpw(pw)
    if not c["port"]:
        c["port"] = free_port()
    if pw and c["basePath"] in ("", "/"):
        c["basePath"] = "/" + rnd(16) + "/"
    if pw:                       # first install: the sub service is on by default (port 2069)
        c["subEnable"] = True
    save(c)
    return c, pw


# ---------------------------------------------------------------- helpers
def sh(argv, timeout=60):
    if DRY and os.path.basename(argv[0]) in ("systemctl", "nft", "ss", "tproxy-server"):
        if os.path.basename(argv[0]) == "systemctl" and argv[1] in ("restart", "enable", "disable", "start", "stop"):
            time.sleep(float(os.environ.get("DXWP_FAKE_DELAY", "0")))
        f = os.environ.get("DXWP_FAKE_" + os.path.basename(argv[0]).upper().replace("-", "_"))
        if f and os.path.exists(f) and (os.path.basename(argv[0]) != NFT_NAME or "list" in argv):
            return 0, open(f).read()
        return 0, ""
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr)
    except Exception as e:
        return 1, str(e)


def port_free(p, addr):
    import socket
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((addr, p))
            return True
        except OSError:
            return False


def hb(n):
    n = float(n)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return ("%d %s" % (n, u)) if u == "B" else ("%.1f %s" % (n, u))
        n /= 1024
    return "%.1f PB" % n


_JC = {}


def jcached(path):
    """json file -> data, re-read only when its mtime/size changes (the UI polls every few seconds)."""
    try:
        st = os.stat(path)
        k = (st.st_mtime_ns, st.st_size)
        hit = _JC.get(path)
        if hit and hit[0] == k:
            return hit[1]
        with open(path) as f:
            d = json.load(f)
        _JC[path] = (k, d)
        return d
    except Exception:
        _JC.pop(path, None)
        return None


def reg_load():
    d = jcached(REG)
    return list(d.get("instances", [])) if isinstance(d, dict) else []


def reg_rm(name):
    try:
        with open(REG) as f:
            d = json.load(f)
        d["instances"] = [i for i in d.get("instances", []) if i.get("name") != name]
        with open(REG, "w") as f:
            json.dump(d, f, indent=2)
    except Exception:
        pass


def profile(name):
    try:
        return jcached("%s/%s/profiles.json" % (TPDIR, name))["profiles"][0]
    except Exception:
        return {}


def inst_carriers(nm, mode=None):
    """(primary, second) carrier of a domain: both carriers of its mode, the installed one first."""
    p = profile(nm).get("carrier_mode", "https")
    if mode is None:
        mode = (find_inst(nm) or {}).get("mode")
    pair = MODE_CARRIERS.get(mode) or next((v for v in MODE_CARRIERS.values() if p in v), ())
    if p not in pair:
        return p, None
    return p, next(x for x in pair if x != p)


ALT = "@2"           # ports key suffix of a user's second-carrier backend


def alt_secret(sec):
    return hashlib.sha256((sec + ":alt").encode()).hexdigest()[:32]


def key_secret(u, key):
    return alt_secret(u["secret"]) if key.endswith(ALT) else u["secret"]


def base_names(u):
    return list(dict.fromkeys(k[:-len(ALT)] if k.endswith(ALT) else k for k in u.get("ports", {})))


def ensure_alt(u):
    """give the user a second backend on every domain that offers a second carrier (alloc_ports needs u already listed)."""
    ch = False
    for nm in u.get("inst", []):
        if nm in u.get("ports", {}) and nm + ALT not in u["ports"] and inst_carriers(nm)[1]:
            u["ports"][nm + ALT] = alloc_ports()
            ch = True
    return ch


def instances():
    out = []
    for i in reg_load():
        a, b = inst_carriers(i["name"], i.get("mode"))
        out.append({"id": i["name"], "hostname": i.get("hostname"), "mode": i.get("mode"),
                    "carrier": a, "carriers": [x for x in (a, b) if x], "st": "ok"})
    return out


def find_inst(name):
    if not NAME_RE.match(name or ""):
        return None
    for i in reg_load():
        if i.get("name") == name:
            return i
    return None


def verify():
    res = {}
    for i in reg_load():
        nm = i["name"]
        act = sh(["systemctl", "is-active", "tproxy@%s.service" % nm, "mtproxy@%s.service" % nm], 10)[1].split()
        e = {"st": "ok" if act and all(a == "active" for a in act) else "w"}
        try:
            met = urlopen("http://127.0.0.1:%s/metrics" % i.get("admin"), timeout=3).read().decode()
            m = {}
            for ln in met.splitlines():
                p = ln.split()
                if len(p) == 2:
                    m[p[0]] = p[1]
            e["live"] = m.get("tproxy_sessions_live", "0")
            e["up"] = hb(m.get("tproxy_bytes_up_total", 0))
            e["down"] = hb(m.get("tproxy_bytes_down_total", 0))
            e["upb"] = int(float(m.get("tproxy_bytes_up_total", 0)))
            e["downb"] = int(float(m.get("tproxy_bytes_down_total", 0)))
        except Exception:
            e["st"] = "w"
        res[nm] = e
    return res


def remove_instance(nm):
    """drop it from the registry at once (so every list is right immediately); stopping services and cleanup run in the background."""
    reg_rm(nm)
    LIVE["inst"].pop(nm, None)

    def bg():
        sh(["systemctl", "disable", "--now", "tproxy@%s.service" % nm, "mtproxy@%s.service" % nm])
        _remove_files(nm)
        sh(["systemctl", "reload", "caddy.service"])
    threading.Thread(target=bg, daemon=True).start()


def _remove_files(nm):
    shutil.rmtree("%s/%s" % (TPDIR, nm), ignore_errors=True)
    for f in ("%s/%s.env" % (MTPROXY_DIR, nm), "%s/%s-origin.pem" % (CADDY_DIR, nm),
              "%s/%s-origin.key" % (CADDY_DIR, nm), "%s/sites/%s.caddy" % (CADDY_DIR, nm)):
        try:
            os.remove(f)
        except OSError:
            pass


def cert_info(f):
    if not f or not os.path.isfile(f):
        return None
    rc, out = sh(["openssl", "x509", "-noout", "-subject", "-enddate", "-in", f], 8)
    if rc:
        return None
    d = {}
    for ln in out.splitlines():
        if "=" in ln:
            k, v = ln.split("=", 1)
            d[k.strip()] = v.strip()
    return {"subject": d.get("subject", ""), "notAfter": d.get("notAfter", "")}


# ---------------------------------------------------------------- pre-install checks (DNS / Cloudflare proxy / server IP)
import ipaddress, socket
CF_NETS = [ipaddress.ip_network(n) for n in (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22", "141.101.64.0/18",
    "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22", "198.41.128.0/17",
    "162.158.0.0/15", "104.16.0.0/13", "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32", "2405:8100::/32",
    "2a06:98c0::/29", "2c0f:f248::/32")]


def _is_cf(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(a.version == n.version and a in n for n in CF_NETS)


def _resolve(host, fam, timeout=6.0):
    """-> (list_of_ips, status) ; status: ok | none | fail. getaddrinfo has no timeout, so run it in a thread."""
    box = {}

    def run():
        try:
            box["r"] = sorted({x[4][0] for x in socket.getaddrinfo(host, None, fam, socket.SOCK_STREAM)})
        except socket.gaierror as e:
            box["e"] = e
        except Exception as e:
            box["e"] = e
    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(timeout)
    if "r" in box:
        return box["r"], "ok"
    e = box.get("e")
    if isinstance(e, socket.gaierror) and e.errno in (socket.EAI_NONAME, getattr(socket, "EAI_NODATA", -5), getattr(socket, "EAI_ADDRFAMILY", -9)):
        return [], "none"
    return [], "fail"


PFC = {}


def preflight(c):
    """Checks that must hold BEFORE the installer runs (Let's Encrypt needs the domain to reach this server directly).
    Returns {"ok": bool, "errors": [{code,...}], "warnings": [...], "ip": server ip, "resolved": [...]}. Pure data; the UI localizes."""
    host = str(c.get("hostname", "")).strip().lower()
    if not HOST_RE.match(host):
        raise ValueError("invalid domain")
    err, warn = [], []
    if not PUBIP[0]:
        pubip_refresh()
    srv = PUBIP[0]
    hit = PFC.get(host)
    if hit and time.time() - hit[0] < 20:
        return hit[1]
    box = {}
    t6 = threading.Thread(target=lambda: box.update(v6=_resolve(host, socket.AF_INET6)), daemon=True)
    t6.start()
    a4, st4 = _resolve(host, socket.AF_INET)
    t6.join(8)
    a6, st6 = box.get("v6", ([], "fail"))
    if st4 == "fail" and st6 == "fail":
        warn.append({"code": "dns_unavailable"})
    elif not a4 and not a6:
        err.append({"code": "nxdomain"})
    if a4 or a6:
        cf = [x for x in a4 + a6 if _is_cf(x)]
        if cf:
            err.append({"code": "cf_proxied", "ips": cf})
        else:
            if a4:
                if not srv:
                    warn.append({"code": "srv_unknown", "ips": a4})
                elif srv not in a4:
                    err.append({"code": "wrong_ip", "ips": a4, "srv": srv})
                elif len(a4) > 1:
                    warn.append({"code": "extra_ips", "ips": [x for x in a4 if x != srv]})
            elif a6:
                warn.append({"code": "only_aaaa", "ips": a6})
            if a4 and a6:
                warn.append({"code": "aaaa", "ips": a6})
    res = {"ok": not err, "errors": err, "warnings": warn, "ip": srv, "resolved": a4 + a6}
    if res["ok"]:
        PFC[host] = (time.time(), res)
    return res


def cfcheck(c):
    """Post-install: is the domain now behind the Cloudflare proxy? state: proxied | direct | mixed | unknown"""
    host = str(c.get("hostname", "")).strip().lower()
    if not HOST_RE.match(host):
        raise ValueError("invalid domain")
    a4, _ = _resolve(host, socket.AF_INET)
    a6, _ = _resolve(host, socket.AF_INET6)
    ips = a4 + a6
    cf = [x for x in ips if _is_cf(x)]
    if not ips:
        st = "unknown"
    elif len(cf) == len(ips):
        st = "proxied"
    elif cf:
        st = "mixed"
    else:
        st = "direct"
    return {"state": st, "ips": ips}


# ---------------------------------------------------------------- build installer args (whitelisted, no shell)
def install_args(c):
    host = str(c.get("hostname", "")).strip().lower()
    if not HOST_RE.match(host):
        raise ValueError("invalid domain")
    mode = c.get("mode", "direct")
    carrier = c.get("carrier") or MODE_CARRIERS.get(mode, ("https",))[0]
    if mode not in ("direct", "cf") or carrier not in CARRIERS:
        raise ValueError("invalid mode/carrier")
    if carrier not in MODE_CARRIERS[mode]:
        raise ValueError("carrier %s is not allowed in %s mode (use %s)" % (carrier, mode, " or ".join(MODE_CARRIERS[mode])))
    a = ["--hostname", host, "--mode", mode, "--carrier", carrier]
    nm = str(c.get("name", "")).strip()
    if nm:
        if not NAME_RE.match(nm):
            raise ValueError("invalid name")
        a += ["--name", nm]
    sec = str(c.get("secret", "")).strip()
    if sec:
        if not re.match(r"^(dd)?[0-9a-fA-F]{32}$", sec):
            raise ValueError("invalid secret")
        a += ["--secret", sec.lower()]
    site, sp = c.get("site", "none"), str(c.get("sitePath", "")).strip()
    if site == "dir":
        if not re.match(r"^/[A-Za-z0-9._/-]{1,200}$", sp):
            raise ValueError("invalid site dir")
        a += ["--site-dir", sp]
    elif site == "up":
        if not re.match(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d{1,5})?(/[^\s'\"\\]*)?$", sp):
            raise ValueError("invalid upstream (loopback only)")
        a += ["--site-upstream", sp]
    for key, flag in (("workers", "--mtproxy-workers"), ("maxc", "--mtproxy-max-connections")):
        v = str(c.get(key, "")).strip()
        if v:
            if not re.match(r"^\d{1,7}$", v):
                raise ValueError("invalid number")
            a += [flag, v]
    cv = str(c.get("caddyv", "")).strip()
    if cv:
        if not re.match(r"^\d+\.\d+\.\d+$", cv):
            raise ValueError("invalid caddy version")
        a += ["--caddy-version", cv]
    fl = c.get("flags") or {}
    a.append("--no-dns-check")  # the panel already ran its own DNS check; the installer would wait for a keypress in cf mode
    for key, flag in (("keepLog", "--keep-log"),):
        if fl.get(key):
            a.append(flag)
    return a


# ---------------------------------------------------------------- background jobs (one at a time)
JOBS, JLOCK, CUR = {}, threading.Lock(), [None]


# ---------------------------------------------------------------- update check
REPO_URL = os.environ.get("DXWP_REPO", "https://github.com/MNSH-Nexo/telegram-webproxy-installer")
UPD_URL = os.environ.get("DXWP_UPDATE_URL", REPO_URL + "/releases/latest")
UPD = {"t": 0.0, "latest": "", "tag": "", "ok": False}
ULOCK = threading.Lock()


def _vt(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:4])


def cur_version():
    """Installed version: the release tag recorded by panel.sh at install/update time (RELEASE file), or VERSION, whichever is newer."""
    try:
        with open(os.path.join(HOME, "RELEASE")) as f:
            rel = ".".join(map(str, _vt(f.read().strip())))
    except Exception:
        rel = ""
    return rel if rel and _vt(rel) > _vt(VERSION) else VERSION


def update_info(force=False):
    """Newest GitHub release (cached, never raises): {cur, latest, tag, available, checked}.
    /releases/latest redirects to /releases/tag/<tag>, so no API token or rate limit is involved."""
    with ULOCK:
        ttl = 600 if UPD["ok"] else 90
        if force or time.time() - UPD["t"] > ttl:
            UPD["t"] = time.time()
            try:
                with urlopen(Request(UPD_URL, headers={"User-Agent": "dxwp-panel"}), timeout=8) as r:
                    m = re.search(r"/tag/([^/?#\s]+)$", r.geturl())
                tag = m.group(1) if m else ""
                UPD["tag"], UPD["latest"], UPD["ok"] = (tag, ".".join(map(str, _vt(tag))), True) if _vt(tag) else ("", "", False)
            except Exception:
                UPD["tag"], UPD["latest"], UPD["ok"] = "", "", False
        lat = UPD["latest"]
        cv = cur_version()
        return {"cur": cv, "latest": lat, "tag": UPD["tag"], "checked": UPD["ok"],
                "available": bool(lat) and _vt(lat) > _vt(cv)}


def start_update(tag=""):
    """Runs `panel.sh update` outside the panel's own cgroup so the restart at its end does not kill it.
    The release tag is written to RELEASE first (restored if the update fails), so the update check is right
    even when the installed panel.sh is an older one that does not record it."""
    if DRY:
        return True
    tag = tag if re.match(r"^[A-Za-z0-9._-]+$", tag or "") else ""
    script = ('old=$(cat "$3" 2>/dev/null); [ -n "$2" ] && printf "%s\\n" "$2" >"$3"; '
              'if ! DXWP_NONINTERACTIVE=1 TERM=dumb bash "$1" update >>/var/log/dxwp-update.log 2>&1; then '
              'if [ -n "$old" ]; then printf "%s\\n" "$old" >"$3"; else rm -f "$3"; fi; fi')
    tb = ["DXWP_TARBALL=%s/archive/refs/tags/%s.tar.gz" % (REPO_URL, tag)] if tag else []
    cmd = ["bash", "-c", script, "dxwp-update", PANELSH, tag, os.path.join(HOME, "RELEASE")]
    run = lambda argv: subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if shutil.which("systemd-run") and run(["systemd-run", "--quiet", "--no-block", "--collect", "--unit=dxwp-update-%d" % int(time.time())]
                                           + ["--setenv=" + x for x in tb] + cmd):
        return True
    return run(["setsid", "-f", "env"] + tb + cmd)


class Busy(Exception):
    pass


def start_job(title, argv, after=None, extra_env=None):
    with JLOCK:
        if CUR[0] and JOBS[CUR[0]]["state"] == "run":
            raise Busy()
        jid = secrets.token_hex(6)
        job = JOBS[jid] = {"state": "run", "log": "", "rc": None, "title": title}
        CUR[0] = jid
        for k in list(JOBS)[:-20]:
            JOBS.pop(k, None)
    env = dict(os.environ, TERM="dumb", DXWP_NONINTERACTIVE="1", **(extra_env or {}))
    env.setdefault("HOME", "/root")

    def run():
        try:
            p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, errors="replace", env=env)
            for line in p.stdout:
                job["log"] = (job["log"] + ANSI.sub("", line.replace("\r", "\n")))[-30000:]
            job["rc"] = p.wait()
        except Exception as e:
            job["log"] += str(e)
            job["rc"] = 1
        job["state"] = "done"
        if job["rc"] == 0 and after:
            try:
                after()
            except Exception:
                pass

    threading.Thread(target=run, daemon=True).start()
    return jid


def restart_panel(delay=2.0):
    def go():
        time.sleep(delay)
        if os.environ.get("DXWP_SYSTEMD") == "1":
            subprocess.Popen(["systemctl", "restart", "dxwp-panel"])
        else:
            os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Thread(target=go, daemon=True).start()


# ---------------------------------------------------------------- users, live traffic, system info
# Model: an "inbound" is an instance (domain + carrier). A user has its own secret and can be attached to
# several inbounds. For every (user, inbound) the panel
#   * adds a profile (own secret) to that instance's profiles.json  -> relay (tproxy-server) accepts it,
#   * starts a dedicated MTProxy backend on its own loopback port   -> the only way to meter/limit one user,
#   * counts bytes of that port with nftables counters              -> live per-user traffic.
UF = os.environ.get("DXWP_USERS", os.path.join(os.path.dirname(CONF), "users.json"))
ACCT = os.environ.get("DXWP_ACCT", os.path.join(os.path.dirname(CONF), "acct.nft"))
RES = os.environ.get("DXWP_RESULT", os.path.join(os.path.dirname(CONF), "install-result.env"))
NFT = shutil.which("nft") or "/usr/sbin/nft"
RELAY_BIN = "/usr/local/bin/tproxy-server"
ULOCK = threading.RLock()
UD = {"users": [], "last": {}}
ACTIVE = {}
LIVE = {"sys": {}, "speed": {"up": 0.0, "down": 0.0}, "inst": {}, "users": {}}
PUBIP = [""]
DIRTY = [False]
CTR_RE = re.compile(r"counter c(\d+)([ud]) \{\s*packets (\d+) bytes (\d+)")


def users_load():
    try:
        with open(UF) as f:
            d = json.load(f)
        UD["users"] = d.get("users", [])
        UD["last"] = d.get("last", {})
    except Exception:
        pass


def users_save():
    with ULOCK:
        os.makedirs(os.path.dirname(UF), mode=0o700, exist_ok=True)
        tmp = UF + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(UD, f)
        os.replace(tmp, UF)
        DIRTY[0] = False


def used_ports():
    s = set()
    for u in UD["users"]:
        for pr in u.get("ports", {}).values():
            s.update(pr)
    for i in reg_load():
        for k in ("backend", "admin", "mtproxy", "mtproxy_admin"):
            try:
                s.add(int(i.get(k)))
            except (TypeError, ValueError):
                pass
    return s


def alloc_ports():
    used = used_ports()
    a = next(p for p in range(21000, 30000) if p not in used and port_free(p, "127.0.0.1"))
    used.add(a)
    b = next(p for p in range(31000, 40000) if p not in used and port_free(p, "127.0.0.1"))
    return [a, b]


def unit_of(port):
    return "mtproxy@dxu-%d.service" % port


def env_of(port):
    return "%s/dxu-%d.env" % (MTPROXY_DIR, port)


def write_env(u, port, adm, key=""):
    import grp
    os.makedirs(MTPROXY_DIR, exist_ok=True)
    path = env_of(port)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    with os.fdopen(fd, "w") as f:
        f.write("MTPROXY_SECRET=%s\nMTPROXY_PORT=%d\nMTPROXY_ADMIN=%d\nMTPROXY_WORKERS=1\nMTPROXY_MAX_CONNECTIONS=4096\n"
                % (key_secret(u, key), port, adm))
    try:
        os.chown(tmp, 0, grp.getgrnam("mtproxy").gr_gid)
    except (KeyError, PermissionError, OSError):
        pass
    os.replace(tmp, path)


def drop_unit(port):
    sh(["systemctl", "disable", "--now", unit_of(port)], 30)
    try:
        os.remove(env_of(port))
    except OSError:
        pass
    ACTIVE.pop(port, None)


def should_run(u):
    now = time.time() * 1000
    lim, ex = u.get("limit") or 0, u.get("expiry") or 0
    ended = bool((lim and u.get("used", 0) >= lim) or (ex and ex < now))
    return bool(u.get("enabled", True)) and not ended


def reconcile(force=False):
    """start/stop per-user backends. Slow systemctl calls run OUTSIDE the lock, so the API never waits for them."""
    todo = []
    with ULOCK:
        want = {}
        for u in UD["users"]:
            run = should_run(u)
            for inst, (a, b) in u.get("ports", {}).items():
                want[a] = (run, u, b, inst)
        for p in list(ACTIVE):
            if p not in want:
                ACTIVE.pop(p, None)
        now = time.time()
        for p, (run, u, b, key) in want.items():
            st = ACTIVE.get(p)
            if force or st is None or st[0] is not run or (not st[2] and now - st[1] > 30):
                if run and not os.path.exists(env_of(p)):
                    write_env(u, p, b, key)
                todo.append((p, run))
    for p, run in todo:
        rc, _ = sh(["systemctl", "enable" if run else "disable", "--now", unit_of(p)], 40)
        with ULOCK:
            ACTIVE[p] = (run, time.time(), rc == 0 or not run)


def write_acct(apply=True):
    """nftables table: counters per user port + drop external access to those backend ports."""
    with ULOCK:
        pr = [tuple(p) for u in UD["users"] for p in u.get("ports", {}).values()]
        L = ["table inet dxwp_acct", "delete table inet dxwp_acct", "table inet dxwp_acct {"]
        for a, _ in pr:
            L += ["\tcounter c%du {}" % a, "\tcounter c%dd {}" % a]
        L += ["\tchain acct {", "\t\ttype filter hook input priority -20; policy accept;"]
        if pr:
            L.append('\t\tiifname != "lo" tcp dport { %s } drop' % ", ".join(str(x) for x in sorted({x for p in pr for x in p})))
        for a, _ in pr:
            L.append('\t\tiifname "lo" tcp dport %d meta length > 60 counter name "c%du"' % (a, a))
            L.append('\t\tiifname "lo" tcp sport %d meta length > 60 counter name "c%dd"' % (a, a))
        L += ["\t}", "}", ""]
        os.makedirs(os.path.dirname(ACCT), mode=0o700, exist_ok=True)
        tmp = ACCT + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(L))
        os.replace(tmp, ACCT)
        if apply:
            apply_acct()


def apply_acct():
    with ULOCK:
        poll(1.0)            # bank what the old counters hold before they are reset
        sh([NFT, "-f", ACCT], 15)
        UD["last"] = {}
        users_save()


# Heavy steps (nft load, unit start/stop, relay restart) run here, in the background, so the
# panel answers "save user" immediately instead of waiting for systemd.
AQ = {"lock": threading.Lock(), "ev": threading.Event(), "nft": False, "restart": set(), "units": set(), "drop": []}


def queue_apply(nft=False, restart=(), units=(), drop=()):
    with AQ["lock"]:
        AQ["nft"] = AQ["nft"] or nft
        AQ["restart"].update(restart)
        AQ["units"].update(units)
        AQ["drop"].extend(drop)
    AQ["ev"].set()


def apply_worker():
    while True:
        AQ["ev"].wait()
        AQ["ev"].clear()
        time.sleep(0.3)                     # let a burst of clicks collapse into one pass
        with AQ["lock"]:
            nft, rs, un, dr = AQ["nft"], set(AQ["restart"]), set(AQ["units"]), list(AQ["drop"])
            AQ["nft"] = False; AQ["restart"].clear(); AQ["units"].clear(); AQ["drop"].clear()
        try:
            for port in dr:
                drop_unit(port)
            if nft:
                apply_acct()
            reconcile()
            for port in un:
                sh(["systemctl", "restart", unit_of(port)], 40)
            for nm in rs:
                sh(["systemctl", "restart", "tproxy@%s.service" % nm], 60)
        except Exception:
            pass


def read_ctr():
    rc, out = sh([NFT, "list", "counters", "table", "inet", "dxwp_acct"], 5)
    if rc != 0:
        return None
    r = {}
    for m in CTR_RE.finditer(out):
        r.setdefault(int(m.group(1)), [0, 0])[0 if m.group(2) == "u" else 1] = int(m.group(4))
    return r


def read_conns():
    rc, out = sh(["ss", "-Htn", "state", "established"], 5)
    cnt = {}
    for ln in out.splitlines():
        p = ln.split()
        if len(p) >= 4 and p[2].startswith("127.0.0.1:"):
            try:
                k = int(p[2].rsplit(":", 1)[1])
                cnt[k] = cnt.get(k, 0) + 1
            except ValueError:
                pass
    return cnt


def poll(dt):
    ctr, conns = read_ctr(), read_conns()
    with ULOCK:
        last = UD["last"]
        for u in UD["users"]:
            up = dn = live = 0
            for inst, (a, b) in u.get("ports", {}).items():
                if ctr is not None and a in ctr:
                    cu, cd = ctr[a]
                    lu, ld = last.get(str(a), [None, None])
                    up += cu - lu if lu is not None and cu >= lu else cu
                    dn += cd - ld if ld is not None and cd >= ld else cd
                    last[str(a)] = [cu, cd]
                live += conns.get(a, 0)
            if live or up or dn:
                u["last"] = int(time.time() * 1000)
                DIRTY[0] = True
            if up or dn:
                u["up"] = u.get("up", 0) + up
                u["down"] = u.get("down", 0) + dn
                u["used"] = u.get("used", 0) + up + dn
                DIRTY[0] = True
            LIVE["users"][u["id"]] = {"live": live, "su": up / dt, "sd": dn / dt}


def read_sys(prev):
    with open("/proc/stat") as f:
        v = [int(x) for x in f.readline().split()[1:9]]
    idle, tot = v[3] + v[4], sum(v)
    pct = 0.0
    if prev and tot > prev[1]:
        pct = max(0.0, min(100.0, 100.0 * (1 - (idle - prev[0]) / (tot - prev[1]))))
    mi = {}
    with open("/proc/meminfo") as f:
        for ln in f:
            k, _, x = ln.partition(":")
            mi[k] = int(x.split()[0]) * 1024
    mhz = 0
    try:
        with open("/proc/cpuinfo") as f:
            for ln in f:
                if ln.startswith("cpu MHz"):
                    mhz = float(ln.split(":")[1]); break
    except Exception:
        pass
    sv = os.statvfs("/")
    try:
        up = float(open("/proc/uptime").read().split()[0])
    except Exception:
        up = 0
    d = {"cpu": {"pct": pct, "cores": os.cpu_count() or 1, "mhz": mhz},
         "ram": {"used": mi["MemTotal"] - mi.get("MemAvailable", mi.get("MemFree", 0)), "total": mi["MemTotal"]},
         "swap": {"used": mi.get("SwapTotal", 0) - mi.get("SwapFree", 0), "total": mi.get("SwapTotal", 0)},
         "disk": {"used": (sv.f_blocks - sv.f_bfree) * sv.f_frsize, "total": sv.f_blocks * sv.f_frsize},
         "load": list(os.getloadavg()), "uptime": up}
    return d, (idle, tot)


def _metrics(i):
    e = {"st": "w"}
    try:
        met = urlopen("http://127.0.0.1:%s/metrics" % i.get("admin"), timeout=1.0).read().decode()
        m = {}
        for ln in met.splitlines():
            p = ln.split()
            if len(p) == 2:
                m[p[0]] = p[1]
        e.update(live=m.get("tproxy_sessions_live", "0"), upb=int(float(m.get("tproxy_bytes_up_total", 0))),
                 downb=int(float(m.get("tproxy_bytes_down_total", 0))))
        e["up"], e["down"] = hb(e["upb"]), hb(e["downb"])
        e["st"] = "ok"
    except Exception:
        pass
    return e


def inst_snapshot(units):
    regs = reg_load()
    out = {}
    if regs:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(8, len(regs))) as ex:
            res = list(ex.map(_metrics, regs))
        out = {i["name"]: e for i, e in zip(regs, res)}
    if units and regs:
        argv = ["systemctl", "is-active"]
        for i in regs:
            argv += ["tproxy@%s.service" % i["name"], "mtproxy@%s.service" % i["name"]]
        act = sh(argv, 10)[1].split()
        if len(act) == 2 * len(regs):
            for k, i in enumerate(regs):
                if out[i["name"]]["st"] == "ok" and not (act[2 * k] == "active" and act[2 * k + 1] == "active"):
                    out[i["name"]]["st"] = "w"
    for nm, e in out.items():
        if not units and e["st"] == "ok" and LIVE["inst"].get(nm, {}).get("st") == "w":
            e["st"] = "w"
    return out


HN = 150                                            # history length (1 sample / second)
HIST = {k: [] for k in ("cpu", "ram", "swap", "disk", "load", "up", "dn")}


def hist_push(k, v):
    a = HIST[k]
    a.append(round(float(v), 3))
    if len(a) > HN:
        del a[0]


def syssampler():
    """CPU / RAM / disk / load once a second in its own thread (never delayed by nft / systemctl calls) + a rolling history,
    so the Status page can draw full charts the moment it opens."""
    prev = None
    while True:
        t1 = time.time()
        try:
            d, prev = read_sys(prev)
            LIVE["sys"] = d
            pc = lambda o: o["used"] / o["total"] * 100 if o.get("total") else 0.0
            hist_push("cpu", d["cpu"]["pct"])
            hist_push("ram", pc(d["ram"]))
            hist_push("swap", pc(d["swap"]))
            hist_push("disk", pc(d["disk"]))
            hist_push("load", d["load"][0])
            sp = LIVE.get("speed") or {}
            hist_push("up", sp.get("up", 0))
            hist_push("dn", sp.get("down", 0))
        except Exception:
            pass
        time.sleep(max(0.05, 1.0 - (time.time() - t1)))


def sampler():
    prev_cpu, t0, ti, tick, pi = None, time.time(), time.time(), 0, {}
    while True:
        t1 = time.time()
        dt = max(0.2, t1 - t0)
        t0 = t1
        try:
            poll(dt)
            if True:
                snap = inst_snapshot(tick % 10 == 0)
                d2 = max(0.2, t1 - ti)
                ti = t1
                su = sd = 0
                for nm, e in snap.items():
                    if nm in pi and "upb" in e and "upb" in pi[nm]:
                        su += e["upb"] - pi[nm]["upb"] if e["upb"] >= pi[nm]["upb"] else e["upb"]
                        sd += e["downb"] - pi[nm]["downb"] if e["downb"] >= pi[nm]["downb"] else e["downb"]
                pi = snap
                LIVE["inst"] = snap
                LIVE["speed"] = {"up": su / d2, "down": sd / d2}
            reconcile()
            if DIRTY[0] and tick % 2 == 0:
                users_save()
            if not PUBIP[0] and tick % 60 == 0:
                threading.Thread(target=pubip_refresh, daemon=True).start()
        except Exception:
            pass
        tick += 1
        time.sleep(max(0.05, 1.0 - (time.time() - t1)))


def pubip_refresh():
    for u in ("https://api4.ipify.org", "https://ipv4.icanhazip.com", "https://v4.api.ipinfo.io/ip", "https://4.ident.me"):
        try:
            ip = urlopen(u, timeout=3).read().decode().strip()
            if IPV4_RE.match(ip):
                PUBIP[0] = ip
                return
        except Exception:
            pass


def write_profiles(path, d):
    import grp
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(d, f)
    try:
        os.chown(tmp, 0, grp.getgrnam("tproxy").gr_gid)
    except (KeyError, PermissionError, OSError):
        pass
    os.chmod(tmp, 0o400)
    os.replace(tmp, path)


def sync_profiles(nm, restart=True):
    """profiles.json of instance nm = its own profile(s) + one profile per attached user."""
    path = "%s/%s/profiles.json" % (TPDIR, nm)
    try:
        with open(path) as f:
            d = json.load(f)
    except Exception:
        return False
    base = [p for p in d.get("profiles", []) if not str(p.get("name", "")).startswith("dxu")]
    car = base[0].get("carrier_mode", "https") if base else "https"
    car2 = inst_carriers(nm)[1]
    new = list(base)
    for u in UD["users"]:
        if nm in u.get("ports", {}):
            new.append({"name": "dxu" + u["id"], "secret": u["secret"],
                        "backend": "127.0.0.1:%d" % u["ports"][nm][0], "carrier_mode": car})
        if nm + ALT in u.get("ports", {}) and car2:
            new.append({"name": "dxu" + u["id"] + "b", "secret": alt_secret(u["secret"]),
                        "backend": "127.0.0.1:%d" % u["ports"][nm + ALT][0], "carrier_mode": car2})
    if new == d.get("profiles"):
        return False
    old = dict(d)
    d["profiles"] = new
    write_profiles(path, d)
    if os.path.exists(RELAY_BIN) and not DRY:
        rc, out = sh([RELAY_BIN, "-config", "%s/%s/config.json" % (TPDIR, nm), "-profiles-file", path, "-check"], 20)
        if rc != 0:
            write_profiles(path, old)
            raise ValueError("relay rejected the profiles: " + out.strip()[-160:])
    if restart:
        sh(["systemctl", "restart", "tproxy@%s.service" % nm], 60)
    return True


def resync_all():
    with ULOCK:
        for i in reg_load():
            try:
                sync_profiles(i["name"])
            except Exception:
                pass


def detach_instance(nm):
    with ULOCK:
        ch = False
        for u in UD["users"]:
            gone = [u["ports"].pop(k) for k in (nm, nm + ALT) if k in u.get("ports", {})]
            if gone:
                queue_apply(drop=[g[0] for g in gone]); ch = True
                u["inst"] = [x for x in u.get("inst", []) if x != nm]
        if ch:
            users_save(); write_acct(apply=False); queue_apply(nft=True)


def detach_all():
    for i in list(UD["users"]):
        for nm in base_names(i):
            detach_instance(nm)


def user_view(u):
    lv = LIVE["users"].get(u["id"], {})
    run = should_run(u)
    lks = sub_links(u)
    return {"id": u["id"], "name": u["name"], "secret": u["secret"], "inst": list(u.get("inst", [])),
            "limit": u.get("limit", 0), "used": u.get("used", 0), "up": u.get("up", 0), "down": u.get("down", 0),
            "expiry": u.get("expiry", 0), "enabled": u.get("enabled", True), "created": u.get("created", 0), "last": u.get("last", 0),
            "online": bool(lv.get("live")) and run, "live": lv.get("live", 0),
            "su": lv.get("su", 0), "sd": lv.get("sd", 0),
            "links": [l["url"] for l in lks],
            "lk": [{"i": l["name"], "c": l["carrier"], "u": l["url"]} for l in lks]}


def parse_user(d, cur=None):
    name = str(d.get("name", cur["name"] if cur else "")).strip()
    if not name or len(name) > 40 or re.search(r"[\x00-\x1f<>\"'&]", name):
        raise ValueError("invalid name")
    inst = d.get("inst", cur["inst"] if cur else [])
    inst = [inst] if isinstance(inst, str) else list(inst or [])
    inst = list(dict.fromkeys(str(x) for x in inst))
    if not inst:
        raise ValueError("select at least one inbound")
    for x in inst:
        if not find_inst(x):
            raise ValueError("no such inbound")
    try:
        lim = int(d.get("limit", cur["limit"] if cur else 0) or 0)
        exp = int(d.get("expiry", cur["expiry"] if cur else 0) or 0)
    except (TypeError, ValueError):
        raise ValueError("invalid number")
    if lim < 0 or exp < 0:
        raise ValueError("invalid number")
    sec = str(d.get("secret", "") or "").strip().lower()
    if sec and not re.match(r"^[0-9a-f]{32}$", sec):
        raise ValueError("secret must be 32 hex characters")
    return {"name": name, "inst": inst, "limit": lim, "expiry": exp, "secret": sec,
            "enabled": bool(d.get("enabled", cur.get("enabled", True) if cur else True))}


def user_find(uid):
    for u in UD["users"]:
        if u["id"] == uid:
            return u
    return None


def user_apply(u, touched, nft=True, restart_units=False):
    """cheap, validated file work now; systemd / nft / relay restarts are queued."""
    for inst, (a, b) in u.get("ports", {}).items():
        write_env(u, a, b, inst)
    if nft:
        write_acct(apply=False)
    changed = [nm for nm in touched if sync_profiles(nm, restart=False)]   # raises if the relay rejects it
    queue_apply(nft=nft, restart=changed, units=[pr[0] for pr in u.get("ports", {}).values()] if restart_units else ())


def user_create(d):
    with ULOCK:
        p = parse_user(d)
        u = {"id": secrets.token_hex(4), "name": p["name"], "secret": p["secret"] or secrets.token_hex(16),
             "inst": p["inst"], "limit": p["limit"], "expiry": p["expiry"], "enabled": p["enabled"],
             "created": int(time.time() * 1000), "used": 0, "up": 0, "down": 0, "ports": {}}
        UD["users"].append(u)            # first, so alloc_ports() sees the ports already given to this user
        for nm in p["inst"]:
            u["ports"][nm] = alloc_ports()
        ensure_alt(u)
        users_save()
        try:
            user_apply(u, p["inst"])
        except Exception:
            user_remove(u["id"])
            raise
        return u


def user_update(uid, d):
    with ULOCK:
        u = user_find(uid)
        if not u:
            raise ValueError("no such user")
        p = parse_user(d, u)
        old = set(u.get("inst", []))
        new = set(p["inst"])
        sec_changed = bool(p["secret"]) and p["secret"] != u["secret"]
        drops = []
        for nm in old - new:
            drops += [u["ports"].pop(k)[0] for k in (nm, nm + ALT) if k in u["ports"]]
        for nm in new - old:
            u["ports"][nm] = alloc_ports()
        if sec_changed:
            u["secret"] = p["secret"]
        u.update(name=p["name"], inst=p["inst"], limit=p["limit"], expiry=p["expiry"], enabled=p["enabled"])
        ensure_alt(u)
        users_save()
        touched = (old ^ new) | (new if sec_changed else set())
        queue_apply(drop=drops)
        user_apply(u, touched, nft=bool(old ^ new), restart_units=sec_changed)
        return u


def user_remove(uid):
    with ULOCK:
        u = user_find(uid)
        if not u:
            return
        ports = [pr[0] for pr in u.get("ports", {}).values()]
        UD["users"] = [x for x in UD["users"] if x is not u]
        users_save()
        write_acct(apply=False)
        changed = []
        for nm in base_names(u):
            try:
                if sync_profiles(nm, restart=False):
                    changed.append(nm)
            except Exception:
                pass
        queue_apply(nft=True, restart=changed, drop=ports)


def user_action(uid, act):
    with ULOCK:
        u = user_find(uid)
        if not u:
            raise ValueError("no such user")
        if act == "toggle":
            u["enabled"] = not u.get("enabled", True)
        elif act == "reset":
            u["used"] = u["up"] = u["down"] = 0
        elif act == "remove":
            user_remove(uid)
            return
        users_save()
        queue_apply()


def boot_users():
    users_load()
    pubip_refresh_t = threading.Thread(target=pubip_refresh, daemon=True)
    pubip_refresh_t.start()
    def first():
        try:
            with ULOCK:
                mig = [u for u in UD["users"] if ensure_alt(u)]
                if mig:
                    users_save()
                    for u in mig:
                        for k, (a, b) in u["ports"].items():
                            write_env(u, a, b, k)
            for nm in {n for u in mig for n in base_names(u)}:
                sync_profiles(nm)
            write_acct()
            reconcile(force=True)
        except Exception:
            pass
    threading.Thread(target=first, daemon=True).start()
    threading.Thread(target=apply_worker, daemon=True).start()
    threading.Thread(target=syssampler, daemon=True).start()
    threading.Thread(target=sampler, daemon=True).start()


def sync_result(c, password=None):
    """Keep install-result.env equal to the real panel settings (menu and web panel share config.json)."""
    import shlex
    try:
        with open(RES) as f:
            old = dict(re.findall(r"^(DXWP_[A-Z_]+)=(.*)$", f.read(), re.M))
    except Exception:
        return
    cf = c.get("certFile") or ""
    scheme = "https" if cf else "http"
    host = old.get("DXWP_HOST", "")
    m = re.match(r"^/root/cert/([^/]+)/", cf)
    if m and m.group(1) != "ip":
        host = shlex.quote(m.group(1))
    elif m and PUBIP[0]:
        host = shlex.quote(PUBIP[0])
    q = shlex.quote
    base = c["basePath"].strip("/")
    hv = shlex.split(host)[0] if host else ""
    lines = ["DXWP_USERNAME=%s" % q(c["username"]),
             "DXWP_PASSWORD=%s" % (q(password) if password else old.get("DXWP_PASSWORD", "''")),
             "DXWP_PORT=%s" % q(str(c["port"])), "DXWP_WEB_BASE_PATH=%s" % q(base), "DXWP_SCHEME=%s" % q(scheme),
             "DXWP_HOST=%s" % (host or "''"), "DXWP_API_TOKEN=%s" % old.get("DXWP_API_TOKEN", q(c.get("apiToken", ""))),
             "DXWP_DB_TYPE=json", "DXWP_ACCESS_URL=%s" % q("%s://%s:%s/%s" % (scheme, hv, c["port"], base))]
    tmp = RES + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, RES)


# ---------------------------------------------------------------- subscription service (3x-ui style)
# Separate listener (default 2069). URL = <path><user secret>. Browsers get the DX sub page, apps get the
# base64 list of proxy links with a Subscription-Userinfo header, exactly like a 3x-ui subscription.
SUB = {"srv": None, "err": "", "lock": threading.Lock(), "page": None}
SUBKEYS = ("subEnable", "subListen", "subDomain", "subPort", "subPath", "subURI")


def sub_norm_path(p):
    p = "/" + str(p or "/sub/").strip().strip("/") + "/"
    if not re.match(r"^(/[A-Za-z0-9._~%-]{1,64}){1,4}/$", p):
        raise ValueError("invalid path")
    return p


def sub_cfg(c=None):
    c = c or CFG
    return {"enable": bool(c.get("subEnable")), "listen": c.get("subListen", ""), "domain": c.get("subDomain", ""),
            "port": int(c.get("subPort") or 2069), "path": c.get("subPath") or "/sub/", "uri": c.get("subURI", "")}


def sub_base(c=None):
    """address shown in the panel (without the secret). {host} = let the browser fill in the host it is on."""
    s = sub_cfg(c)
    if s["uri"]:
        return s["uri"]
    c = c or CFG
    cf = c.get("certFile") or ""
    host = s["domain"]
    m = re.match(r"^/root/cert/([^/]+)/", cf)
    if not host and m and m.group(1) != "ip":
        host = m.group(1)
    host = host or PUBIP[0] or "{host}"
    return "%s://%s:%d%s" % ("https" if cf else "http", host, s["port"], s["path"])


def sub_links(u):
    """one entry per attached inbound: what the sub page lists and what apps receive."""
    out = []
    mp = {i["id"]: i for i in instances()}
    for nm in u.get("inst", []):
        i = mp.get(nm)
        if not i:
            continue
        host = i.get("hostname") or nm
        cs = i.get("carriers") or [i.get("carrier", "")]
        for k, car in enumerate(cs):
            sec = u["secret"] if k == 0 else alt_secret(u["secret"])
            if k and nm + ALT not in u.get("ports", {}):
                continue
            out.append({"name": nm, "host": host, "carrier": car, "mode": i.get("mode", ""),
                        "url": "https://t.me/webproxy?server=%s&secret=%s" % (host, sec)})
    return out


def sub_user(sec):
    sec = sec.lower()
    if sec.startswith("dd") and len(sec) == 34:
        sec = sec[2:]
    if not re.match(r"^[0-9a-f]{32}$", sec):
        return None
    with ULOCK:
        for u in UD["users"]:
            if u["secret"] == sec:
                return u
    return None


def sub_page(u, host=""):
    if SUB["page"] is None:
        with open(os.path.join(WEB, "sub.html"), encoding="utf-8") as f:
            SUB["page"] = f.read()
    with ULOCK:
        v = user_view(u)
        data = {"id": u["secret"][:16], "name": u["name"], "enabled": bool(u.get("enabled", True)), "down": u.get("down", 0),
                "up": u.get("up", 0), "used": u.get("used", 0), "limit": u.get("limit", 0), "expiry": u.get("expiry", 0),
                "last": u.get("last", 0), "links": sub_links(u) if should_run(u) else []}
    data["sub"] = sub_base().replace("{host}", host or "localhost") + u["secret"]
    js = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace("&", "\\u0026")
    page = SUB["page"]
    k = page.index('<script id="dx-data" type="application/json">') + len('<script id="dx-data" type="application/json">')
    return (page[:k] + js + page[k:]).encode()


class SubH(BaseHTTPRequestHandler):
    server_version = "dxsub"
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def out(self, code, body=b"", ctype="text/plain; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy", CSP)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            s = sub_cfg()
            path, _, qs = self.path.partition("?")
            path = path.replace("//", "/")
            if not path.startswith(s["path"]) or len(path) == len(s["path"]):
                return self.out(404, b"Not found")
            u = sub_user(path[len(s["path"]):].strip("/"))
            if not u:
                return self.out(404, b"Not found")
            q = dict(x.split("=", 1) if "=" in x else (x, "") for x in qs.split("&") if x)
            ua = self.headers.get("Accept", "")
            if q.get("html") == "1" or ("text/html" in ua and "plain" not in q):
                h = (self.headers.get("Host") or "").strip()
                h = h[:h.index("]") + 1] if h.startswith("[") else h.rsplit(":", 1)[0]
                return self.out(200, sub_page(u, h), "text/html; charset=utf-8")
            links = [x["url"] for x in sub_links(u)] if should_run(u) else []
            body = base64.b64encode("\n".join(links).encode()).decode().encode() if "plain" not in q else "\n".join(links).encode()
            ex = {"Subscription-Userinfo": "upload=%d; download=%d; total=%d; expire=%d" % (
                      u.get("up", 0), u.get("down", 0), u.get("limit", 0), int((u.get("expiry") or 0) / 1000)),
                  "Profile-Update-Interval": "12", "Profile-Title": "DX WebProxy",
                  "Content-Disposition": 'inline; filename="%s"' % u["secret"][:16]}
            self.out(200, body, "text/plain; charset=utf-8", ex)
        except Exception:
            try:
                self.out(500, b"error")
            except Exception:
                pass


def sub_stop():
    with SUB["lock"]:
        s = SUB["srv"]
        SUB["srv"] = None
        if s:
            try:
                s.shutdown()
                s.server_close()
            except Exception:
                pass


def sub_start():
    """(re)start the listener from the current config. Returns "" or an error text."""
    sub_stop()
    s = sub_cfg()
    SUB["err"] = ""
    if not s["enable"]:
        return ""
    try:
        srv = ThreadingHTTPServer((s["listen"] or "0.0.0.0", s["port"]), SubH)
    except OSError as e:
        SUB["err"] = "port %d: %s" % (s["port"], e.strerror or e)
        return SUB["err"]
    srv.daemon_threads = True
    cf, kf = CFG.get("certFile"), CFG.get("keyFile")
    if cf and kf:
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.load_cert_chain(cf, kf)
            srv.socket = ctx.wrap_socket(srv.socket, server_side=True, do_handshake_on_connect=False)

            def watch(me=srv):
                last = os.path.getmtime(cf)
                while SUB["srv"] is me:
                    time.sleep(30)
                    try:
                        m = os.path.getmtime(cf)
                        if m != last:
                            ctx.load_cert_chain(cf, kf)
                            last = m
                    except Exception:
                        pass
            threading.Thread(target=watch, daemon=True).start()
        except Exception as e:
            print("WARNING: sub service without TLS, cannot load certificate: %s" % e, flush=True)
    SUB["srv"] = srv
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("DXWP sub listening on %s:%d%s" % (s["listen"] or "0.0.0.0", s["port"], s["path"]), flush=True)
    return ""


def sub_save(d):
    c = load()
    en = bool(d.get("enable"))
    listen = str(d.get("listen", "")).strip()
    if listen and not (IPV4_RE.match(listen) or re.match(r"^[0-9a-fA-F:]{2,45}$", listen)):
        raise ValueError("invalid listen address")
    dom = str(d.get("domain", "")).strip().lower()
    if dom and not (HOST_RE.match(dom) or IPV4_RE.match(dom)):
        raise ValueError("invalid domain")
    try:
        port = int(str(d.get("port") or 2069).strip())
    except ValueError:
        raise ValueError("invalid port")
    if not 1 <= port <= 65535:
        raise ValueError("invalid port")
    if port == int(c["port"]):
        raise ValueError("port is used by the panel")
    path = sub_norm_path(d.get("path"))
    uri = str(d.get("uri", "")).strip()
    if uri:
        if not re.match(r"^https?://[^\s<>\"'\\]+$", uri):
            raise ValueError("invalid URI")
        if not uri.endswith("/"):
            uri += "/"
    prev = {k: c.get(k) for k in SUBKEYS}
    c.update(subEnable=en, subListen=listen, subDomain=dom, subPort=port, subPath=path, subURI=uri)
    save(c)
    CFG.update({k: c[k] for k in SUBKEYS})
    err = sub_start()
    if err:                                   # keep the last working settings, like 3x-ui refusing a busy port
        c.update(prev)
        save(c)
        CFG.update({k: c[k] for k in SUBKEYS})
        sub_start()
        raise ValueError(err)
    sync_result(c)
    return {"ok": 1, "base": sub_base(), "running": bool(SUB["srv"])}


# ---------------------------------------------------------------- HTTP
CFG = {}
SESS = {}          # token -> expiry
FAIL = {}          # ip -> (count, until)
TLS_ON = [False]
GZ = {}
CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; font-src data:; "
       "img-src data:; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


class H(BaseHTTPRequestHandler):
    server_version = "DXWP"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 30

    def log_message(self, *a):
        pass

    # -- plumbing
    def send(self, code, body=b"", ctype="application/json", cookie=None, loc=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text") or "json" in ctype else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if getattr(self, "_enc", None):
            self.send_header("Content-Encoding", self._enc)
            self.send_header("Vary", "Accept-Encoding")
            self._enc = None
        if self.close_connection:
            self.send_header("Connection", "close")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        if TLS_ON[0]:
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        if loc:
            self.send_header("Location", loc)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def authed(self):
        tk = CFG.get("apiToken") or ""
        ah = self.headers.get("Authorization", "")
        if tk and ah.startswith("Bearer ") and hmac.compare_digest(ah[7:].strip().encode(), tk.encode()):
            return True
        try:
            tok = SimpleCookie(self.headers.get("Cookie", ""))["dxwp_s"].value
        except Exception:
            return False
        exp = SESS.get(tok)
        if exp and exp > time.time():
            return True
        SESS.pop(tok, None)
        return False

    def body(self):
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            raise ValueError("bad content-type")
        return json.loads(self._raw or b"{}")

    def page(self, name):
        import gzip
        path = os.path.join(WEB, name)
        st = os.stat(path)
        hit = GZ.get(name)
        if not hit or hit[0] != st.st_mtime_ns:
            with open(path, "rb") as f:
                raw = f.read()
            hit = GZ[name] = (st.st_mtime_ns, raw, gzip.compress(raw, 6))
        if "gzip" in self.headers.get("Accept-Encoding", ""):
            self._enc = "gzip"
            return self.send(200, hit[2], "text/html")
        self.send(200, hit[1], "text/html")

    # -- dispatch
    def do_HEAD(self):
        self.go()

    def do_GET(self):
        self.go()

    def do_POST(self):
        self.go()

    def go(self):
        self._raw = b""
        if self.command == "POST":
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if n < 0 or n > 65536:
                self.close_connection = True
                return self.send(413, {"error": "too large"})
            self._raw = self.rfile.read(n) if n else b""
        u = urlparse(self.path)
        base = CFG["basePath"]
        if base != "/" and u.path == base.rstrip("/"):
            return self.send(302, b"", "text/plain", loc=base)
        if not u.path.startswith(base):
            return self.send(404, b"404 page not found\n", "text/plain")
        rel = u.path[len(base):]
        try:
            if rel == "" and self.command in ("GET", "HEAD"):
                return self.page("index.html" if self.authed() else "login.html")
            if not rel.startswith("api/"):
                return self.send(404, b"404 page not found\n", "text/plain")
            self.api(rel[4:].strip("/"), parse_qs(u.query))
        except Busy:
            self.send(409, {"error": "busy"})
        except ValueError as e:
            self.send(400, {"error": str(e)})
        except Exception:
            self.send(500, {"error": "server error"})

    # -- API
    def api(self, ep, q):
        post = self.command == "POST"
        if ep == "login" and post:
            return self.login()
        if not self.authed():
            return self.send(401, {"error": "auth"})
        if post:
            d = self.body()
        if ep == "logout" and post:
            try:
                SESS.pop(SimpleCookie(self.headers.get("Cookie", ""))["dxwp_s"].value, None)
            except Exception:
                pass
            return self.send(200, {"ok": 1}, cookie="dxwp_s=; Max-Age=0; Path=%s; HttpOnly; SameSite=Strict" % CFG["basePath"])
        if ep == "instances":
            return self.send(200, instances())
        if ep == "verify":
            return self.send(200, verify())
        if ep == "job":
            j = JOBS.get((q.get("id") or [""])[0])
            return self.send(200 if j else 404, j or {"error": "no job"})
        if ep == "update" and not post:
            return self.send(200, update_info((q.get("force") or [""])[0] == "1"))
        if ep == "update" and post:
            ui = update_info(True)
            if not ui["available"]:
                return self.send(409, {"error": "no update available"})
            ok = start_update(ui["tag"])
            return self.send(200 if ok else 500, {"ok": ok})
        if ep == "settings" and not post:
            return self.send(200, {"port": CFG["port"], "basePath": CFG["basePath"], "username": CFG["username"],
                                   "tls": TLS_ON[0], "certFile": CFG["certFile"], "keyFile": CFG["keyFile"],
                                   "cert": cert_info(CFG["certFile"]), "version": cur_version(), "ip": PUBIP[0],
                                   "subEnable": bool(CFG.get("subEnable")), "subListen": CFG.get("subListen", ""),
                                   "subDomain": CFG.get("subDomain", ""), "subPort": CFG.get("subPort", 2069),
                                   "subPath": CFG.get("subPath", "/sub/"), "subURI": CFG.get("subURI", ""),
                                   "subBase": sub_base(), "subRunning": bool(SUB["srv"]), "subError": SUB["err"]})
        if ep == "sysinfo" and not post:
            s = dict(LIVE["sys"]); s["ip"] = PUBIP[0]
            return self.send(200, s)
        if ep == "live" and not post:
            lite = (q.get("lite") or [""])[0] == "1"       # Status page: no per-user data
            out = {"t": int(time.time() * 1000), "sys": LIVE["sys"], "speed": LIVE["speed"],
                   "inst": LIVE["inst"], "instances": instances()}
            if (q.get("h") or [""])[0] == "1":
                out["hist"] = {k: list(v) for k, v in HIST.items()}
            if not lite:
                with ULOCK:
                    out["users"] = [user_view(u) for u in UD["users"]]
            return self.send(200, out)
        if ep == "users" and not post:
            with ULOCK:
                return self.send(200, [user_view(u) for u in UD["users"]])
        if not post:
            return self.send(404, {"error": "unknown"})
        m = re.match(r"^users(?:/([0-9A-Za-z_-]{1,32}))?(?:/(toggle|reset|remove))?$", ep)
        if m:
            uid, act = m.group(1), m.group(2)
            if not uid:
                return self.send(200, {"ok": 1, "id": user_create(d)["id"]})
            if act:
                user_action(uid, act)
            else:
                user_update(uid, d)
            return self.send(200, {"ok": 1})
        if ep == "sub/settings":
            return self.send(200, sub_save(d))
        if ep == "preflight":
            return self.send(200, preflight(d))
        if ep == "cfcheck":
            return self.send(200, cfcheck(d))
        if ep == "install":
            pf = preflight(d)
            if not pf["ok"]:
                return self.send(409, {"error": "preflight", "pre": pf})
            return self.send(200, {"job": start_job("install", ["bash", INSTALLER] + install_args(d), after=resync_all)})
        if ep == "wipe":
            return self.send(200, {"job": start_job("wipe", ["bash", INSTALLER, "--wipe"], after=detach_all)})
        m = re.match(r"^instance/([^/]+)/(restart|remove)$", ep)
        if m:
            inst = find_inst(m.group(1))
            if not inst:
                return self.send(404, {"error": "no such instance"})
            nm, act = inst["name"], m.group(2)
            if act == "restart":
                threading.Thread(target=sh, args=(["systemctl", "restart", "mtproxy@%s.service" % nm, "tproxy@%s.service" % nm], 60), daemon=True).start()
                return self.send(200, {"ok": True})
            if act == "remove":
                detach_instance(nm)
                remove_instance(nm)
                return self.send(200, {"ok": 1})
            return self.send(404, {"error": "unknown action"})
        if ep == "settings":
            return self.save_settings(d)
        if ep == "cert/issue":
            return self.cert_issue(d)
        if ep == "cert/custom":
            cf, kf = str(d.get("cert", "")).strip(), str(d.get("key", "")).strip()
            if not (os.path.isfile(cf) and os.path.isfile(kf)):
                raise ValueError("certificate or key file not found")
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            try:
                ctx.load_cert_chain(cf, kf)
            except ssl.SSLError:
                raise ValueError("certificate and key do not match")
            c = load(); c["certFile"], c["keyFile"] = cf, kf; save(c); sync_result(c)
            restart_panel()
            return self.send(200, {"ok": 1, "restart": 1})
        if ep == "cert/remove":
            c = load(); c["certFile"] = c["keyFile"] = ""; save(c); sync_result(c)
            restart_panel()
            return self.send(200, {"ok": 1, "restart": 1})
        if ep == "restart":
            restart_panel()
            return self.send(200, {"ok": 1})
        return self.send(404, {"error": "unknown"})

    def login(self):
        ip = self.client_address[0]
        cnt, until = FAIL.get(ip, (0, 0))
        if until > time.time():
            return self.send(429, {"error": "locked"})
        d = self.body()
        u, p = str(d.get("username", "")), str(d.get("password", ""))
        ok_u = hmac.compare_digest(u.encode(), CFG["username"].encode())
        ok_p = vpw(p, CFG["passHash"])
        if not (ok_u and ok_p):
            cnt += 1
            FAIL[ip] = (cnt, time.time() + 300 if cnt >= 5 else 0)
            if cnt >= 5:
                FAIL[ip] = (0, time.time() + 300)
            time.sleep(0.4)
            return self.send(401, {"error": "bad"})
        FAIL.pop(ip, None)
        remember = bool(d.get("remember"))
        tok = secrets.token_urlsafe(32)
        SESS[tok] = time.time() + (30 * 86400 if remember else 12 * 3600)
        ck = "dxwp_s=%s; Path=%s; HttpOnly; SameSite=Strict%s%s" % (
            tok, CFG["basePath"], "; Secure" if TLS_ON[0] else "", "; Max-Age=%d" % (30 * 86400) if remember else "")
        self.send(200, {"ok": 1}, cookie=ck)

    def save_settings(self, d):
        c = load()
        changed = False
        if "port" in d and str(d["port"]).strip():
            p = int(d["port"])
            if not 1 <= p <= 65535:
                raise ValueError("invalid port")
            if p != c["port"] and not port_free(p, c["listen"]):
                raise ValueError("port is already in use")
            changed |= p != c["port"]; c["port"] = p
        if "basePath" in d:
            b = norm_base(str(d["basePath"]))
            changed |= b != c["basePath"]; c["basePath"] = b
        un = str(d.get("username", "")).strip()
        if un:
            if not re.match(r"^[A-Za-z0-9._-]{3,32}$", un):
                raise ValueError("invalid username")
            c["username"] = un
        pw = str(d.get("password", ""))
        if pw:
            if len(pw) < 8:
                raise ValueError("password too short")
            c["passHash"] = hpw(pw)
            SESS.clear()
        save(c)
        sync_result(c, pw or None)
        restart_panel()
        self.send(200, {"ok": 1, "port": c["port"], "basePath": c["basePath"], "restart": 1})

    def cert_issue(self, d):
        # panel.sh frees port 80 itself (stops whatever holds it, restores it afterwards), so the web UI only
        # sends the domain or the IP.
        mode, val = d.get("mode"), str(d.get("value", "")).strip().lower()
        if mode == "domain":
            if not HOST_RE.match(val):
                raise ValueError("invalid domain")
            argv = ["bash", PANELSH, "ssl-domain", val]
        elif mode == "ip":
            ip6 = str(d.get("ipv6", "")).strip()
            if not IPV4_RE.match(val) or any(int(x) > 255 for x in val.split(".")):
                raise ValueError("invalid IPv4")
            if ip6 and not re.match(r"^[0-9a-fA-F:]{2,45}$", ip6):
                raise ValueError("invalid IPv6")
            argv = ["bash", PANELSH, "ssl-ip", val] + ([ip6] if ip6 else [])
        else:
            raise ValueError("invalid mode")
        self.send(200, {"job": start_job("cert", argv, after=lambda: restart_panel(3), extra_env={"DXWP_NO_RESTART": "1"})})


def serve():
    global CFG
    CFG = load()
    CFG, pw = init_cfg(CFG) if (not CFG["username"] or not CFG["port"]) else (CFG, None)
    if pw:
        print("First run credentials -> username: %s  password: %s" % (CFG["username"], pw), flush=True)
    boot_users()
    sub_start()
    srv = ThreadingHTTPServer((CFG["listen"], int(CFG["port"])), H)
    srv.daemon_threads = True
    cf, kf = CFG["certFile"], CFG["keyFile"]
    if cf and kf:
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.load_cert_chain(cf, kf)
            srv.socket = ctx.wrap_socket(srv.socket, server_side=True, do_handshake_on_connect=False)
            TLS_ON[0] = True

            def watch():
                last = os.path.getmtime(cf)
                while True:
                    time.sleep(60)
                    try:
                        m = os.path.getmtime(cf)
                        if m != last:
                            ctx.load_cert_chain(cf, kf)
                            last = m
                    except Exception:
                        pass
            threading.Thread(target=watch, daemon=True).start()
        except Exception as e:
            print("WARNING: TLS disabled, cannot load certificate: %s" % e, flush=True)
    print("DXWP %s listening on %s:%s%s (%s)" % (VERSION, CFG["listen"], CFG["port"], CFG["basePath"],
                                                 "https" if TLS_ON[0] else "http"), flush=True)
    srv.serve_forever()


# ---------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser(prog="dxwp_panel")
    sp = ap.add_subparsers(dest="cmd")
    sp.add_parser("run")
    sp.add_parser("show")
    sp.add_parser("init")
    s = sp.add_parser("setting")
    for k in ("-port", "-username", "-password", "-webBasePath", "-listenIP", "-subEnable", "-subPort", "-subPath", "-subListen", "-subDomain", "-subURI"):
        s.add_argument(k)
    s.add_argument("-reset", action="store_true")
    s.add_argument("-show", action="store_true")
    s.add_argument("-getApiToken", action="store_true")
    s.add_argument("-resetApiToken", action="store_true")
    sp.add_parser("users")
    ce = sp.add_parser("cert")
    ce.add_argument("-webCert")
    ce.add_argument("-webCertKey")
    ce.add_argument("-remove", action="store_true")
    a = ap.parse_args()
    c = load()
    if a.cmd in (None, "run"):
        return serve()
    if a.cmd == "users":
        users_load()
        for u in UD["users"]:
            print("%-24s %-9s used %s / %s  inbounds: %s" % (u["name"], "active" if should_run(u) else "stopped", hb(u.get("used", 0)),
                  hb(u["limit"]) if u.get("limit") else "unlimited", ",".join(u.get("inst", [])) or "-"))
        return
    if a.cmd == "init":
        c, pw = init_cfg(c)
        if pw:
            print("username: %s\npassword: %s" % (c["username"], pw))
    elif a.cmd == "setting":
        if a.reset:
            c.update(username="", passHash="", port=0, basePath="/")
            c, pw = init_cfg(c)
            print("reset -> username: %s  password: %s" % (c["username"], pw))
        if a.port:
            if not 1 <= int(a.port) <= 65535:
                sys.exit("invalid port")
            c["port"] = int(a.port)
        if a.username:
            c["username"] = a.username
        if a.password:
            c["passHash"] = hpw(a.password)
        if a.webBasePath:
            c["basePath"] = norm_base(a.webBasePath)
        if a.subEnable is not None:
            c["subEnable"] = a.subEnable.lower() in ("1", "true", "yes", "on")
        if a.subPort:
            c["subPort"] = int(a.subPort)
        if a.subPath:
            c["subPath"] = sub_norm_path(a.subPath)
        if a.subListen is not None:
            c["subListen"] = a.subListen
        if a.subDomain is not None:
            c["subDomain"] = a.subDomain.lower()
        if a.subURI is not None:
            c["subURI"] = a.subURI
        if a.listenIP:
            if not (IPV4_RE.match(a.listenIP) or ":" in a.listenIP):
                sys.exit("invalid listen IP")
            c["listen"] = a.listenIP
        if a.resetApiToken or (a.getApiToken and not c.get("apiToken")):
            c["apiToken"] = rnd(48)
        save(c)
        sync_result(c, a.password or (pw if a.reset else None))
        if a.getApiToken or a.resetApiToken:
            print("apiToken: %s" % c["apiToken"])
            return
    elif a.cmd == "cert":
        if a.remove:
            c["certFile"] = c["keyFile"] = ""
        if a.webCert:
            c["certFile"] = a.webCert
        if a.webCertKey:
            c["keyFile"] = a.webCertKey
        save(c)
        sync_result(c)
    c = load()
    print("port: %s\nbasePath: %s\nusername: %s\ncertFile: %s\nkeyFile: %s\nlisten: %s" % (
        c["port"], c["basePath"], c["username"], c["certFile"] or "-", c["keyFile"] or "-", c["listen"]))
    print("sub: %s\nsubPort: %s\nsubPath: %s\nsubBase: %s" % ("on" if c.get("subEnable") else "off", c.get("subPort"),
                                                                 c.get("subPath"), sub_base(c)))


if __name__ == "__main__":
    main()
