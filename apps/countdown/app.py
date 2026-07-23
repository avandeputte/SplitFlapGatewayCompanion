def fetch(settings, format_lines, get_rows, get_cols, canvas=None, i18n=None, caps=None):
    # A Matrix panel gets the rich full-width colour bars; a flap wall gets the text countdown.
    # Both rotate through the SAME active countdown slots (by wall-clock), so the two views agree.
    if canvas is not None:
        return _render_canvas(canvas, settings, caps)

    from datetime import datetime
    import pytz

    # Seconds are opt-in, and only where the wall says sub-second updates are
    # honest (caps.instant — its own motion statement): a mechanical module takes
    # seconds per flip, so a ticking seconds field would keep the wall permanently
    # mid-clatter. getattr, so the app still runs on stock splitflap-os.
    show_secs = (str(settings.get('show_seconds', '')).strip().lower()
                 in {'1', 'true', 'yes', 'on'}
                 and bool(getattr(caps, 'instant', False)))

    def t(s):
        return i18n.t(s, "time") if i18n is not None else s

    def u(k):                       # localized D/H/M/S suffix (French J for jour, etc.)
        return i18n.unit(k) if i18n is not None else k

    def is_enabled(value, default=False):
        if value is None:
            return default
        return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}

    def clean_event(value, fallback='Countdown'):
        text = str(value or '').strip()
        # caps.can_show is the wall's own answer to "can you show this?" — the
        # old version reached into the host's __main__ for FLAP_CHARS, which was
        # splitflap-os internals, not a contract. Without caps (stock
        # splitflap-os) nothing is filtered: the renderer degrades what it must.
        can = getattr(caps, 'can_show', None)
        if callable(can):
            text = ''.join(ch if can(ch) else ' ' for ch in text)
        return text.strip() or fallback

    def clean_target(value):
        return str(value or '').strip()

    def build_compact_countdown(total_seconds, cols):
        days, rem = divmod(total_seconds, 86400)
        hrs, rem = divmod(rem, 3600)
        mins, secs = divmod(rem, 60)

        # Keep the most useful leading units that still fit on the sign.
        # Only the seconds field is padded (with a space): it ticks every second
        # at the end of the line, and a 10S -> 9S rollover used to shorten the
        # line and shift everything left of it by a flap.
        day_u = u('D')
        # Past a year out, years lead: "8Y 267D 14H" reads where "3187D" only
        # counts. Only when years AND days actually fit together — on a very
        # narrow sign a bare "8Y" says less than the day total, so it keeps that.
        years, remdays = divmod(days, 365)
        lead = [f'{years}{u("Y")}', f'{remdays}{day_u}']
        if years > 0 and len(' '.join(lead)) <= cols:
            sections = lead + [f'{hrs}{u("H")}', f'{mins}{u("M")}']
        else:
            sections = [
                f'{days}{day_u}',
                f'{hrs}{u("H")}',
                f'{mins}{u("M")}',
            ]
        if show_secs:
            sections.append(f'{secs:>2}{u("S")}')

        text = ''
        for section in sections:
            candidate = section if not text else f'{text} {section}'
            if len(candidate) <= cols:
                text = candidate
            else:
                break

        if text:
            return text
        # At the narrowest widths, preserve the day suffix so the value still has context.
        if cols <= 1:
            return day_u[:cols]
        return f"{str(days)[:cols - len(day_u)]}{day_u}"

    # One colour per unit — cool to urgent, top to bottom. The empty bar cell is
    # the black square: it lands as a blank flap but keeps every line the same
    # width, so a bar growing or shrinking never re-centres the row.
    BAR_TILES = {'Y': '\U0001f7ea', 'D': '\U0001f7e6', 'H': '\U0001f7e9',
                 'M': '\U0001f7e8', 'S': '\U0001f7e5'}
    BAR_EMPTY = '⬛'

    def build_unit_rows(total_seconds, cols, max_rows):
        """A tall wall gets one row per unit: the value, then a colour bar of how
        much of that unit's own cycle remains — years of a decade, days of the
        year, hours of the day, minutes of the hour, seconds of the minute.
        Values sit right-aligned in a fixed field so a tick only touches the
        flaps that changed.

        Past a year out, a years row leads and the day row becomes days-within-
        the-year — which is also what keeps every value inside the 3-character
        column (this used to clamp at 999D and lie for anything further out).
        Rows are trimmed least-significant-first to what the wall has: on a
        5-row wall an 8-year countdown shows Y/D/H/M and the ticking seconds
        yield — seconds are for launch day, not a retirement eight years away."""
        days, rem = divmod(total_seconds, 86400)
        hrs, rem = divmod(rem, 3600)
        mins, secs = divmod(rem, 60)
        years, remdays = divmod(days, 365)
        units = []
        if years > 0:
            units.append(('Y', years, years / 10.0))
            units.append(('D', remdays, remdays / 365.0))
        else:
            units.append(('D', days, days / 365.0))
        units += [('H', hrs, hrs / 24.0),
                  ('M', mins, mins / 60.0)]
        if show_secs:
            units.append(('S', secs, secs / 60.0))
        units = units[:max_rows]
        labels = {key: u(key) for key, _, _ in units}
        label_w = max(len(v) for v in labels.values())
        lines = []
        for key, val, frac in units:
            field = f'{val:>3}{labels[key]:<{label_w}}'
            bar_len = cols - len(field) - 1
            if bar_len < 3:                      # no room for a meaningful bar
                lines.append(field.strip())
                continue
            filled = min(bar_len, round(min(frac, 1.0) * bar_len))
            lines.append(field + ' ' + BAR_TILES[key] * filled + BAR_EMPTY * (bar_len - filled))
        return lines

    def _pick(cols, *words):
        for w in words:
            if w and cols >= len(w):
                return w
        return (words[-1] or '')[:cols]

    def build_arrived_text(cols):
        return _pick(cols, t('Arrived') + '!', t('Here') + '!', t('Now') + '!', 'Go')

    def build_celebration_text(cols):
        return _pick(cols, t('Celebrate') + '!', t('Party') + '!', '')

    def build_remaining_text(cols):
        return _pick(cols, t('Remaining'), t('Left'), '')

    def build_slot_pages(event, target, now, rows, cols):
        diff = target - now
        if diff.total_seconds() <= 0:
            arrived_text = build_arrived_text(cols)
            if rows == 1:
                return [format_lines(event[:cols]), format_lines(arrived_text)]
            if rows == 2:
                return [format_lines(event, arrived_text)]

            celebration_text = build_celebration_text(cols)
            return [format_lines(event, arrived_text, celebration_text)]

        total_seconds = max(0, int(diff.total_seconds()))
        countdown_text = build_compact_countdown(total_seconds, cols)

        if rows >= 5:
            # Room for the full instrument panel: the event, then a row per unit.
            return [format_lines(event, *build_unit_rows(total_seconds, cols, rows - 1))]
        if rows == 1:
            return [format_lines(event[:cols]), format_lines(countdown_text[:cols])]
        if rows == 2:
            return [format_lines(event, countdown_text)]
        return [format_lines(event, countdown_text, build_remaining_text(cols))]

    def parse_target(target_str, tz, now, *, allow_default=False):
        if not target_str:
            if not allow_default:
                return None
            # Slot 1 keeps legacy behavior by defaulting to the next New Year.
            return now.replace(
                year=now.year + 1,
                month=1,
                day=1,
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )

        try:
            target = datetime.fromisoformat(target_str)
        except (TypeError, ValueError):
            return None

        if target.tzinfo is None:
            target = tz.localize(target)
        return target

    try:
        tz = pytz.timezone(settings.get('timezone') or 'UTC')
    except Exception:
        tz = pytz.utc

    now = datetime.now(tz)
    rows = get_rows()
    cols = get_cols()

    slots = [
        {
            'enabled': is_enabled(settings.get('countdown_enabled', 'on'), default=True),
            'event': clean_event(
                settings.get('countdown_event', 'New Year'),
                fallback='New Year',
            ),
            'target': clean_target(settings.get('countdown_target', '')),
            'allow_default_target': True,
        }
    ]

    for index in range(2, 6):
        slots.append(
            {
                'enabled': is_enabled(settings.get(f'countdown_{index}_enabled', 'off')),
                'event': clean_event(settings.get(f'countdown_{index}_event', '')),
                'target': clean_target(settings.get(f'countdown_{index}_target', '')),
                'allow_default_target': False,
            }
        )

    # If every slot is toggled off, still show slot 1 rather than a blank app.
    if not any(slot['enabled'] for slot in slots):
        slots[0]['enabled'] = True

    # One page-group per active countdown. We show ONE group per fetch and rotate
    # which by wall-clock time — not by returning every countdown as its own page.
    # That is what lets the seconds tick: the app is re-fetched every second (like
    # the clock app), so the shown countdown re-renders each second, while the
    # switch to the NEXT countdown happens only every `transition_seconds`. Returning
    # them all as pages instead coupled the rotation to the 1-second page dwell —
    # which is why they used to flip past once a second.
    groups = []
    for slot in slots:
        if not slot['enabled']:
            continue
        if not slot['event'] and not slot['target']:
            continue
        target = parse_target(
            slot['target'],
            tz,
            now,
            allow_default=slot['allow_default_target'],
        )
        if target is None:
            continue
        event = slot['event'] or 'Countdown'
        groups.append(build_slot_pages(event, target, now, rows, cols))

    if not groups:
        if rows == 2:
            return [format_lines('Countdown', 'Check config')]
        return [format_lines('Countdown', 'Check config', '')]

    if len(groups) == 1:
        return groups[0]

    try:
        span = max(2, int(float(settings.get('transition_seconds', 6) or 6)))
    except (ValueError, TypeError):
        span = 6
    return groups[_rotation_index(now.timestamp(), span, len(groups))]


