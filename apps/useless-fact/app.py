"""A random useless fact (keyless: uselessfacts)."""


def fetch(settings, format_lines, get_rows, get_cols, i18n=None, paginate=None):
    paginate = paginate or (lambda t, title='': [format_lines(title, t)] if title else [format_lines(t)])
    import requests

    def t(s):
        return i18n.t(s, "facts") if i18n is not None else s

    try:
        max_len = int(float(settings.get('max_length', '140') or 140))
    except (TypeError, ValueError):
        max_len = 140
    max_len = max(40, min(280, max_len))

    # The API serves facts in English and German; follow the Language for the
    # ones it has and fall back to English for everyone else.
    lang = i18n.lang_base if i18n is not None else 'en'
    api_lang = lang if lang in ('en', 'de') else 'en'

    def one():
        d = requests.get('https://uselessfacts.jsph.pl/api/v2/facts/random',
                         params={'language': api_lang}, timeout=8).json()
        return str(d.get('text', '') or '').strip()

    try:
        # This API can't filter by length, so try a few times for a short one and
        # keep the shortest we've seen if none come in under the limit.
        best = None
        for _ in range(3):
            fact = one()
            if fact and len(fact) <= max_len:
                best = fact
                break
            if fact and (best is None or len(fact) < len(best)):
                best = fact
        if not best:
            return [format_lines(t('Random fact'), t('No data'), '')]
        return paginate(best, t('Did you know'))
    except Exception:
        return [format_lines(t('Random fact'), t('Offline'), '')]
