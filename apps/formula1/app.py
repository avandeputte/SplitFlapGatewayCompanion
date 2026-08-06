"""Formula 1 — next Grand Prix & championship leader (keyless: Jolpica / Ergast)."""


# =============================================================================
# SHARED — the F1 data: the next race and the championship standings, straight
# from Jolpica/Ergast. Both surfaces build on these, so a wall and a panel
# always name the same Grand Prix and the same leader.
# =============================================================================

def _next_race():
    """The next race of the current season, or None once the season is over.
    Raises on a network failure — the caller decides what offline looks like."""
    import requests
    nxt = requests.get('https://api.jolpi.ca/ergast/f1/current/next.json', timeout=10).json()
    races = nxt.get('MRData', {}).get('RaceTable', {}).get('Races', [])
    return races[0] if races else None


def _driver_standings():
    """The current driver standings (list of standing dicts, leader first).
    Raises on a network failure; [] when the API has nothing yet."""
    import requests
    st = requests.get('https://api.jolpi.ca/ergast/f1/current/driverStandings.json', timeout=10).json()
    lists = st.get('MRData', {}).get('StandingsTable', {}).get('StandingsLists', [])
    return lists[0].get('DriverStandings', []) if lists else []


def _race_start(r):
    """The race's start as an aware UTC datetime, or None when unparseable."""
    from datetime import datetime
    try:
        return datetime.fromisoformat(
            f"{r.get('date', '')}T{r.get('time', '00:00:00Z')}".replace('Z', '+00:00'))
    except ValueError:
        return None


# =============================================================================
# SPLIT-FLAP — fetch() and its helpers, unique to the character-grid flap wall.
# =============================================================================

def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    from datetime import datetime, timezone
    rows, cols = get_rows(), get_cols()

    def t(s, ctx="sports"):
        return i18n.t(s, ctx) if i18n is not None else s

    def u(k):                                 # localized D/H duration suffix
        return i18n.unit(k) if i18n is not None else k

    pages = []
    try:
        r = _next_race()
        if r:
            name = str(r.get('raceName', '')).replace('Grand Prix', 'GP')
            cd = ''
            dt = _race_start(r)
            if dt is not None:
                secs = int((dt - datetime.now(timezone.utc)).total_seconds())
                if secs > 0:
                    d, h = secs // 86400, (secs % 86400) // 3600
                    in_ = t('In', 'time')
                    cd = f'{in_} {d}{u("D")} {h}{u("H")}' if d else f'{in_} {h}{u("H")}'
                else:
                    cd = t('Race weekend')
            if rows == 1:
                pages.append(f'{name} {cd}'[:cols].center(cols))
            elif rows == 2:
                pages.append(format_lines(t('Next GP'), name))
                pages.append(format_lines(name, cd))
            else:
                pages.append(format_lines(t('Next Grand Prix'), name, cd))
        else:
            pages.append(format_lines('Formula 1', t('Season'), t('Over')))
    except Exception:
        return [format_lines('Formula 1', t('Offline'), '')]

    try:
        ds = _driver_standings()
        if ds:
            top = ds[0]
            nm = str(top.get('Driver', {}).get('familyName', ''))
            pts = top.get('points', '')
            if rows == 1:
                pages.append(f'{t("Leader")} {nm} {pts}'[:cols].center(cols))
            elif rows == 2:
                pages.append(format_lines(t('Championship'), f'{nm} {pts}{t("pts")}'))
            elif rows >= 4:
                # A tall wall gets the standings, not just the leader — one driver
                # per spare row, points right-aligned so they read as a column.
                lines = [t('Championship')]
                for d in ds[:rows - 1]:
                    dnm = str(d.get('Driver', {}).get('familyName', ''))[:cols - 5]
                    dpts = str(d.get('points', ''))
                    gap = max(1, cols - len(dnm) - len(dpts))
                    lines.append(f'{dnm}{" " * gap}{dpts}'[:cols])
                pages.append(format_lines(*lines))
            else:
                pages.append(format_lines(t('Leader'), nm, f'{pts} {t("points")}'))
    except Exception:
        pass
    return pages or [format_lines('Formula 1', t('No data'), '')]


# =============================================================================
# MATRIX PANEL — fetch_canvas() and its helpers, unique to the LED panel.
#
# A next-race card: an F1-red round chip, the Grand Prix name big, the date and
# a countdown to lights-out, with the championship leader along the bottom on a
# panel tall enough to carry it. Black background, no gradient.
# =============================================================================

_F1_RED = (225, 30, 20)                     # the series' livery red
_WHITE = (240, 240, 244)
_AMBER = (255, 180, 60)                     # the countdown
_GREEN = (80, 220, 120)                     # "race weekend" — it's on
_GRAY = (150, 150, 158)