def _rotation_index(now_ts, span, count):
    """Which countdown shows now: epoch // span gives stable, aligned blocks, so
    each fetch (once a second) lands in one block and the shown countdown holds
    for `span` seconds before advancing. Pure and module-level so it is testable
    without freezing the clock."""
    if count <= 0:
        return 0
    return int(now_ts // max(2, int(span))) % count


# ---------------------------------------------------------------------------
# Canvas view — the countdown as full-width draining colour bars on a Matrix
# panel (the pixel-native cousin of the flap bars above). It rotates through the
# SAME active slots as the flap view, by the same wall-clock rule, so a wall and
# a panel showing this app agree on which countdown is up. Ported from the former
# standalone "Countdown Bars" (canvas-countdown) app, now folded in here.
# ---------------------------------------------------------------------------

# One colour per unit, cool (far off) to urgent (imminent). No pink anywhere.
_UNITS = {
    'Y': ((150, 70, 230), 'YEARS', 'YEAR'),
    'D': ((60, 130, 245), 'DAYS', 'DAY'),
    'H': ((32, 200, 150), 'HOURS', 'HOUR'),
    'M': ((255, 175, 45), 'MIN', 'MIN'),
    'S': ((240, 68, 55), 'SEC', 'SEC'),
}


def _is_on(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def _active_targets(settings, caps, tz, now):
    """The enabled, valid countdown slots as ``[(EVENT, target_datetime), ...]`` — the same
    enumeration the flap path does (slot 1 defaults to next New Year; a wall with no slots on still
    shows slot 1), so both views rotate over the same set."""
    from datetime import datetime

    def clean_event(value, fallback):
        text = str(value or '').strip()
        can = getattr(caps, 'can_show', None)
        if callable(can):
            text = ''.join(ch if can(ch) else ' ' for ch in text)
        return text.strip() or fallback

    def parse_target(target_str, allow_default):
        if not target_str:
            if not allow_default:
                return None
            return now.replace(year=now.year + 1, month=1, day=1,
                               hour=0, minute=0, second=0, microsecond=0)
        try:
            target = datetime.fromisoformat(target_str)
        except (TypeError, ValueError):
            return None
        return tz.localize(target) if target.tzinfo is None else target

    slots = [{
        'enabled': _is_on(settings.get('countdown_enabled', 'on'), default=True),
        'event': clean_event(settings.get('countdown_event', 'New Year'), 'New Year'),
        'target': str(settings.get('countdown_target', '') or '').strip(),
        'allow_default': True,
    }]
    for i in range(2, 6):
        slots.append({
            'enabled': _is_on(settings.get(f'countdown_{i}_enabled', 'off')),
            'event': clean_event(settings.get(f'countdown_{i}_event', ''), 'Countdown'),
            'target': str(settings.get(f'countdown_{i}_target', '') or '').strip(),
            'allow_default': False,
        })
    if not any(s['enabled'] for s in slots):
        slots[0]['enabled'] = True

    targets = []
    for s in slots:
        if not s['enabled'] or (not s['target'] and not s['allow_default']):
            continue
        target = parse_target(s['target'], s['allow_default'])
        if target is not None:
            targets.append((s['event'], target))
    return targets


def _valstr(key, value):
    return f'{value:02d}' if key in ('H', 'M', 'S') else str(value)


def _bar_font(canvas, avail_h, sample='80'):
    """Largest bundled font whose ``sample`` ink fits ``avail_h`` px tall; returns
    (font, ink_top, ink_height) so callers can centre by ink."""
    size = max(5, int(avail_h * 1.5))
    font = canvas.font(size)
    for _ in range(64):
        _, tp, _, bt = font.getbbox(sample or '80')
        if (bt - tp) <= avail_h or size <= 5:
            return font, tp, bt - tp
        size -= 1
        font = canvas.font(size)
    _, tp, _, bt = font.getbbox(sample or '80')
    return font, tp, bt - tp


def _fit_width(canvas, text, max_w, start):
    size = max(5, int(start))
    font = canvas.font(size)
    while size > 5 and font.getlength(text) > max_w:
        size -= 1
        font = canvas.font(size)
    return font


def _label(key, value, font, max_w):
    _, plural, singular = _UNITS[key]
    vs = _valstr(key, value)
    word = singular if value == 1 else plural
    cand = f'{vs} {word}'
    if font.getlength(cand) <= max_w:
        return cand
    short = f'{vs} {key}'
    if font.getlength(short) <= max_w:
        return short
    return vs


def _truncate(font, text, max_w):
    while text and font.getlength(text) > max_w:
        text = text[:-1]
    return text


def _shadow_text(draw, x, y, text, font, fill=(255, 255, 255)):
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, -1), (-1, 1), (1, 1)):
        draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0), anchor='la')
    draw.text((x, y), text, font=font, fill=fill, anchor='la')


