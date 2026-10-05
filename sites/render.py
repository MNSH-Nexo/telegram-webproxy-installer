#!/usr/bin/env python3
"""Render one random cover-site template for a hostname.

usage: render.py SITES_DIR HOSTNAME OUTDIR [TEMPLATE]
Every template is a folder next to this file. Placeholders (__BRAND__, __DOMAIN__, colours, ...) are
filled per domain, so two domains never serve the same page. Prints the template name that was used.
"""
import os, re, secrets, shutil, sys

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
                with open(d, "w", encoding="utf-8") as fh:
                    fh.write(txt)
            else:
                shutil.copy2(s, d)
    print(t)


if __name__ == "__main__":
    main()
