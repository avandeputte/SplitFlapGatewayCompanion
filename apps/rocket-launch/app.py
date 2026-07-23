"""Next orbital rocket launch (Launch Library 2 / The Space Devs, keyless)."""


# =============================================================================
# SHARED — the launch data: the next upcoming launch and the pieces both
# surfaces print (rocket, mission, net time), so a wall and a panel always
# count down to the same lift-off.
# =============================================================================

def _next_launch():
    """The next upcoming launch record, or None when the API lists nothing.
    Raises on a network failure — the caller decides what offline looks like."""
    import requests
    data = requests.get('https://ll.thespacedevs.com/2.2.0/launch/upcoming/',
                        params={'limit': 1, 'mode': 'list'}, timeout=10).json()
    res = data.get('results') or []
    return res[0] if res else None


def _rocket_mission(r):
    """LL2 names a launch 'Rocket | Mission' — split it, with sane fallbacks."""
    name = str(r.get('name', ''))
    rocket, _, mission = name.partition('|')
    rocket = rocket.strip() or 'Rocket'
    mission = mission.strip() or rocket
    return rocket, mission


def _net_dt(r):
    """The launch's NET as an aware UTC datetime, or None when unparseable."""
    from datetime import datetime
    net = r.get('net')
    if not net:
        return None
    try:
        return datetime.fromisoformat(str(net).replace('Z', '+00:00'))
    except ValueError:
        return None


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================

def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    from datetime import datetime, timezone
    rows, cols = get_rows(), get_cols()

    def t(s, ctx="space"):
        return i18n.t(s, ctx) if i18n is not None else s

    def u(k):                                 # localized D/H/M duration suffix
        return i18n.unit(k) if i18n is not None else k

    try:
        r = _next_launch()
        if r is None:
            return [format_lines(t('Next launch'), t('None'), t('Scheduled'))]
        rocket, mission = _rocket_mission(r)
        cd, when = '', ''
        dt = _net_dt(r)
        if dt is not None:
            # The countdown says how long; on a tall wall there is room to say WHEN.
            # net is UTC, so it has to be moved into the user's zone or a launch late
            # tonight reads as tomorrow.
            try:
                import pytz
                local = dt.astimezone(pytz.timezone(settings.get('timezone', 'US/Eastern')))
            except Exception:
                local = dt
            if i18n is not None:
                when = f'{i18n.weekday(local, short=True)} {i18n.time(local, ampm_space=False)}'
            else:
                when = local.strftime('%a %I:%M%p').lstrip('0')
            secs = int((dt - datetime.now(timezone.utc)).total_seconds())
            if secs <= 0:
                cd = t('Imminent')
            else:
                d, h, m = secs // 86400, (secs % 86400) // 3600, (secs % 3600) // 60
                in_ = t('In', 'time')
                cd = (f'{in_} {d}{u("D")} {h}{u("H")}' if d
                      else (f'{in_} {h}{u("H")} {m}{u("M")}' if h
                            else f'{in_} {m}{u("M")}'))
        if rows == 1:
            return [f'{rocket} {cd}'[:cols].center(cols)]
        if rows == 2:
            return [format_lines(t('Next launch'), rocket), format_lines(mission, cd)]
        if rows == 3:
            return [format_lines(t('Next launch'), rocket, cd),
                    format_lines(t('Mission'), mission, cd)]

        # Four rows or more: it all fits at once. Splitting the rocket from its mission
        # across two pages was a three-row compromise — on a taller wall it just means
        # waiting for a page turn to read the other half of one sentence.
        lines = [t('Next launch'), rocket, mission]
        if rows >= 5 and when:
            lines.append(when)
        lines.append(cd)
        return [format_lines(*lines)]
    except Exception:
        return [format_lines(t('Next launch'), t('Offline'), '')]


# =============================================================================
# MATRIX PANEL — fetch_matrix() and its helpers, unique to the LED panel.
#
# A launch card: a little drawn rocket, the vehicle big, the mission beneath it
# and a T-minus line burning amber along the bottom. Black background.
# =============================================================================

_WHITE = (240, 240, 244)
_GRAY = (150, 150, 158)
_CYAN = (90, 200, 250)                      # the mission line
_AMBER = (255, 170, 50)                     # T-minus
_RED = (240, 80, 60)                        # imminent
_HULL = (205, 210, 220)                     # the rocket's hull
_FLAME = (255, 140, 40)


def _cv_fit(canvas, text, max_w, max_h):
    """The largest bundled font whose ``text`` fits within ``max_w`` x ``max_h`` (down to 5px)."""
    size = max(5, int(max_h) + 2)
    font = canvas.font(size)
    for _ in range(80):
        b = font.getbbox(text or '0')
        if size <= 5 or (font.getlength(text or '0') <= max_w and (b[3] - b[1]) <= max_h):
            return font
        size -= 1
        font = canvas.font(size)
    return font


