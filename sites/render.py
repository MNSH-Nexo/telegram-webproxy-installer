#!/usr/bin/env python3
"""Render one random cover-site template for a hostname.

usage: render.py SITES_DIR HOSTNAME OUTDIR [TEMPLATE]
Every template is a folder next to this file. Placeholders (__BRAND__, __DOMAIN__, colours, ...) are
filled per domain, so two domains never serve the same page. Prints the template name that was used.
"""
import hashlib, os, random, re, secrets, shutil, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import art

PAL = {
    "edgewire": [("#7c6cff", "#22d3ee"), ("#f97316", "#facc15"), ("#10b981", "#22d3ee"), ("#ec4899", "#8b5cf6"), ("#3b82f6", "#06b6d4")],
    "cdncrest": [("#0f766e", "#2dd4bf"), ("#1d4ed8", "#60a5fa"), ("#7c3aed", "#c4b5fd"), ("#b45309", "#fbbf24"), ("#be123c", "#fb7185")],
    "tesla":    [("#e31937", "#e31937"), ("#f97316", "#f97316"), ("#3b82f6", "#3b82f6"), ("#22c55e", "#22c55e"), ("#a855f7", "#a855f7")],
}
NAMES = [("Andrew", "Андрей"), ("Dmitry", "Дмитрий"), ("Sergey", "Сергей"), ("Alex", "Алексей"), ("Max", "Максим"), ("Igor", "Игорь"), ("Victor", "Виктор"), ("Roman", "Роман")]
SKIP = {"www", "cf", "proxy", "tg", "mt", "mtproto", "vpn", "web", "app", "m", "cdn", "api"}


def brand(host):
    labels = [x for x in host.lower().split(".") if x]
    core = labels[-2] if len(labels) >= 2 else labels[0]
    if len(labels) > 2 and len(core) < 3:
        core = labels[-3]
    core = re.sub(r"[^a-z]+", " ", core).strip().split(" ")[0] if re.search(r"[a-z]", core) else ""
    if len(core) < 3 or core in SKIP:
        core = secrets.choice(["Nimbus", "Vector", "Orbit", "Lumen", "Cobalt", "Arcade", "Tessera", "Quanta"])
    return core.capitalize()


def csp_safe(html, rel, out, host):
    """The relay serves the site under  default-src 'self'; style-src 'self'  - inline <style>, style="" attributes and inline
    scripts are blocked by the browser (the page then shows up as unstyled HTML). Move all CSS into a same-origin file."""
    css = []
    html = re.sub(r"<style[^>]*>(.*?)</style>", lambda m: css.append(m.group(1)) or "", html, flags=re.S)
    rules, names = [], {}

    def conv(m):
        tag = m.group(0)
        sm = re.search(r'\sstyle="([^"]*)"', tag)
        decl = [d.strip() for d in sm.group(1).split(";") if d.strip()]
        key = ";".join(decl)
        if key not in names:
            names[key] = "_x%d" % (len(names) + 1)
            rules.append(".%s{%s}" % (names[key], ";".join(d if "!important" in d else d + "!important" for d in decl)))
        tag = tag.replace(sm.group(0), "", 1)
        cm = re.search(r'\sclass="([^"]*)"', tag)
        if cm:
            return tag.replace(cm.group(0), ' class="%s %s"' % (cm.group(1), names[key]), 1)
        return re.sub(r"^<([A-Za-z][\w:-]*)", lambda t: '<%s class="%s"' % (t.group(1), names[key]), tag, count=1)

    html = re.sub(r'<[A-Za-z][^<>]*\sstyle="[^"]*"[^<>]*>', conv, html)
    css_text = "\n".join(css) + "\n" + "\n".join(rules) + "\n"
    name = "assets/s-" + hashlib.sha1((host + rel).encode()).hexdigest()[:8] + ".css"
    os.makedirs(os.path.join(out, "assets"), exist_ok=True)
    with open(os.path.join(out, name), "w", encoding="utf-8") as fh:
        fh.write(css_text)
    href = os.path.relpath(os.path.join(out, name), os.path.dirname(os.path.join(out, rel)))
    link = '<link rel="stylesheet" href="%s">' % href
    return html.replace("</head>", link + "\n</head>", 1) if "</head>" in html else link + html


def main():
    base, host, out = sys.argv[1:4]
    want = sys.argv[4] if len(sys.argv) > 4 else ""
    tpls = sorted(d for d in os.listdir(base) if os.path.isfile(os.path.join(base, d, "index.html")))
    if not tpls:
        sys.exit(2)
    t = want if want in tpls else secrets.choice(tpls)
    a, b = secrets.choice(PAL.get(t, [("#2563eb", "#22d3ee")]))
    en, ru = secrets.choice(NAMES)
    br = brand(host)
    sub = {"__BRAND__": br, "__BRANDL__": br.lower(), "__DOMAIN__": host, "__A__": a, "__B__": b,
           "__NAME_EN__": en, "__NAME_RU__": ru, "__INIT__": en[0]}
    rng = random.Random(host)          # same domain -> same artwork, different domains -> different artwork
    sub.update({"__ICONS__": art.sprite(), "__ART_AURORA__": art.aurora(a, b, rng), "__ART_GLOBE__": art.globe(a, a if t == "cdncrest" else b, rng),
                "__ART_CHART__": art.chart(a, b, rng), "__ART_DEVICES__": art.devices(a, b, rng), "__ART_CAR__": art.car(a, rng),
                "__ART_LOGOS__": art.logos(rng), "__THUMB_WEB__": art.thumb("web", a, b, rng), "__THUMB_CLI__": art.thumb("cli", a, b, rng), "__THUMB_API__": art.thumb("api", a, b, rng)})
    for k in range(1, 6):
        sub["__ART_CITY%d__" % k] = art.city(a, b, rng, fid="ct%d" % k)
    for k in range(1, 5):
        sub["__AV%d__" % k] = art.avatar(k, rng.choice([a, b]), rng.choice([a, b]))
    for name in art.ICONS:
        sub["__ICON_%s__" % name] = '<svg class="ic" aria-hidden="true"><use href="#i-%s"/></svg>' % name
    pat = re.compile("|".join(map(re.escape, sorted(sub, key=len, reverse=True))))
    src = os.path.join(base, t)
    os.makedirs(out, exist_ok=True)
    for r, _, fs in os.walk(src):
        for f in fs:
            s, d = os.path.join(r, f), os.path.join(out, os.path.relpath(os.path.join(r, f), src))
            os.makedirs(os.path.dirname(d), exist_ok=True)
            if f.endswith((".html", ".svg", ".txt", ".css", ".js")):
                with open(s, encoding="utf-8") as fh:
                    txt = pat.sub(lambda m: sub[m.group(0)], fh.read())
                if f.endswith(".html"):
                    txt = csp_safe(txt, os.path.relpath(d, out), out, host)
                with open(d, "w", encoding="utf-8") as fh:
                    fh.write(txt)
            else:
                shutil.copy2(s, d)
    for r, ds, fs in os.walk(out):            # the relay runs as another user: directories 755, files 644, whatever the umask was
        os.chmod(r, 0o755)
        for f in fs:
            os.chmod(os.path.join(r, f), 0o644)
    print(t)


if __name__ == "__main__":
    main()
