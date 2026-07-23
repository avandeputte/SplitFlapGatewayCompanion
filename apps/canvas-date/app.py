"""Date Card — the date as bold typography, at the panel's full resolution.

A canvas app (surface: canvas), a sibling of the Lumina clock. Where the flap
Date app can only spell the date in the blocky built-in font, this renders a
huge anti-aliased day-of-month numeral on the LEFT and stacks the weekday,
month and year in a clean size hierarchy on the RIGHT — all pushed as one frame
to the Matrix panel (PUT /api/canvas/frame).

The accent is a slim full-width bar along the bottom tracking the year's
progress (day-of-year / days-in-year). It — and the weekday line — take a gentle
Mon..Sun color, a cool blue on Monday warming to a coral on Sunday, so each day
reads at a glance. The backdrop is a subtle very-dark-blue → black gradient and
the big day picks up a whisper of the day's hue at its base. Curated, high
contrast, no pink. The bundled font and the panel-sized helpers (font / vgrad /
blank / frame) come from the injected ``canvas``.
"""

# Mon..Sun accent ramp — cool at the start of the week, warm at the weekend.
# High-contrast on a near-black panel; hand-picked so none of them is pink.
_WEEKDAY = [
    (90, 165, 255),   # Mon — blue
    (0, 200, 205),    # Tue — teal
    (60, 210, 130),   # Wed — green
    (150, 205, 70),   # Thu — lime
    (255, 185, 45),   # Fri — amber
    (255, 140, 45),   # Sat — orange
    (255, 95, 70),    # Sun — coral (not pink)
]


def _lerp(a, b, t):
    return tuple(int(round(a[k] + (b[k] - a[k]) * t)) for k in range(3))


def _scale(c, k):
    return tuple(max(0, min(255, int(c[i] * k))) for i in range(3))


def _fit(canvas, text, max_cap, max_w):
    """Largest bundled font whose ``text`` fits both a cap height and a width.
    Returns the font plus the text's ink metrics so it can be placed precisely."""
    max_cap, max_w = max(6.0, max_cap), max(6.0, max_w)
    n = max(1, len(text))
    est = min(max_cap / 0.66, max_w / (0.62 * n))   # start at/above the true fit
    size = max(6, int(est) + 8)
    font = canvas.font(size)
    for _ in range(300):
        l, t, r, b = font.getbbox(text)
        if ((b - t) <= max_cap and (r - l) <= max_w) or size <= 6:
            break
        size -= 1
        font = canvas.font(size)
    l, t, r, b = font.getbbox(text)
    return {"font": font, "text": text, "w": r - l, "h": b - t, "l": l, "t": t}


def _vfill(Image, W, H, top, bot, y0, y1):
    """A panel-sized image whose vertical gradient runs ``top``→``bot`` across the
    band [y0, y1] — shown through a glyph mask so the big numeral is filled by it."""
    col = Image.new("RGB", (1, H))
    px = col.load()
    span = max(1.0, y1 - y0)
    for yy in range(H):
        px[0, yy] = _lerp(top, bot, min(1.0, max(0.0, (yy - y0) / span)))
    return col.resize((W, H))


