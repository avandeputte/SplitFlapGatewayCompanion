# =============================================================================
# SHARED — today, in words: timezone resolution and the localized weekday /
# month-day / year strings both surfaces print.
# =============================================================================

def _tz(settings):
    import pytz
    try:
        return pytz.timezone(settings.get('timezone', 'US/Eastern'))
    except pytz.UnknownTimeZoneError:
        return pytz.timezone('US/Eastern')


def _parts(settings, i18n):
    """(now, time_str, weekday, month_day) — the strings every surface shows.
    When the companion injects i18n, honor the global Language: localized weekday,
    locale-ordered date (9 JUILLET, not JUILLET 9), and 24h time outside English."""
    from datetime import datetime
    now = datetime.now(_tz(settings))
    if i18n is not None:
        return now, i18n.time(now), i18n.weekday(now), i18n.date(now)
    return (now, now.strftime('%I:%M %p').lstrip('0'), now.strftime('%A'),
            f"{now.strftime('%B')} {now.day}")


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================

def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    now, time_str, weekday, month_day = _parts(settings, i18n)
    rows = get_rows()
    if rows == 2:
        return [format_lines(month_day, weekday)]
    if rows >= 4:
        return [format_lines(time_str, weekday, month_day, str(now.year))]
    return [format_lines(time_str, month_day, weekday)]


# =============================================================================
# MATRIX PANEL — fetch_canvas() and its helpers, unique to the LED panel.
#
# The Date Card: a huge day-of-month numeral on the left (gradient-filled through
# a glyph mask), the weekday / month / year stacked in a size hierarchy beside it
# (weekday in the day's own Mon..Sun accent color), a facts column (ISO week, day
# of year, days left) on a big panel, and a year-progress bar along the bottom in
# the same accent. Solid black background; sleeps until local midnight.
# =============================================================================

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


def _cv_fit_ink(canvas, text, max_cap, max_w):
    """Largest bundled font whose ``text`` fits both a cap height and a width.
    Returns the font plus the text's ink metrics so it can be placed precisely."""
    max_cap, max_w = max(8.0, max_cap), max(8.0, max_w)
    n = max(1, len(text))
    est = min(max_cap / 0.66, max_w / (0.62 * n))   # start at/above the true fit
    size = max(8, int(est) + 8)
    font = canvas.font(size)
    for _ in range(300):
        l, t, r, b = font.getbbox(text)
        if ((b - t) <= max_cap and (r - l) <= max_w) or size <= 8:
            break
        size -= 1
        font = canvas.font(size)
    l, t, r, b = font.getbbox(text)
    return {"font": font, "text": text, "w": r - l, "h": b - t, "l": l, "t": t}


def _cv_vfill(canvas, Image, W, H, top, bot, y0, y1):
    """A panel-sized image whose vertical gradient runs ``top``→``bot`` across the
    band [y0, y1] — shown through a glyph mask so the big numeral is filled by it."""
    col = Image.new("RGB", (1, H))
    px = col.load()
    span = max(1.0, y1 - y0)
    for yy in range(H):
        px[0, yy] = canvas.mix(top, bot, min(1.0, max(0.0, (yy - y0) / span)))
    return col.resize((W, H))