def _cv_wrap(font, text, max_w, max_lines):
    """Greedy word-wrap of ``text`` to pixel width ``max_w``, at most ``max_lines`` lines.
    Stays local: canvas.wrap hyphen-splits overlong words, which lets canvas.wrap_fit
    settle on a larger font and cut the tail (race names would lose words)."""
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
    size = max(8, int(max_h))
    for _ in range(80):
        font = canvas.font(size)
        lines = _cv_wrap(font, text, max_w, max_lines)
        b = font.getbbox('Ag')
        lh = b[3] - b[1]
        gap = max(1, lh // 6)
        total = len(lines) * lh + (len(lines) - 1) * gap
        widest = max((font.getlength(ln) for ln in lines), default=0)
        if size <= 8 or (total <= max_h and widest <= max_w):
            return font, lines, lh, gap
        size -= 1
    font = canvas.font(8)
    lines = _cv_wrap(font, text, max_w, max_lines)
    b = font.getbbox('Ag')
    return font, lines, b[3] - b[1], 1


def _cv_countdown(dt):
    """('IN 4D 12H' amber / 'RACE WEEKEND' green / '' when unknown, color)."""
    from datetime import datetime, timezone
    if dt is None:
        return '', _GRAY
    secs = int((dt - datetime.now(timezone.utc)).total_seconds())
    if secs <= 0:
        return 'RACE WEEKEND', _GREEN
    d, h, m = secs // 86400, (secs % 86400) // 3600, (secs % 3600) // 60
    if d:
        return f'IN {d}D {h}H', _AMBER
    return (f'IN {h}H {m}M' if h else f'IN {m}M'), _AMBER


def _cv_when(dt, i18n):
    """'SUN JUL 26' — the race day, localized where an i18n is present."""
    if dt is None:
        return ''
    if i18n is not None:
        return f'{i18n.weekday(dt, short=True)} {i18n.date(dt, short=True)}'.upper()
    return dt.strftime('%a %b %d').upper()


def _cv_race_ops(canvas, name, rnd, when, cd, cd_col, leader, W, H):
    """The race card as on-device DRAW OPS — the gtext twin of the wide PIL branch below,
    for the LCD (manifest ``lcd_ops``): the red round chip, the Grand Prix name and the
    countdown drawn by the wall at its native resolution instead of a 256x160 pixel frame
    upscaled x5. Same header/body/footer bands, same livery red."""
    canvas.clear((0, 0, 0))
    m = max(3, int(W * 0.012))
    y0 = max(1, int(H * 0.006))

    # Header strip: the red round chip, "NEXT RACE", the race day on the right.
    chip = f'R{rnd}' if rnd else 'F1'
    head_h = max(11, int(H * 0.22))
    chip_h = head_h - 2 * y0
    cpad = max(2, int(chip_h * 0.09))
    csize = canvas.fit_gtext(chip, int(W * 0.2), max(8, int((chip_h - 2 * cpad) / 0.72)))
    hpad = max(3, int(csize * 0.40))
    cw = int(canvas.text_width(chip, csize)) + 2 * hpad
    canvas.roundrect(m, y0, cw, chip_h, max(2, int(chip_h * 0.16)), _F1_RED, fill=True)
    # Clamp the label ink INSIDE the chip (>= its top + cpad): the fitted size keeps the
    # ink shorter than the chip by construction, and the max() is the same guarantee the
    # PIL chip spells out — a tall label pushes down, never clips against the chip's edge.
    ly = max(y0 + cpad - 0.20 * csize, y0 + chip_h / 2 - 0.56 * csize)
    canvas.gtext(m + cw / 2, ly, chip, color=_WHITE, size=csize, align='center')
    ww = 0
    if when:
        wsize = canvas.fit_gtext(when, int(W * 0.45), max(8, int(head_h * 0.52)))
        ww = canvas.text_width(when, wsize)
        canvas.gtext(W - m, y0 + chip_h / 2 - 0.56 * wsize, when, color=_WHITE,
                     size=wsize, align='right')
    # "NEXT RACE" only where it fits at a readable size — the chip and date carry
    # the meaning on their own when it can't.
    lbl = 'NEXT RACE'
    gap = max(5, int(W * 0.012))
    avail = W - (m + cw + gap) - m - ww - gap
    lsize = canvas.fit_gtext(lbl, avail, max(8, int(head_h * 0.42)))
    if lsize >= max(9, int(H * 0.03)) and canvas.text_width(lbl, lsize) <= avail:
        canvas.gtext(m + cw + gap, y0 + chip_h / 2 - 0.56 * lsize, lbl, color=_GRAY,
                     size=lsize)
    rule_y = head_h + y0
    canvas.line(m, rule_y, W - 1 - m, rule_y, color=_F1_RED, t=max(1, int(H * 0.005)))

    # Footer: the countdown (left) and the championship leader in what's left, both
    # sitting their ink on the bottom row — no dark rows under the card.
    foot_cap = max(9, int(H * 0.145))
    cdw = 0
    if cd:
        fsize = canvas.fit_gtext(cd, int(W * 0.55), foot_cap)
        cdw = canvas.text_width(cd, fsize)
        canvas.gtext(m, H - int(fsize * 0.98), cd, color=cd_col, size=fsize)
    if leader:
        avail = W - 2 * m - cdw - max(8, int(W * 0.03))
        gsize = canvas.fit_gtext(leader, avail, foot_cap)
        if canvas.text_width(leader, gsize) > avail:
            leader = leader.rsplit(' ', 1)[0]        # drop the points, keep the name
            gsize = canvas.fit_gtext(leader, avail, foot_cap)
        if canvas.text_width(leader, gsize) <= avail:  # can't fit at the floor: drop it
            canvas.gtext(W - m, H - int(gsize * 0.98), leader, color=_GRAY, size=gsize,
                         align='right')

    # Middle: the Grand Prix name, as big as it wraps.
    top = rule_y + max(2, int(H * 0.02))
    body_h = (H - int(foot_cap * 0.85) - max(2, int(H * 0.02))) - top
    nsize, nlines = canvas.fit_wrap_gtext(name, W - 2 * m, body_h, 2)
    step = int(nsize * 1.18)
    blk = (len(nlines) - 1) * step + int(nsize * 0.72)
    ny = top + max(0.0, (body_h - blk) / 2.0)
    for ln in nlines:
        canvas.gtext(W // 2, ny - 0.20 * nsize, ln, color=_WHITE, size=nsize,
                     align='center')
        ny += step
    canvas.show()


def fetch_canvas(settings, canvas, i18n=None):
    """Draw the next Grand Prix as a red-chipped race card (name, date, countdown, leader).
    The countdown moves by the hour, so an hourly-ish redraw is plenty; tighter inside race week."""
    from PIL import ImageDraw

    try:
        r = _next_race()
    except Exception:
        canvas.frame(canvas.message('FORMULA 1', 'OFFLINE', color=_WHITE))
        return 120.0
    if not r:
        canvas.frame(canvas.message('FORMULA 1', 'SEASON OVER', color=_WHITE))
        return 300.0

    name = str(r.get('raceName', '')).replace('Grand Prix', 'GP').upper()
    rnd = str(r.get('round', '') or '')
    dt = _race_start(r)
    cd, cd_col = _cv_countdown(dt)
    when = _cv_when(dt, i18n)

    leader = ''
    try:
        ds = _driver_standings()
        if ds:
            drv = str(ds[0].get('Driver', {}).get('familyName', '')).upper()
            leader = f'P1 {drv} {ds[0].get("points", "")}'.strip()
    except Exception:
        pass

    W, H = canvas.width, canvas.height
    if getattr(canvas, 'can_gtext', False) and H >= 96:
        # The big-panel path: live ops at native resolution (crisp chip + TTF type).
        _cv_race_ops(canvas, name, rnd, when, cd, cd_col, leader, W, H)
        from datetime import datetime, timezone
        if dt is not None and 0 < (dt - datetime.now(timezone.utc)).total_seconds() < 86400:
            return 60.0
        return 300.0

    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"

    if W >= 96 and H >= 48:
        # Header strip: a red round chip, "NEXT RACE", the race day on the right.
        chip = f'R{rnd}' if rnd else 'F1'
        head_h = max(11, int(H * 0.22))
        cf = canvas.fit_font(chip, int(W * 0.2), head_h - 5)
        cb = cf.getbbox(chip)
        cw = int(cf.getlength(chip)) + 6
        draw.rounded_rectangle([2, 1, 2 + cw, head_h - 1], radius=2, fill=_F1_RED)
        # Clamp the label ink INSIDE the chip (>= its top + 2): a fitted font whose ink
        # overshoots must push down, never clip against the chip's own edge.
        cy = max(3 - cb[1], 1 + (head_h - 2 - (cb[3] - cb[1])) / 2.0 - cb[1])
        draw.text((2 + (cw - cf.getlength(chip)) / 2.0, cy), chip, font=cf, fill=_WHITE)
        ww = 0
        if when:
            wf = canvas.fit_font(when, int(W * 0.45), max(7, head_h - 6))
            wb = wf.getbbox(when)
            ww = wf.getlength(when)
            wy = max(1 - wb[1], 1 + (head_h - 2 - (wb[3] - wb[1])) / 2.0 - wb[1])
            draw.text((W - 3 - ww, wy), when, font=wf, fill=_WHITE)
        # "NEXT RACE" only where it fits at a readable size — the chip and date
        # carry the meaning on their own when it can't.
        lbl = 'NEXT RACE'
        lf = canvas.fit_font(lbl, W - cw - 14 - ww, max(7, head_h - 6))
        lb = lf.getbbox(lbl)
        if lf.getlength(lbl) <= W - cw - 14 - ww:
            draw.text((2 + cw + 5, 1 + (head_h - 2 - (lb[3] - lb[1])) / 2.0 - lb[1]), lbl, font=lf, fill=_GRAY)
        draw.line([(2, head_h + 1), (W - 3, head_h + 1)], fill=_F1_RED)

        # Footer: the countdown (left) and the championship leader in what's left,
        # both sitting their ink on the bottom row — no dark rows under the card.
        foot_h = max(9, int(H * 0.19))
        fy = H - foot_h - 1
        cdw = 0
        if cd:
            ff = canvas.fit_font(cd, int(W * 0.55), foot_h)
            fb = ff.getbbox(cd)
            cdw = ff.getlength(cd)
            draw.text((3, H - fb[3]), cd, font=ff, fill=cd_col)
        if leader:
            avail = W - 6 - cdw - 8
            gf = canvas.fit_font(leader, avail, foot_h)
            if gf.getlength(leader) > avail:
                leader = leader.rsplit(' ', 1)[0]        # drop the points, keep the name
                gf = canvas.fit_font(leader, avail, foot_h)
            gb = gf.getbbox(leader)
            if gf.getlength(leader) <= avail:            # can't fit at the 8px floor: drop it
                draw.text((W - 3 - gf.getlength(leader), H - gb[3]),
                          leader, font=gf, fill=_GRAY)

        # Middle: the Grand Prix name, as big as it wraps.
        top = head_h + 3
        body_h = fy - top - 1
        nf, lines, lh, gap = _cv_wrap_fit(canvas, name, W - 6, body_h, 2)
        block = len(lines) * lh + (len(lines) - 1) * gap
        ny = top + max(0.0, (body_h - block) / 2.0)
        for ln in lines:
            draw.text(((W - nf.getlength(ln)) / 2.0, ny - nf.getbbox(ln)[1]), ln, font=nf, fill=_WHITE)
            ny += lh + gap
    else:
        # Compact: a red "F1" tag + date strip, the name, the countdown.
        tag = 'F1'
        strip_h = max(8, int(H * 0.28))
        tf = canvas.fit_font(tag, 18, strip_h - 2)
        tb = tf.getbbox(tag)
        tw = int(tf.getlength(tag)) + 4
        draw.rectangle([1, 1, 1 + tw, strip_h - 1], fill=_F1_RED)
        draw.text((1 + (tw - tf.getlength(tag)) / 2.0,
                   1 + (strip_h - 2 - (tb[3] - tb[1])) / 2.0 - tb[1]), tag, font=tf, fill=_WHITE)
        if when:
            wf = canvas.fit_font(when, W - tw - 6, strip_h - 2)
            wb = wf.getbbox(when)
            if wf.getlength(when) > W - tw - 6:          # won't fit: date only, no weekday
                when = when.split(' ', 1)[-1]
                wf = canvas.fit_font(when, W - tw - 6, strip_h - 2)
                wb = wf.getbbox(when)
            if wf.getlength(when) <= W - tw - 6:         # can't fit at the 8px floor: drop it
                draw.text((W - 2 - wf.getlength(when),
                           1 + (strip_h - 2 - (wb[3] - wb[1])) / 2.0 - wb[1]), when, font=wf, fill=_GRAY)
        body_top = strip_h + 1
        cd_h = max(7, int(H * 0.24)) if cd else 0
        body_h = H - body_top - cd_h - 2
        nf, lines, lh, gap = _cv_wrap_fit(canvas, name, W - 4, body_h, 2)
        block = len(lines) * lh + (len(lines) - 1) * gap
        ny = body_top + max(0.0, (body_h - block) / 2.0)
        for ln in lines:
            draw.text(((W - nf.getlength(ln)) / 2.0, ny - nf.getbbox(ln)[1]), ln, font=nf, fill=_WHITE)
            ny += lh + gap
        if cd:
            ff = canvas.fit_font(cd, W - 4, cd_h)
            fb = ff.getbbox(cd)
            draw.text(((W - ff.getlength(cd)) / 2.0, H - 1 - cd_h + (cd_h - (fb[3] - fb[1])) / 2.0 - fb[1]),
                      cd, font=ff, fill=cd_col)

    canvas.frame(img)
    # Days out the countdown only moves hourly; inside the final day it moves by the minute.
    from datetime import datetime, timezone
    if dt is not None and 0 < (dt - datetime.now(timezone.utc)).total_seconds() < 86400:
        return 60.0
    return 300.0