def fetch_matrix(settings, canvas):
    from datetime import datetime
    from PIL import Image, ImageDraw

    tzname = str(settings.get("timezone") or "").strip()
    try:
        if tzname:
            import pytz
            now = datetime.now(pytz.timezone(tzname))
        else:
            now = datetime.now()
    except Exception:
        now = datetime.now()

    W, H = canvas.width, canvas.height
    accent = _WEEKDAY[now.weekday()]

    # -- panel-adaptive geometry --------------------------------------------
    if W <= 72:                       # 64x32 — compact
        left_frac, pad_x, gap_inner = 0.48, 2, 3
    else:                             # 128x32 / 128x64 — room for wider type
        left_frac, pad_x, gap_inner = 0.52, 4, 6
    pad_top = 2 if H >= 48 else 1
    bar_h = 3 if H >= 48 else 2
    bar_gap = 2 if H >= 48 else 1
    gap_v = 2 if H >= 48 else 1

    content_top = pad_top
    content_bottom = H - (bar_h + bar_gap)
    content_h = max(8, content_bottom - content_top)

    # -- the big day-of-month numeral on the left ---------------------------
    day_str = str(now.day)
    day_cap = content_h * (0.94 if len(day_str) == 1 else 0.86)
    day = _fit(canvas, day_str, day_cap, W * left_frac)
    day_center_y = content_top + content_h / 2.0
    day_top = day_center_y - day["h"] / 2.0

    # -- the weekday / month / year stack on the right ----------------------
    large = W >= 192                         # a big panel gets a third, facts column
    col_x = pad_x + day["w"] + gap_inner
    if large:
        info_x = int(W * 0.70)
        col_w = max(10, info_x - col_x - gap_inner)
    else:
        col_w = max(10, W - col_x - pad_x)
    avail = max(6, content_h - 2 * gap_v)
    wk_cap, mo_cap, yr_cap = avail * 0.38, avail * 0.32, avail * 0.30

    def choose(full, abbr, cap):
        """Full name when it fits the column at its target size, else the abbrev."""
        f = _fit(canvas, full, cap, col_w)
        return full if f["h"] >= cap - 1 else abbr

    wk_text = choose(now.strftime("%A").upper(), now.strftime("%a").upper(), wk_cap)
    mo_text = choose(now.strftime("%B").upper(), now.strftime("%b").upper(), mo_cap)
    yr_text = str(now.year)

    def stack(scale=1.0):
        return [_fit(canvas, t, c * scale, col_w) for t, c in
                ((wk_text, wk_cap), (mo_text, mo_cap), (yr_text, yr_cap))]

    lines = stack()
    total = sum(ln["h"] for ln in lines) + 2 * gap_v
    if total > content_h:                                  # rare rounding overflow
        lines = stack(content_h / total * 0.98)
        total = sum(ln["h"] for ln in lines) + 2 * gap_v

    # -- compose: dark gradient, gradient-filled day, then the stack --------
    base = canvas.blank((0, 0, 0))          # solid black — no tinted card behind it

    m = Image.new("L", (W, H), 0)
    dm = ImageDraw.Draw(m)
    dm.fontmode = "1"                           # crisp 1-bit glyph mask — no AA edges
    dm.text((pad_x - day["l"], day_top - day["t"]), day_str,
            fill=255, font=day["font"], anchor="la")
    fill = _vfill(Image, W, H, (255, 255, 255), _lerp((255, 255, 255), accent, 0.16),
                  day_top, day_top + day["h"])
    base = Image.composite(fill, base, m)

    draw = ImageDraw.Draw(base)
    draw.fontmode = "1"                         # crisp 1-bit text — no anti-aliased fuzz
    colors = (accent, (232, 236, 244), (150, 166, 196))
    y = content_top + (content_h - total) / 2.0
    for ln, col in zip(lines, colors):
        draw.text((col_x - ln["l"], y - ln["t"]), ln["text"],
                  fill=col, font=ln["font"], anchor="la")
        y += ln["h"] + gap_v

    # -- a far-right facts column on a big panel, so the width isn't wasted --
    if large:
        yr2 = now.year
        leap2 = (yr2 % 4 == 0 and yr2 % 100 != 0) or (yr2 % 400 == 0)
        yday = now.timetuple().tm_yday
        facts = [f"WEEK {now.isocalendar()[1]}", f"DAY {yday}",
                 f"{(366 if leap2 else 365) - yday} LEFT"]
        info_w = max(10, W - info_x - pad_x)
        icap = content_h * 0.26
        ifs = [_fit(canvas, s, icap, info_w) for s in facts]
        itot = sum(f["h"] for f in ifs) + 2 * gap_v
        iy = content_top + (content_h - itot) / 2.0
        for f, col in zip(ifs, ((214, 224, 240), (192, 202, 224), (168, 180, 206))):
            draw.text((info_x - f["l"], iy - f["t"]), f["text"], fill=col,
                      font=f["font"], anchor="la")
            iy += f["h"] + gap_v

    # -- accent: the year's progress along the bottom -----------------------
    yr = now.year
    leap = (yr % 4 == 0 and yr % 100 != 0) or (yr % 400 == 0)
    frac = (now.timetuple().tm_yday - 1 +
            (now.hour * 3600 + now.minute * 60 + now.second) / 86400.0) / (366 if leap else 365)
    frac = min(1.0, max(0.0, frac))
    bar_y = H - bar_h
    fill_w = int(round(frac * W))
    draw.rectangle([0, bar_y, W - 1, H - 1], fill=_scale(accent, 0.18))
    if fill_w > 0:
        draw.rectangle([0, bar_y, fill_w - 1, H - 1], fill=accent)
    if 0 < fill_w < W:
        draw.rectangle([fill_w, bar_y, fill_w, H - 1], fill=(255, 255, 255))

    canvas.frame(base)
    # Nothing on this card changes until the day rolls: the numeral/weekday at local midnight,
    # and the year-progress bar drifts about a pixel a day. So sleep until the next midnight
    # rather than repainting an identical frame every 2s. Capped at an hour so a clock/DST step
    # self-corrects, and the redraw lands with the panel already showing the right frame.
    from datetime import timedelta
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1.0, min(3600.0, (midnight - now).total_seconds()))
