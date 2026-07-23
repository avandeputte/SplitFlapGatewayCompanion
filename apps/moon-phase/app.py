def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    from datetime import datetime, timezone
    import math

    def t(s):
        return i18n.t(s, "moon") if i18n is not None else s

    def u(k):                       # localized D/H/M/S suffix (French J for jour, etc.)
        return i18n.unit(k) if i18n is not None else k

    # The moon's phase is a fact about the moon, not about your wall clock — the
    # old local-tz conversion here converted straight back to UTC and cancelled out.
    now = datetime.now(timezone.utc)

    # Known new moon: January 6, 2000 18:14 UTC
    ref = datetime(2000, 1, 6, 18, 14, 0, tzinfo=timezone.utc)
    diff = (now - ref).total_seconds()
    synodic = 29.53058867
    days_into_cycle = (diff / 86400) % synodic

    # Phase name
    phase_idx = int(days_into_cycle / (synodic / 8))
    phases = [
        'New moon', 'Waxing crescent', 'First quarter', 'Waxing gibbous',
        'Full moon', 'Waning gibbous', 'Last quarter', 'Waning crescent'
    ]
    phase_name = phases[phase_idx % 8]

    # Illumination percentage
    illumination = (1 - math.cos(2 * math.pi * days_into_cycle / synodic)) / 2
    illum_pct = int(illumination * 100)

    # Days to next full and new moon
    full_moon_day = synodic / 2
    if days_into_cycle < full_moon_day:
        days_to_full = full_moon_day - days_into_cycle
    else:
        days_to_full = synodic - days_into_cycle + full_moon_day
    days_to_new = synodic - days_into_cycle

    cols = get_cols()

    # "Full in 5 Days" where there's room, "Full in 5D" only where there isn't — a wide
    # Matrix wall has no reason to abbreviate. Both the full word and the compact suffix
    # are localized (the time domain: Days->Jours/Tage, D->J/T).
    def days_line(label, n):
        n = int(n)
        full = f'{t(label)} {n} {u("Days")}'
        return full if len(full) <= cols else f'{t(label)} {n}{u("D")}'

    # Visual bar: colour tiles render everywhere — yellow pixels on a matrix wall,
    # the yellow colour FLAP on a physical one, where a literal 'w' was just the
    # letter W repeated across the row.
    filled = int(illumination * cols)
    bar = '🟨' * filled + '⬛' * (cols - filled)

    name = t(phase_name)
    if get_rows() >= 4:
        # Everything is already computed; a 3-row wall just couldn't show it at once.
        return [
            format_lines(name, bar, f'{illum_pct}% {t("Lit")}',
                         days_line("Full in", days_to_full),
                         days_line("New in", days_to_new)),
        ]
    pages = [
        format_lines(name, f'{illum_pct}% {t("Lit")}', days_line("Full in", days_to_full)),
        format_lines(name, bar, days_line("New in", days_to_new)),
    ]
    return pages


def trigger(settings, conditions):
    """Fire on full moon or new moon."""
    from datetime import datetime, timezone

    phase_type = conditions.get('phase', 'full')
    # UTC throughout — the phase doesn't depend on the configured timezone.
    now = datetime.now(timezone.utc)

    ref = datetime(2000, 1, 6, 18, 14, 0, tzinfo=timezone.utc)
    diff = (now - ref).total_seconds()
    synodic = 29.53058867
    days_into_cycle = (diff / 86400) % synodic

    state = getattr(trigger, '_state', None)
    if state is None:
        state = {'fired_phase': None}
        setattr(trigger, '_state', state)

    # Full moon: days 13.5–15.5 into cycle; new moon: days 0–1 or 28.5–29.5
    if phase_type == 'full':
        in_phase = 13.5 <= days_into_cycle <= 15.5
    else:  # new
        in_phase = days_into_cycle <= 1.0 or days_into_cycle >= 28.5

    phase_key = f"{phase_type}:{int(days_into_cycle)}"
    if in_phase and state['fired_phase'] != phase_key:
        state['fired_phase'] = phase_key
        return True
    if not in_phase:
        state['fired_phase'] = None  # reset so next occurrence fires
    return False