def fetch_canvas(settings, canvas, i18n=None):
    from datetime import datetime
    from PIL import Image, ImageDraw

    now = datetime.now(_tz(settings))    # the SAME clock the flap view reads

    W, H = canvas.width, canvas.height
    accent = _WEEKDAY[now.weekday()]

    if getattr(canvas, "can_gtext", False) and H >= 96:
        # LCD with scalable on-device text: draw the Date Card as gtext + geometry ops
        # instead of a pixel frame — crisp at native resolution, a handful of ops a frame
        # over the draw stream. Same composition as the PIL tall layout (the big day
        # numeral, the weekday/month/year stack, a facts column on a wide panel, the
        # year-progress bar), sized by the SAME _cv_fit_ink so the placement matches; the
        # numeral is a solid near-white (the PIL white->accent gradient fill isn't an op).
        from datetime import timedelta

        if W <= 72:
            left_frac, pad_x, gap_inner = 0.48, 2, 3
        else:
            left_frac, pad_x, gap_inner = 0.52, 4, 6
        pad_top = 2 if H >= 48 else 1
        bar_h = max(3, int(H * 0.03)) if H >= 48 else 2
        bar_gap = 2 if H >= 48 else 1
        gap_v = 2 if H >= 48 else 1

        content_top = pad_top
        content_bottom = H - (bar_h + bar_gap)
        content_h = max(8, content_bottom - content_top)

        day_str = str(now.day)
        day_cap = content_h * (0.94 if len(day_str) == 1 else 0.86)
        day = _cv_fit_ink(canvas, day_str, day_cap, W * left_frac)
        day_center_y = content_top + content_h / 2.0
        day_top = day_center_y - day["h"] / 2.0

        large = W >= 192
        col_x = pad_x + day["w"] + gap_inner
        if large:
            info_x = int(W * 0.70)
            col_w = max(10, info_x - col_x - gap_inner)
        else:
            col_w = max(10, W - col_x - pad_x)
        avail = max(6, content_h - 2 * gap_v)
        wk_cap, mo_cap, yr_cap = avail * 0.38, avail * 0.32, avail * 0.30

        def choose(full, abbr, cap):
            f = _cv_fit_ink(canvas, full, cap, col_w)
            return full if f["h"] >= cap - 1 else abbr

        if i18n is not None:
            wk_full, wk_abbr = str(i18n.weekday(now)).upper(), str(i18n.weekday(now, short=True)).upper()
            mo_full, mo_abbr = str(i18n.month(now)).upper(), str(i18n.month(now, short=True)).upper()
        else:
            wk_full, wk_abbr = now.strftime("%A").upper(), now.strftime("%a").upper()
            mo_full, mo_abbr = now.strftime("%B").upper(), now.strftime("%b").upper()
        wk_text = choose(wk_full, wk_abbr, wk_cap)
        mo_text = choose(mo_full, mo_abbr, mo_cap)
        yr_text = str(now.year)

        def stack(scale=1.0):
            return [_cv_fit_ink(canvas, txt, c * scale, col_w) for txt, c in
                    ((wk_text, wk_cap), (mo_text, mo_cap), (yr_text, yr_cap))]

        lines = stack()
        total = sum(ln["h"] for ln in lines) + 2 * gap_v
        if total > content_h:
            lines = stack(content_h / total * 0.98)
            total = sum(ln["h"] for ln in lines) + 2 * gap_v

        canvas.clear((0, 0, 0))
        canvas.gtext(int(pad_x - day["l"]), int(day_top - day["t"]), day_str,
                     color=canvas.mix((255, 255, 255), accent, 0.10), size=day["font"].size)

        colors = (accent, (232, 236, 244), (150, 166, 196))
        y = content_top + (content_h - total) / 2.0
        for ln, col in zip(lines, colors):
            canvas.gtext(int(col_x - ln["l"]), int(y - ln["t"]), ln["text"],
                         color=col, size=ln["font"].size)
            y += ln["h"] + gap_v

        if large:
            yr2 = now.year
            leap2 = (yr2 % 4 == 0 and yr2 % 100 != 0) or (yr2 % 400 == 0)
            yday = now.timetuple().tm_yday
            facts = [f"WEEK {now.isocalendar()[1]}", f"DAY {yday}",
                     f"{(366 if leap2 else 365) - yday} LEFT"]
            info_w = max(10, W - info_x - pad_x)
            icap = content_h * 0.26
            ifs = [_cv_fit_ink(canvas, s, icap, info_w) for s in facts]
            itot = sum(f["h"] for f in ifs) + 2 * gap_v
            iy = content_top + (content_h - itot) / 2.0
            for f, col in zip(ifs, ((214, 224, 240), (192, 202, 224), (168, 180, 206))):
                canvas.gtext(int(info_x - f["l"]), int(iy - f["t"]), f["text"],
                             color=col, size=f["font"].size)
                iy += f["h"] + gap_v

        yr = now.year
        leap = (yr % 4 == 0 and yr % 100 != 0) or (yr % 400 == 0)
        frac = (now.timetuple().tm_yday - 1 +
                (now.hour * 3600 + now.minute * 60 + now.second) / 86400.0) / (366 if leap else 365)
        frac = min(1.0, max(0.0, frac))
        bar_y = H - bar_h
        fill_w = int(round(frac * W))
        canvas.rect(0, bar_y, W, bar_h, color=canvas.dim(accent, 0.18), fill=True)
        if fill_w > 0:
            canvas.rect(0, bar_y, fill_w, bar_h, color=accent, fill=True)
        if 0 < fill_w < W:
            canvas.rect(fill_w, bar_y, 1, bar_h, color=(255, 255, 255), fill=True)

        canvas.show()
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return max(1.0, min(3600.0, (midnight - now).total_seconds()))

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
    day = _cv_fit_ink(canvas, day_str, day_cap, W * left_frac)
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
        f = _cv_fit_ink(canvas, full, cap, col_w)
        return full if f["h"] >= cap - 1 else abbr

    if i18n is not None:                 # localized names, like the flap view
        wk_full, wk_abbr = str(i18n.weekday(now)).upper(), str(i18n.weekday(now, short=True)).upper()
        mo_full, mo_abbr = str(i18n.month(now)).upper(), str(i18n.month(now, short=True)).upper()
    else:
        wk_full, wk_abbr = now.strftime("%A").upper(), now.strftime("%a").upper()
        mo_full, mo_abbr = now.strftime("%B").upper(), now.strftime("%b").upper()
    wk_text = choose(wk_full, wk_abbr, wk_cap)
    mo_text = choose(mo_full, mo_abbr, mo_cap)
    yr_text = str(now.year)

    def stack(scale=1.0):
        return [_cv_fit_ink(canvas, t, c * scale, col_w) for t, c in
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
    fill = _cv_vfill(canvas, Image, W, H, (255, 255, 255),
                     canvas.mix((255, 255, 255), accent, 0.16),
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
        ifs = [_cv_fit_ink(canvas, s, icap, info_w) for s in facts]
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
    draw.rectangle([0, bar_y, W - 1, H - 1], fill=canvas.dim(accent, 0.18))
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
