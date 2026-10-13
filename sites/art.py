"""Small SVG illustration kit for the cover sites. Everything is drawn here (no image files, no external requests),
seeded by the hostname, so every domain gets its own globe / skyline / curves in its own palette."""
import math, random


def _f(x):
    return ("%.1f" % x).rstrip("0").rstrip(".")


# rough continent outlines (lon, lat) - enough for a recognisable dotted world map
LAND = [
    [(-168,66),(-140,70),(-125,70),(-95,72),(-80,70),(-62,60),(-55,50),(-67,44),(-76,35),(-81,25),(-97,26),(-97,18),(-90,15),(-83,9),(-78,8),(-86,14),(-105,20),(-112,30),(-117,33),(-124,40),(-125,49),(-135,58),(-150,60),(-165,60)],
    [(-55,60),(-45,60),(-20,70),(-20,82),(-60,82),(-72,76)],
    [(-80,8),(-62,11),(-50,0),(-35,-6),(-40,-22),(-48,-27),(-58,-38),(-65,-42),(-68,-55),(-74,-50),(-72,-30),(-70,-18),(-80,-6)],
    [(-10,36),(-9,43),(-2,48),(5,52),(10,56),(20,60),(30,70),(40,68),(40,50),(30,45),(25,37),(15,38),(5,43)],
    [(-17,21),(-10,35),(10,37),(32,31),(35,12),(51,12),(40,-5),(40,-15),(33,-27),(20,-35),(12,-18),(9,0),(-8,5),(-17,14)],
    [(40,68),(60,72),(100,78),(140,72),(180,68),(160,55),(142,46),(130,35),(122,30),(122,22),(108,18),(105,10),(100,2),(98,16),(90,22),(80,10),(73,20),(66,25),(56,26),(48,30),(36,36),(30,45),(40,50)],
    [(36,30),(48,30),(56,26),(58,20),(45,12),(40,20)],
    [(114,-22),(130,-12),(142,-11),(153,-26),(148,-38),(135,-35),(117,-35)],
    [(-6,50),(2,51),(0,58),(-6,58)], [(130,32),(142,36),(145,44),(140,43)], [(95,5),(106,-6),(120,-8),(140,-4),(130,2),(110,4)],
]


def _land(lat, lon):
    lon = (lon + 180) % 360 - 180
    for poly in LAND:
        inside, j = False, len(poly) - 1
        for i in range(len(poly)):
            xi, yi = poly[i]
            xj, yj = poly[j]
            if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
        if inside:
            return True
    return False


def aurora(a, b, rng):
    """soft blurred colour blobs, used as a hero background"""
    blobs = "".join('<ellipse cx="%d" cy="%d" rx="%d" ry="%d" fill="%s" opacity=".%d"/>' % (
        rng.randint(80, 920), rng.randint(40, 380), rng.randint(160, 340), rng.randint(90, 190), rng.choice([a, b]), rng.randint(32, 55)) for _ in range(5))
    return ('<svg class="aur" viewBox="0 0 1000 420" preserveAspectRatio="xMidYMid slice" aria-hidden="true"><defs><filter id="aurb" x="-30%%" y="-30%%" width="160%%" height="160%%">'
            '<feGaussianBlur stdDeviation="62"/></filter></defs><g filter="url(#aurb)">%s</g></svg>' % blobs)


