"""Bitcoin Fear & Greed Index plugin for Split-Flap Display."""

def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    import urllib.request
    import json

    def t(s):
        return i18n.t(s, "sentiment") if i18n is not None else s

    try:
        url = "https://api.alternative.me/fng/?limit=1"
        req = urllib.request.Request(url, headers={"User-Agent": "SplitFlap/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        entry = data["data"][0]
        value = entry["value"]
        # "Extreme Fear" / "Fear" / "Neutral" / "Greed" / "Extreme Greed" -> localized.
        # The API already writes the classification as a person would, and the catalog
        # folds its keys, so it needs no uppercasing to be found — shouting it here
        # would only take the case away from the wall before the wall could decide.
        label = t(entry["value_classification"])
        # A colour square renders everywhere: a coloured pixel block on a matrix
        # wall, the matching colour FLAP on a physical one (every reel carries 7).
        n = int(value)
        tile = "🟥" if n <= 24 else "🟧" if n <= 44 else "🟨" if n <= 55 else "🟩"
        rows, cols = get_rows(), get_cols()
        if rows == 1:
            # The index value is the payload — it must never be the line that drops.
            return [format_lines(f"{tile} F&G {value} {label}"[:cols])]
        if rows == 2:
            return [format_lines("BTC Fear&Greed", f"{tile} {value}/100 {label}"[:cols])]
        # A wide wall gets a full-width gauge: the bar fills to the index (0-100) across
        # the whole wall, in the zone's colour — a red sliver at Extreme Fear, a long
        # green bar at Greed — so the mood reads at a glance from across the room. Colour
        # tiles render everywhere (matrix pixels / the matching colour FLAP on a reel),
        # like moon-phase. A narrow wall keeps the concise three-line text.
        if cols >= 24:
            filled = max(0, min(cols, round(n / 100 * cols)))
            bar = tile * filled + '⬛' * (cols - filled)
            return [format_lines("BTC Fear & Greed", bar, f"{value}/100  {label}")]
        return [format_lines("BTC Fear&Greed", f"Index: {value}/100", f"{tile} {label}")]
    except Exception:
        return [format_lines("BTC Fear&Greed", t("Offline"), "")]


def trigger(settings, conditions):
    """Fire when the Fear & Greed index crosses into extreme territory."""
    import urllib.request, json

    zone = conditions.get('zone', 'extreme_fear')
    threshold = int(conditions.get('threshold', 20))

    state = getattr(trigger, '_state', None)
    if state is None:
        state = {'last_zone': None}
        setattr(trigger, '_state', state)

    try:
        url = "https://api.alternative.me/fng/?limit=1"
        req = urllib.request.Request(url, headers={"User-Agent": "SplitFlap/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        value = int(data["data"][0]["value"])

        if zone == 'extreme_fear':
            in_zone = value <= threshold
        elif zone == 'extreme_greed':
            in_zone = value >= (100 - threshold)
        else:  # either
            in_zone = value <= threshold or value >= (100 - threshold)

        current_zone = zone if in_zone else None
        if in_zone and state['last_zone'] != current_zone:
            state['last_zone'] = current_zone
            return True
        if not in_zone:
            state['last_zone'] = None
    except Exception:
        raise
    return False
