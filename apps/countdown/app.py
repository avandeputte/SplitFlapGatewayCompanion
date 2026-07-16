def fetch(settings, format_lines, get_rows, get_cols, i18n=None, caps=None):
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

    pages = []
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
        pages.extend(build_slot_pages(event, target, now, rows, cols))

    if pages:
        return pages

    if rows == 2:
        return [format_lines('Countdown', 'Check config')]
    return [format_lines('Countdown', 'Check config', '')]


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