def globe(a, b, rng):
    R, cx, cy, lon0 = 200, 250, 250, rng.choice([10, 20, 30, 40, 70, 80, 95, -20, -60, -90])
    seed = rng.uniform(0, 6.28)
    dots, sea, pts = [], [], []
    lat = -80
    k = 0
    while lat <= 80:
        k += 1
        step = 3.1 / max(0.2, math.cos(math.radians(lat)))
        lon = 0.0
        while lon < 360:
            la, lo = math.radians(lat), math.radians(lon - lon0)
            vis = math.cos(la) * math.cos(lo)
            if vis > 0.02:
                x, y = cx + R * math.cos(la) * math.sin(lo), cy - R * math.sin(la)
                if _land(lat, lon):
                    dots.append("M%s %sh0" % (_f(x), _f(y)))
                    if vis > 0.45 and rng.random() < 0.012:
                        pts.append((x, y))
                elif (int(lon / step) + k) % 2 == 0:
                    sea.append("M%s %sh0" % (_f(x), _f(y)))
            lon += step
        lat += 3.1
    rng.shuffle(pts)
    pts = pts[:8]
    pins, arcs = "", ""
    for i, (x, y) in enumerate(pts):
        pins += ('<circle cx="%s" cy="%s" r="3.2" fill="%s"/><circle cx="%s" cy="%s" r="3" fill="none" stroke="%s" stroke-width="1.4">'
                 '<animate attributeName="r" values="3;15" dur="2.8s" begin="%.1fs" repeatCount="indefinite"/>'
                 '<animate attributeName="opacity" values=".9;0" dur="2.8s" begin="%.1fs" repeatCount="indefinite"/></circle>') % (_f(x), _f(y), b, _f(x), _f(y), b, i * .35, i * .35)
    for i in range(len(pts) - 1):
        (x1, y1), (x2, y2) = pts[i], pts[i + 1]
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        dx, dy = mx - cx, my - cy
        n = math.hypot(dx, dy) or 1
        k = 38 + math.hypot(x2 - x1, y2 - y1) * .22
        arcs += '<path d="M%s %sQ%s %s %s %s" fill="none" stroke="url(#gla)" stroke-width="1.4" stroke-dasharray="3 5"><animate attributeName="stroke-dashoffset" values="0;-32" dur="3s" repeatCount="indefinite"/></path>' % (
            _f(x1), _f(y1), _f(mx + dx / n * k), _f(my + dy / n * k), _f(x2), _f(y2))
    return ('<svg class="globe" viewBox="0 0 500 500" role="img" aria-label="Global network map"><defs>'
            '<radialGradient id="glb" cx="40%%" cy="35%%" r="75%%"><stop offset="0" stop-color="%s" stop-opacity=".30"/><stop offset=".7" stop-color="%s" stop-opacity=".07"/><stop offset="1" stop-color="%s" stop-opacity="0"/></radialGradient>'
            '<linearGradient id="gla" x1="0" x2="1"><stop offset="0" stop-color="%s"/><stop offset="1" stop-color="%s"/></linearGradient></defs>'
            '<circle cx="250" cy="250" r="238" fill="url(#glb)"/><circle cx="250" cy="250" r="200" fill="none" stroke="%s" stroke-opacity=".35"/>'
            '<path d="%s" stroke="%s" stroke-width="2" stroke-linecap="round" opacity=".16"/><path d="%s" stroke="%s" stroke-width="3.6" stroke-linecap="round" opacity=".75"/>%s%s</svg>') % (a, b, b, a, b, a, "".join(sea), b, "".join(dots), b, arcs, pins)


def chart(a, b, rng, w=400, h=110):
    ys = [h * 0.72]
    for _ in range(9):
        ys.append(max(h * 0.12, min(h * 0.86, ys[-1] + rng.uniform(-0.2, 0.15) * h)))
    xs = [i * w / 9 for i in range(10)]
    d = "M%s %s" % (_f(xs[0]), _f(ys[0]))
    for i in range(1, 10):
        cx = (xs[i - 1] + xs[i]) / 2
        d += "C%s %s %s %s %s %s" % (_f(cx), _f(ys[i - 1]), _f(cx), _f(ys[i]), _f(xs[i]), _f(ys[i]))
    return ('<svg viewBox="0 0 %d %d" preserveAspectRatio="none" class="chart"><defs><linearGradient id="chg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="%s" stop-opacity=".45"/><stop offset="1" stop-color="%s" stop-opacity="0"/></linearGradient></defs>'
            '<path d="%sV%dH0Z" fill="url(#chg)"/><path d="%s" fill="none" stroke="%s" stroke-width="2.2"/></svg>') % (w, h, a, a, d, h, d, b)