def _render_bars(canvas, ImageDraw, keys, val, frac, event, header_h):
    W, H = canvas.width, canvas.height
    pad = 2
    img = canvas.blank((0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"

    if header_h > 0 and event:
        hf, htop, hh = _bar_font(canvas, max(5, int(header_h * 0.5)), sample=event)
        etext = _truncate(hf, event, W - 4)
        ex = (W - hf.getlength(etext)) / 2.0
        ey = max(1.0, (header_h - 1 - hh) / 2.0 - htop)
        _shadow_text(draw, ex, ey, etext, hf, fill=(238, 238, 244))
        draw.rectangle([0, header_h - 1, W - 1, header_h - 1], fill=_UNITS[keys[0]][0])

    n = len(keys)
    top, area = header_h, H - header_h
    edges = [top + round(i * area / n) for i in range(n + 1)]
    min_bh = min(edges[i + 1] - edges[i] for i in range(n))
    font, ink_top, ink_h = _bar_font(canvas, max(5, min_bh - 3))

    for i, key in enumerate(keys):
        color = _UNITS[key][0]
        y0, y1 = edges[i], edges[i + 1]
        bh = y1 - y0
        fw = int(round(min(1.0, max(0.0, frac[key])) * W))
        if fw > 0:
            draw.rectangle([0, y0, fw - 1, y1 - 1], fill=color)
        vtext = _label(key, val[key], font, W - 2 * pad)
        ty = y0 + (bh - ink_h) / 2.0 - ink_top
        _shadow_text(draw, pad, ty, vtext, font)
    return img


def _render_arrived(canvas, Image, ImageDraw, event, frame):
    W, H = canvas.width, canvas.height
    base = canvas.vgrad((255, 196, 70), (28, 168, 92)).convert('RGBA')

    overlay = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    cx = int((frame * 4) % (W + 30)) - 15
    for dx in range(-14, 15):
        a = int(70 * max(0.0, 1 - abs(dx) / 14.0))
        if a:
            od.line([(cx + dx, 0), (cx + dx, H)], fill=(255, 255, 255, a))
    img = Image.alpha_composite(base, overlay).convert('RGB')
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"

    hero = 'ARRIVED!'
    hf = _fit_width(canvas, hero, W - 4, int(H * 0.52))
    hb = hf.getbbox(hero)
    hh = hb[3] - hb[1]
    eh, ef = 0, None
    if event:
        ef = _fit_width(canvas, event, W - 4, int(H * 0.30))
        eb = ef.getbbox(event)
        eh = eb[3] - eb[1]
    gap = 2 if event else 0
    y = (H - (hh + gap + eh)) / 2.0
    if event:
        _shadow_text(draw, (W - ef.getlength(event)) / 2.0, y - ef.getbbox(event)[1],
                     event, ef, fill=(255, 250, 235))
        y += eh + gap
    _shadow_text(draw, (W - hf.getlength(hero)) / 2.0, y - hb[1], hero, hf)
    return img


def _render_message(canvas, ImageDraw, line1, line2):
    W, H = canvas.width, canvas.height
    img = canvas.vgrad((34, 40, 52), (12, 14, 20))
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    f1 = _fit_width(canvas, line1, W - 4, int(H * 0.40))
    b1 = f1.getbbox(line1)
    h1 = b1[3] - b1[1]
    f2, h2 = None, 0
    if line2:
        f2 = _fit_width(canvas, line2, W - 4, int(H * 0.30))
        b2 = f2.getbbox(line2)
        h2 = b2[3] - b2[1]
    gap = 2 if line2 else 0
    y = (H - (h1 + gap + h2)) / 2.0
    _shadow_text(draw, (W - f1.getlength(line1)) / 2.0, y - b1[1], line1, f1, fill=(235, 238, 245))
    if line2:
        y += h1 + gap
        _shadow_text(draw, (W - f2.getlength(line2)) / 2.0, y - b2[1], line2, f2, fill=(150, 170, 210))
    return img


def _render_canvas(canvas, settings, caps):
    """Draw the current countdown as full-width draining bars, rotating through the active slots by
    wall-clock. ~5 fps for a smooth seconds sweep when seconds are on, gentler otherwise."""
    from datetime import datetime
    from PIL import Image, ImageDraw
    import pytz

    st = getattr(_render_canvas, '_state', None)
    if st is None:
        st = {'frame': 0}
        setattr(_render_canvas, '_state', st)
    st['frame'] += 1
    frame = st['frame']

    try:
        tz = pytz.timezone(settings.get('timezone') or 'UTC')
    except Exception:
        tz = pytz.utc
    now = datetime.now(tz)

    targets = _active_targets(settings, caps, tz, now)
    if not targets:
        canvas.frame(_render_message(canvas, ImageDraw, 'SET A TARGET', 'DATE'))
        return 30.0

    try:
        span = max(2, int(float(settings.get('transition_seconds', 6) or 6)))
    except (ValueError, TypeError):
        span = 6
    event, target = targets[_rotation_index(now.timestamp(), span, len(targets))]
    event = event.upper()
    show_seconds = _is_on(settings.get('show_seconds', 'no'))

    W, H = canvas.width, canvas.height
    total = (target - now).total_seconds()
    if total <= 0:
        canvas.frame(_render_arrived(canvas, Image, ImageDraw, event, frame))
        return 0.2

    total_i = int(total)
    days_i, rem = divmod(total_i, 86400)
    hrs_i, rem = divmod(rem, 3600)
    mins_i, secs_i = divmod(rem, 60)
    years_i, remdays_i = divmod(days_i, 365)
    days_f = total / 86400.0

    val = {'Y': years_i, 'D': (remdays_i if years_i > 0 else days_i),
           'H': hrs_i, 'M': mins_i, 'S': secs_i}
    frac = {
        'S': (total % 60.0) / 60.0,
        'M': (total % 3600.0) / 3600.0,
        'H': (total % 86400.0) / 86400.0,
        'D': ((days_f % 365.0) / 365.0) if years_i > 0 else min(1.0, days_f / 365.0),
        'Y': min(1.0, days_f / 3650.0),
    }

    header_h = max(9, min(18, int(H * 0.24))) if event else 0
    max_bars = max(1, (H - header_h) // 7)
    if W < 96:
        max_bars = min(max_bars, 3)
    max_bars = min(max_bars, 5)

    keys = (['Y', 'D'] if years_i > 0 else ['D']) + ['H', 'M']
    if show_seconds:
        keys.append('S')
    keys = keys[:max_bars]

    canvas.frame(_render_bars(canvas, ImageDraw, keys, val, frac, event, header_h))
    return 0.2 if show_seconds else 1.0


def trigger(settings, conditions):
    """Fire when the countdown reaches a configured milestone."""
    from datetime import datetime
    import pytz

    milestone = conditions.get('milestone', '1d')
    target_str = settings.get('countdown_target', '')
    try:
        tz = pytz.timezone(settings.get('timezone') or 'UTC')
    except Exception:
        tz = pytz.utc
    now = datetime.now(tz)

    if not target_str:
        return False

    try:
        target = datetime.fromisoformat(target_str)
        if target.tzinfo is None:
            target = tz.localize(target)
        diff = target - now
        total_secs = diff.total_seconds()
        if total_secs <= 0:
            return False

        windows = {
            '30d': (30 * 86400, 29 * 86400),
            '7d':  (7 * 86400,  6 * 86400),
            '1d':  (86400,      82800),
            '1h':  (3600,       3540),
            '0':   (60,         0),
        }
        lo, hi = windows.get(milestone, (86400, 82800))
        in_window = hi <= total_secs <= lo

        state = getattr(trigger, '_state', None)
        if state is None:
            state = {'fired_milestone': None}
            setattr(trigger, '_state', state)

        key = f"{milestone}:{target_str}"
        if in_window and state['fired_milestone'] != key:
            state['fired_milestone'] = key
            return True
    except Exception:
        raise
    return False
