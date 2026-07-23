def _columns(pairs, cols, gap=3):
    """Two aligned columns — destination flush left, time flush right — kept together
    as one CENTRED block rather than pinned to the wall's edges.

    format_lines centres each line, so the block is only as wide as its content plus a
    small gap: on a wide wall the destination and its time sit together in the middle
    instead of stranded at opposite ends. The times still line up in a column (every
    line the same width). A narrow wall falls back to the full width, trimming the
    destination, never the time."""
    pairs = [(str(left), str(right)) for left, right in pairs]
    rw = max((len(r) for _, r in pairs), default=0)
    lw = max((len(l) for l, _ in pairs), default=0)
    inner = min(cols, lw + gap + rw)
    lspace = max(1, inner - rw)                       # destination column, incl. the gap
    out = []
    for left, right in pairs:
        if len(left) > lspace - 1:
            left = left[:max(0, lspace - 1)]
        out.append((left.ljust(lspace) + right.rjust(rw))[:cols])
    return out


def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    import requests
    from datetime import datetime, timezone

    def t(s):
        return i18n.t(s, "transit") if i18n is not None else s

    # Defaults match the manifest (place-NSTAT = North Station), so a blank
    # setting rides the same platform the settings dialog shows.
    stop = settings.get('mbta_stop', 'place-NSTAT')
    route = settings.get('mbta_route', 'Orange')
    rows, cols = get_rows(), get_cols()
    try:
        r = requests.get(
            'https://api-v3.mbta.com/predictions',
            params={'filter[stop]': stop, 'filter[route]': route, 'sort': 'arrival_time'},
            timeout=10
        ).json()
        preds = {}
        now = datetime.now(timezone.utc)
        for p in r.get('data', []):
            arr = p['attributes'].get('arrival_time')
            d_id = p['attributes'].get('direction_id', 0)
            if arr and d_id not in preds:
                dt = datetime.fromisoformat(arr)
                mins = max(0, int((dt - now).total_seconds() // 60))
                preds[d_id] = f'{mins} {t("min")}'

        # Where each direction actually GOES — "Forest Hills", not "Dir0". The route
        # carries a destination per direction_id; it never changes, so look it up once
        # and cache it. A generic label is the fallback if the lookup ever fails.
        cache = getattr(fetch, '_dests', None)
        if cache is None:
            cache = {}
            setattr(fetch, '_dests', cache)
        if route not in cache:
            try:
                rt = requests.get(f'https://api-v3.mbta.com/routes/{route}', timeout=8).json()
                dd = (rt.get('data') or {}).get('attributes', {}).get('direction_destinations') or []
                cache[route] = {i: name for i, name in enumerate(dd) if name}
            except Exception:
                cache[route] = {}
        dests = cache[route]

        def dname(d_id):
            return dests.get(d_id) or f'Dir{d_id}'

        no_color = settings.get('disable_colors', 'no') == 'yes'
        header = f'{route} {t("Line")}' if no_color else f'🟧 {route} {t("Line")} 🟧'
        line0 = preds.get(0, t('No data'))
        line1 = preds.get(1, t('No data'))
        if rows == 1:
            return [format_lines(f'{dname(0)} {line0}  {dname(1)} {line1}'[:cols])]
        pairs = [(dname(0), line0), (dname(1), line1)]
        if rows == 2:
            return [format_lines(*_columns(pairs, cols))]
        return [format_lines(header, *_columns(pairs, cols))]
    except Exception:
        return [format_lines('Metro', t('Error'), t('Check config'))]


def trigger(settings, conditions):
    """Fire when the next train is arriving within the configured window, or on service alerts."""
    import requests
    from datetime import datetime, timezone

    condition_type = conditions.get('condition_type', 'arriving')
    minutes = int(conditions.get('minutes', 5))
    direction = conditions.get('direction', 'either')
    stop = settings.get('mbta_stop', 'place-NSTAT')
    route = settings.get('mbta_route', 'Orange')

    state = getattr(trigger, '_state', None)
    if state is None:
        state = {'seen_alert_ids': set()}
        setattr(trigger, '_state', state)

    try:
        if condition_type == 'arriving':
            r = requests.get(
                'https://api-v3.mbta.com/predictions',
                params={'filter[stop]': stop, 'filter[route]': route, 'sort': 'arrival_time'},
                timeout=10
            ).json()
            now = datetime.now(timezone.utc)
            for p in r.get('data', []):
                arr = p['attributes'].get('arrival_time')
                d_id = p['attributes'].get('direction_id', 0)
                if not arr:
                    continue
                if direction == '0' and d_id != 0:
                    continue
                if direction == '1' and d_id != 1:
                    continue
                dt = datetime.fromisoformat(arr)
                mins_away = (dt - now).total_seconds() / 60
                if 0 <= mins_away <= minutes:
                    return True

        elif condition_type == 'alert':
            r = requests.get(
                'https://api-v3.mbta.com/alerts',
                params={'filter[route]': route, 'filter[stop]': stop},
                timeout=10
            ).json()
            for alert in r.get('data', []):
                aid = alert.get('id', '')
                effect = alert.get('attributes', {}).get('effect', '')
                # Only fire for service-affecting alerts
                if effect in ('DELAY', 'SUSPENSION', 'SHUTTLE', 'STOP_CLOSURE', 'DETOUR'):
                    if aid not in state['seen_alert_ids']:
                        state['seen_alert_ids'].add(aid)
                        return True
            # Prune old alert IDs
            if len(state['seen_alert_ids']) > 200:
                state['seen_alert_ids'] = set(list(state['seen_alert_ids'])[-100:])

    except Exception:
        raise
    return False