def wallpaper(a, b, rng, w=640, h=400, fid="wp"):
    s = "".join('<ellipse cx="%d" cy="%d" rx="%d" ry="%d" fill="%s" opacity=".%d" transform="rotate(%d %d %d)"/>' % (
        rng.randint(0, w), rng.randint(0, h), rng.randint(110, 260), rng.randint(40, 90), rng.choice([a, b, "#fff"]), rng.randint(30, 70), rng.randint(-40, 40), w // 2, h // 2) for _ in range(9))
    return '<defs><linearGradient id="%s" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="%s" stop-opacity=".9"/><stop offset="1" stop-color="%s"/></linearGradient></defs><rect width="%d" height="%d" fill="url(#%s)"/>%s' % (fid, b, a, w, h, fid, s)


def devices(a, b, rng):
    """laptop + phone mock-up, the way a download/landing page shows its product"""
    ui = ''.join('<rect x="%d" y="%d" width="%d" height="9" rx="4.5" fill="#fff" opacity="%s"/>' % (x, y, w, o) for x, y, w, o in ((250, 170, 140, .9), (250, 190, 90, .5)))
    return ('<svg class="dev" viewBox="0 0 760 440" role="img" aria-label="Product preview"><defs><clipPath id="dsc"><rect x="96" y="30" width="520" height="318" rx="8"/></clipPath>'
            '<linearGradient id="dbs" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#d9dde3"/><stop offset="1" stop-color="#9aa1ab"/></linearGradient></defs>'
            '<ellipse cx="380" cy="404" rx="330" ry="16" fill="#000" opacity=".12"/>'
            '<rect x="86" y="20" width="540" height="338" rx="14" fill="#171a21"/><g clip-path="url(#dsc)" transform="translate(96 30) scale(.8125 .795)">%s'
            '<rect x="215" y="150" width="210" height="34" rx="17" fill="#fff" opacity=".92"/><circle cx="234" cy="167" r="6" fill="%s"/><rect x="248" y="163" width="110" height="8" rx="4" fill="%s" opacity=".5"/>%s</g>'
            '<path d="M40 358h632l-26 28a14 14 0 0 1-10 4H76a14 14 0 0 1-10-4z" fill="url(#dbs)"/><rect x="300" y="358" width="112" height="9" rx="4" fill="#7d848f" opacity=".6"/>'
            '<g transform="translate(540 96)"><rect width="150" height="300" rx="26" fill="#14161c"/><rect x="7" y="7" width="136" height="286" rx="20" fill="#f6f8fa"/><rect x="52" y="14" width="46" height="9" rx="4.5" fill="#14161c"/>'
            '<rect x="16" y="40" width="118" height="70" rx="10" fill="%s"/><rect x="16" y="122" width="118" height="12" rx="6" fill="#dfe4ea"/><rect x="16" y="144" width="90" height="10" rx="5" fill="#e8ecf0"/>'
            '<rect x="16" y="172" width="118" height="46" rx="10" fill="#fff" stroke="#e1e6ec"/><rect x="26" y="184" width="60" height="8" rx="4" fill="%s" opacity=".6"/><rect x="26" y="200" width="84" height="7" rx="3.5" fill="#dfe4ea"/>'
            '<rect x="16" y="232" width="118" height="46" rx="10" fill="#fff" stroke="#e1e6ec"/><rect x="26" y="244" width="52" height="8" rx="4" fill="%s" opacity=".6"/><rect x="26" y="260" width="74" height="7" rx="3.5" fill="#dfe4ea"/></g></svg>') % (
        wallpaper(a, b, rng, 640, 400, "dwp"), b, a, ui, a, a, b)


def car(color, rng):
    return ('<svg class="car" viewBox="0 0 620 230" role="img" aria-label="Electric car"><defs>'
            '<linearGradient id="cbd" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="%s"/><stop offset="1" stop-color="#0d0f12"/></linearGradient>'
            '<linearGradient id="cgl" x1="0" x2="1"><stop offset="0" stop-color="#9fb4c7"/><stop offset="1" stop-color="#26303a"/></linearGradient>'
            '<radialGradient id="cgw" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="%s" stop-opacity=".35"/><stop offset="1" stop-color="%s" stop-opacity="0"/></radialGradient></defs>'
            '<ellipse cx="310" cy="206" rx="285" ry="15" fill="#000" opacity=".5"/><ellipse cx="310" cy="190" rx="300" ry="38" fill="url(#cgw)"/>'
            '<path d="M38 160C38 142 50 134 76 130L150 118C176 84 218 64 272 62H356C410 64 452 88 480 118L548 128C570 132 580 144 580 160V176H38Z" fill="url(#cbd)"/>'
            '<path d="M168 116C190 88 224 72 272 70H352C398 72 432 92 458 116Z" fill="url(#cgl)" opacity=".92"/><path d="M300 70V116M168 116H458" stroke="#0b0d10" stroke-width="3" opacity=".7"/>'
            '<path d="M60 150H560" stroke="#fff" stroke-opacity=".18" stroke-width="2"/><path d="M540 138q18 2 30 10-14 2-30-2z" fill="#ffd9d9" opacity=".9"/><path d="M42 150q14-8 30-9v10z" fill="#fff" opacity=".85"/>'
            '<g fill="#0a0b0d"><circle cx="150" cy="176" r="44"/><circle cx="470" cy="176" r="44"/></g><g fill="#15181d"><circle cx="150" cy="176" r="31"/><circle cx="470" cy="176" r="31"/></g>'
            '<g stroke="#8d96a3" stroke-width="3.5" stroke-linecap="round"><path d="M150 152v48M126 164l48 24M126 188l48-24"/><path d="M470 152v48M446 164l48 24M446 188l48-24"/></g>'
            '<circle cx="150" cy="176" r="7" fill="#aeb6c1"/><circle cx="470" cy="176" r="7" fill="#aeb6c1"/></svg>') % (color, color, color)


def city(a, b, rng, w=640, h=360, fid="ct"):
    out = ['<svg class="photo" viewBox="0 0 %d %d" preserveAspectRatio="xMidYMid slice" role="img" aria-label="City skyline"><defs><linearGradient id="%s" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="%s"/><stop offset=".62" stop-color="%s"/><stop offset="1" stop-color="#fbd7a8"/></linearGradient></defs><rect width="%d" height="%d" fill="url(#%s)"/>' % (w, h, fid, "#12163a", a, w, h, fid)]
    out.append('<circle cx="%d" cy="%d" r="46" fill="#ffe8b8" opacity=".85"/><circle cx="%d" cy="%d" r="92" fill="#ffe8b8" opacity=".18"/>' % ((rng.randint(w // 4, w * 3 // 4), int(h * .52)) * 2))
    for layer, (col, op, hmin, hmax, ww) in enumerate((("#2a2450", .55, 60, 150, 38), ("#171635", .8, 80, 190, 46), ("#0b0b1d", 1, 90, 210, 54))):
        x = -rng.randint(0, 30)
        while x < w:
            bw = rng.randint(ww - 12, ww + 24)
            bh = rng.randint(hmin, hmax)
            out.append('<rect x="%d" y="%d" width="%d" height="%d" fill="%s" opacity="%s"/>' % (x, h - bh, bw, bh, col, op))
            if layer == 2:
                for wy in range(h - bh + 10, h - 8, 14):
                    for wx in range(x + 6, x + bw - 8, 11):
                        if rng.random() < .26:
                            out.append('<rect x="%d" y="%d" width="5" height="7" fill="%s" opacity=".%d"/>' % (wx, wy, rng.choice(["#ffd37a", "#ffe9b5", b]), rng.randint(5, 9)))
            x += bw + rng.randint(-4, 8)
    out.append("</svg>")
    return "".join(out)


def avatar(i, a, b, initial="A"):
    return ('<svg class="av" viewBox="0 0 64 64" aria-hidden="true"><defs><linearGradient id="av%d" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="%s"/><stop offset="1" stop-color="%s"/></linearGradient></defs>'
            '<circle cx="32" cy="32" r="32" fill="url(#av%d)"/><circle cx="32" cy="25" r="11" fill="#fff" opacity=".88"/><path d="M10 58c2-14 12-20 22-20s20 6 22 20a32 32 0 0 1-44 0z" fill="#fff" opacity=".88"/></svg>') % (i, a, b, i)


ICONS = {
    "bolt": "M13 2 4 14h7l-1 8 9-12h-7z", "shield": "M12 2 4 5v6c0 5 3.5 9 8 11 4.5-2 8-6 8-11V5zM9 12l2 2 4-4",
    "globe": "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18", "code": "M8 7 3 12l5 5M16 7l5 5-5 5M14 4l-4 16",
    "clock": "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7v5l3 2", "wrench": "M14.5 6.5a4 4 0 0 0-5.2 5.2L3 18l3 3 6.3-6.3a4 4 0 0 0 5.2-5.2L15 12l-3-3z",
    "battery": "M3 8h15v8H3zM18 11h3v2h-3zM6 10v4M9 10v4", "cpu": "M7 7h10v10H7zM10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4",
    "chart": "M4 20V4M4 20h16M8 16v-5M12 16V8M16 16v-8", "lock": "M6 11h12v9H6zM8 11V8a4 4 0 0 1 8 0v3", "cloud": "M7 18a4 4 0 0 1-.5-8A6 6 0 0 1 18 9a4.5 4.5 0 0 1 0 9z",
    "users": "M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM2 20c0-4 3-6 7-6s7 2 7 6M17 5a3 3 0 0 1 0 6M19 14c2 .6 3 2.4 3 6",
    "map": "M9 4 3 6v14l6-2 6 2 6-2V4l-6 2zM9 4v14M15 6v14", "box": "M3 7l9-4 9 4v10l-9 4-9-4zM3 7l9 4 9-4M12 11v10", "star": "M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z",
    "mail": "M3 6h18v12H3zM3 7l9 7 9-7", "phone": "M6 3h4l2 5-2.5 1.5a11 11 0 0 0 5 5L16 12l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 4 5a2 2 0 0 1 2-2z", "pin": "M12 21s7-6 7-11a7 7 0 1 0-14 0c0 5 7 11 7 11zM12 12a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z",
}


def sprite():
    return '<svg width="0" height="0" style="position:absolute" aria-hidden="true">%s</svg>' % "".join(
        '<symbol id="i-%s" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="%s"/></symbol>' % (k, v) for k, v in ICONS.items())


def logos(rng, color="currentColor"):
    names = ["Fintro", "Harborly", "Northwind", "Loomcast", "Klystra", "Voyager", "Redshift", "Basecamp"]
    shapes = ['<circle cx="9" cy="9" r="7"/>', '<path d="M2 15 9 2l7 13z"/>', '<rect x="2" y="2" width="14" height="14" rx="4"/>', '<path d="M9 1l8 8-8 8-8-8z"/>',
              '<path d="M2 9a7 7 0 0 1 14 0v7H2z"/>', '<path d="M2 3h14v4H2zM2 11h9v4H2z"/>', '<circle cx="9" cy="9" r="7" fill="none" stroke="currentColor" stroke-width="3"/>', '<path d="M3 15 9 3l6 12-6-4z"/>']
    return "".join('<span><svg viewBox="0 0 18 18" width="18" height="18" fill="currentColor">%s</svg>%s</span>' % (shapes[i % len(shapes)], n) for i, n in enumerate(names))


def thumb(kind, a, b, rng):
    """small illustrated tile for 'other ways' cards"""
    if kind == "web":
        return ('<svg viewBox="0 0 300 180" class="thumb" aria-hidden="true">%s<rect x="42" y="20" width="216" height="142" rx="10" fill="#fff" opacity=".96"/><rect x="42" y="20" width="216" height="22" rx="10" fill="#eef1f5"/>'
                '<circle cx="56" cy="31" r="3.5" fill="#ff6b6b"/><circle cx="68" cy="31" r="3.5" fill="#ffc94d"/><circle cx="80" cy="31" r="3.5" fill="#4cd964"/><g transform="translate(54 52) scale(.34)">%s</g>'
                '<rect x="178" y="56" width="68" height="9" rx="4.5" fill="%s"/><rect x="178" y="74" width="52" height="7" rx="3.5" fill="#dfe4ea"/><rect x="178" y="90" width="60" height="7" rx="3.5" fill="#dfe4ea"/></svg>') % (
            '<defs><linearGradient id="tw1" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="%s" stop-opacity=".25"/><stop offset="1" stop-color="%s" stop-opacity=".1"/></linearGradient></defs><rect width="300" height="180" fill="url(#tw1)"/>' % (a, b),
            wallpaper(a, b, rng, 400, 300, "tw2"), a)
    if kind == "cli":
        rows = "".join('<rect x="%d" y="%d" width="%d" height="7" rx="3.5" fill="%s" opacity=".9"/>' % (62 + (i % 2) * 12, 62 + i * 17, rng.randint(70, 150), (a, "#cfd8e3", b, "#cfd8e3")[i % 4]) for i in range(5))
        return ('<svg viewBox="0 0 300 180" class="thumb" aria-hidden="true"><defs><linearGradient id="tc1" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="%s" stop-opacity=".25"/><stop offset="1" stop-color="%s" stop-opacity=".1"/></linearGradient></defs>'
                '<rect width="300" height="180" fill="url(#tc1)"/><rect x="42" y="20" width="216" height="142" rx="10" fill="#10151c"/><circle cx="56" cy="34" r="3.5" fill="#ff6b6b"/><circle cx="68" cy="34" r="3.5" fill="#ffc94d"/><circle cx="80" cy="34" r="3.5" fill="#4cd964"/>'
                '<text x="56" y="62" font-family="ui-monospace,monospace" font-size="11" fill="%s">$</text>%s</svg>') % (a, b, b, rows.replace('y="62"', 'y="70"'))
    return ('<svg viewBox="0 0 300 180" class="thumb" aria-hidden="true"><defs><linearGradient id="tp1" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="%s" stop-opacity=".25"/><stop offset="1" stop-color="%s" stop-opacity=".1"/></linearGradient></defs>'
            '<rect width="300" height="180" fill="url(#tp1)"/><rect x="48" y="26" width="204" height="128" rx="10" fill="#fff"/><text x="62" y="62" font-family="ui-monospace,monospace" font-size="15" fill="%s">{ }</text>'
            '<rect x="96" y="52" width="70" height="8" rx="4" fill="%s" opacity=".75"/><rect x="62" y="80" width="150" height="7" rx="3.5" fill="#dfe4ea"/><rect x="62" y="96" width="120" height="7" rx="3.5" fill="#dfe4ea"/><rect x="62" y="112" width="140" height="7" rx="3.5" fill="#dfe4ea"/>'
            '<rect x="196" y="122" width="44" height="20" rx="10" fill="%s"/></svg>') % (a, b, a, b, a)