def _cv_wrap(font, text, max_w, max_lines):
    """Greedy word-wrap of ``text`` to pixel width ``max_w``, at most ``max_lines`` lines."""
    words, lines, cur = str(text or '').split(), [], ''
    for w in words:
        cand = f'{cur} {w}'.strip()
        if not cur or font.getlength(cand) <= max_w:
            cur = cand
        else:
            lines.append(cur)
            cur = w
            if len(lines) >= max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    return lines[:max_lines] or ['']


def _cv_wrap_fit(canvas, text, max_w, max_h, max_lines):
    """Largest font at which ``text`` wraps into <= ``max_lines`` lines fitting the box.
    Returns (font, lines, line_height, gap)."""
    size = max(5, int(max_h))
    for _ in range(80):
        font = canvas.font(size)
        lines = _cv_wrap(font, text, max_w, max_lines)
        b = font.getbbox('Ag')
        lh = b[3] - b[1]
        gap = max(1, lh // 6)
        total = len(lines) * lh + (len(lines) - 1) * gap
        widest = max((font.getlength(ln) for ln in lines), default=0)
        if size <= 5 or (total <= max_h and widest <= max_w):
            return font, lines, lh, gap
        size -= 1
    font = canvas.font(5)
    lines = _cv_wrap(font, text, max_w, max_lines)
    b = font.getbbox('Ag')
    return font, lines, b[3] - b[1], 1


def _cv_message(canvas, ImageDraw, line1, line2):
    """A quiet two-line message (offline / nothing scheduled)."""
    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    f1 = _cv_fit(canvas, line1, W - 4, int(H * 0.32))
    b1 = f1.getbbox(line1)
    h1 = b1[3] - b1[1]
    f2 = _cv_fit(canvas, line2, W - 4, int(H * 0.22)) if line2 else None
    h2 = (f2.getbbox(line2)[3] - f2.getbbox(line2)[1]) if line2 else 0
    gap = 3 if line2 else 0
    y = (H - (h1 + gap + h2)) / 2.0
    draw.text(((W - f1.getlength(line1)) / 2.0, y - b1[1]), line1, font=f1, fill=_WHITE)
    if line2:
        y += h1 + gap
        draw.text(((W - f2.getlength(line2)) / 2.0, y - f2.getbbox(line2)[1]), line2, font=f2, fill=_GRAY)
    return img


def _cv_tminus(dt):
    """('T-2D 14H' / 'T-38M' / 'LIFT-OFF', color, seconds-to-go or None)."""
    from datetime import datetime, timezone
    if dt is None:
        return '', _GRAY, None
    secs = int((dt - datetime.now(timezone.utc)).total_seconds())
    if secs <= 0:
        return 'LIFT-OFF', _RED, secs
    d, h, m = secs // 86400, (secs % 86400) // 3600, (secs % 3600) // 60
    if d:
        return f'T-{d}D {h}H', _AMBER, secs
    if h:
        return f'T-{h}H {m:02d}M', _AMBER, secs
    return f'T-{m}M', _RED, secs


def _cv_rocket(draw, x, y, w, h):
    """A little rocket in flight, drawn with primitives: nose, hull, fins, flame."""
    cx = x + w // 2
    nose_h = max(3, int(h * 0.22))
    flame_h = max(3, int(h * 0.2))
    body_top = y + nose_h
    body_bot = y + h - flame_h - 1
    bw = max(3, int(w * 0.5))
    bx0, bx1 = cx - bw // 2, cx + bw // 2
    # nose cone, hull, porthole
    draw.polygon([(cx, y), (bx0, body_top), (bx1, body_top)], fill=_RED)
    draw.rectangle([bx0, body_top, bx1, body_bot], fill=_HULL)
    pr = max(1, bw // 4)
    pc_y = body_top + (body_bot - body_top) // 3
    draw.ellipse([cx - pr, pc_y - pr, cx + pr, pc_y + pr], fill=_CYAN)
    # fins
    fin_h = max(3, int(h * 0.18))
    draw.polygon([(bx0, body_bot), (bx0 - max(2, bw // 2), body_bot), (bx0, body_bot - fin_h)], fill=_RED)
    draw.polygon([(bx1, body_bot), (bx1 + max(2, bw // 2), body_bot), (bx1, body_bot - fin_h)], fill=_RED)
    # flame
    draw.polygon([(bx0 + 1, body_bot + 1), (bx1 - 1, body_bot + 1), (cx, y + h)], fill=_FLAME)
    draw.line([(cx, body_bot + 1), (cx, y + h - max(1, flame_h // 2))], fill=(255, 230, 120))


def fetch_matrix(settings, canvas, i18n=None):
    """Draw the next launch as a card — rocket icon, vehicle, mission, T-minus. The countdown
    reads in minutes, so a minutely redraw serves; it tightens as lift-off closes in."""
    from PIL import ImageDraw

    try:
        r = _next_launch()
    except Exception:
        canvas.frame(_cv_message(canvas, ImageDraw, 'NEXT LAUNCH', 'OFFLINE'))
        return 120.0
    if r is None:
        canvas.frame(_cv_message(canvas, ImageDraw, 'NEXT LAUNCH', 'NONE SCHEDULED'))
        return 300.0

    rocket, mission = _rocket_mission(r)
    rocket, mission = rocket.upper(), mission.upper()
    dt = _net_dt(r)
    tmin, tcol, secs = _cv_tminus(dt)

    when = ''
    if dt is not None:
        try:
            import pytz
            local = dt.astimezone(pytz.timezone(settings.get('timezone', 'US/Eastern')))
        except Exception:
            local = dt
        if i18n is not None:
            when = f'{i18n.weekday(local, short=True)} {i18n.time(local, ampm_space=False)}'.upper()
        else:
            when = local.strftime('%a %I:%M%p').lstrip('0').upper()

    W, H = canvas.width, canvas.height
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"

    if W >= 96 and H >= 48:
        # Header: label left, launch time right, amber divider. Tall enough for a ~10px face;
        # the ink top rides y=1 — row 0 stays as the 1px a fitted font's ink can overshoot
        # its bbox by, so the glyph tops never clip.
        head_h = max(12, int(H * 0.22))
        lbl = 'NEXT LAUNCH'
        ww = 0
        if when:
            wf = _cv_fit(canvas, when, int(W * 0.42), head_h - 3)
            wb = wf.getbbox(when)
            ww = wf.getlength(when)
            draw.text((W - 3 - ww, 1 - wb[1]), when, font=wf, fill=_WHITE)
        lf = _cv_fit(canvas, lbl, W - 6 - ww - 8, head_h - 3)
        lb = lf.getbbox(lbl)
        if (lb[3] - lb[1]) >= 6:
            draw.text((3, 1 - lb[1]), lbl, font=lf, fill=_GRAY)
        draw.line([(2, head_h + 1), (W - 3, head_h + 1)], fill=_AMBER)

        # A rocket riding the left edge, text beside it — its flame licks the last row.
        icon_w = max(12, int(H * 0.28)) if W >= 128 else 0
        tx = 3 + (icon_w + 4 if icon_w else 0)
        tw = W - 3 - tx
        if icon_w:
            _cv_rocket(draw, 3, head_h + 5, icon_w, H - head_h - 6)

        # Footer: T-minus, big, its ink sunk to the panel's bottom edge.
        foot_h = max(9, int(H * 0.22))
        fy = H - foot_h - 1
        if tmin:
            ff = _cv_fit(canvas, tmin, tw, foot_h)
            fb = ff.getbbox(tmin)
            draw.text((tx, H - 1 - (fb[3] - fb[1]) - fb[1]), tmin, font=ff, fill=tcol)

        # Body: the vehicle (up to 2 lines), the mission under it in cyan.
        top = head_h + 3
        mis_h = max(7, int(H * 0.15))
        body_h = fy - top - mis_h - 3
        nf, lines, lh, gap = _cv_wrap_fit(canvas, rocket, tw, body_h, 2)
        block = len(lines) * lh + (len(lines) - 1) * gap
        ny = top + max(0.0, (body_h - block) / 2.0)
        for ln in lines:
            draw.text((tx, ny - nf.getbbox(ln)[1]), ln, font=nf, fill=_WHITE)
            ny += lh + gap
        if mission and mission != rocket:
            # A readable size first, the full name second: keep the font at mis_h and
            # ellipsise the mission to the width rather than shrink it out of legibility.
            mf = _cv_fit(canvas, '0', tw, mis_h)
            mtext = mission
            while mtext and mf.getlength(mtext + '…') > tw and mtext != '…':
                mtext = mtext[:-1].rstrip()
            mtext = mission if mf.getlength(mission) <= tw else (mtext + '…' if mtext else '')
            if mtext:
                mb = mf.getbbox(mtext)
                draw.text((tx, fy - 2 - mis_h + (mis_h - (mb[3] - mb[1])) / 2.0 - mb[1]),
                          mtext, font=mf, fill=_CYAN)
    else:
        # Compact: vehicle over a big T-minus. The vehicle's ink rides row 1 (row 0 is
        # the bbox-overshoot slack) and the T-minus sinks to the panel's bottom edge.
        pad = 2
        t_h = max(8, int(H * 0.34))
        name_h = H - t_h - 4
        nf, lines, lh, gap = _cv_wrap_fit(canvas, rocket, W - 2 * pad, name_h, 2)
        ny = 1
        for ln in lines:
            draw.text(((W - nf.getlength(ln)) / 2.0, ny - nf.getbbox(ln)[1]), ln, font=nf, fill=_WHITE)
            ny += lh + gap
        if tmin:
            ff = _cv_fit(canvas, tmin, W - 2 * pad, t_h)
            fb = ff.getbbox(tmin)
            draw.text(((W - ff.getlength(tmin)) / 2.0, H - 1 - (fb[3] - fb[1]) - fb[1]),
                      tmin, font=ff, fill=tcol)

    canvas.frame(img)
    if secs is not None and secs <= 3600:
        return 30.0                    # inside the last hour the minutes matter
    return 120.0
