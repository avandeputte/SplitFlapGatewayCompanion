"""Precious metal spot prices, USD per troy ounce (keyless: gold-api.com)."""


def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    import requests
    rows, cols = get_rows(), get_cols()

    def t(s):
        return i18n.t(s, "metals") if i18n is not None else s

    def n(v, d):        # locale decimal/grouping (2,345 vs 2.345 vs 2 345)
        return i18n.number(v, d) if i18n is not None else f'{v:,.{d}f}'

    def price(sym):
        try:
            return requests.get(f'https://api.gold-api.com/price/{sym}', timeout=8).json().get('price')
        except Exception:
            return None

    def fmt(p):
        if not isinstance(p, (int, float)):
            return '--'
        return f'${n(p, 0)}' if p >= 100 else f'${n(p, 2)}'

    try:
        gold, silver = price('XAU'), price('XAG')
        if gold is None and silver is None:
            return [format_lines('METALS', 'OFFLINE', '')]
        # Localized names vary in length — pad both to the same width so they align.
        g, s = t('GOLD'), t('SILVER')
        w = max(len(g), len(s))
        if rows == 1:
            pages = []
            if gold is not None:
                pages.append(f'{g} {fmt(gold)}/OZ'[:cols].center(cols))
            if silver is not None:
                pages.append(f'{s} {fmt(silver)}/OZ'[:cols].center(cols))
            return pages
        if rows == 2:
            return [format_lines(f'{g:<{w}} {fmt(gold)}', f'{s:<{w}} {fmt(silver)}')]
        return [format_lines(f'{t("SPOT PRICE")} /OZ', f'{g:<{w}} {fmt(gold)}', f'{s:<{w}} {fmt(silver)}')]
    except Exception:
        return [format_lines('METALS', 'OFFLINE', '')]
