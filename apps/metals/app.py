"""Precious metal spot prices, USD per troy ounce (keyless: gold-api.com)."""


def fetch(settings, format_lines, get_rows, get_cols):
    import requests
    rows, cols = get_rows(), get_cols()

    def price(sym):
        try:
            return requests.get(f'https://api.gold-api.com/price/{sym}', timeout=8).json().get('price')
        except Exception:
            return None

    def fmt(p):
        if not isinstance(p, (int, float)):
            return '--'
        return f'${p:.0f}' if p >= 100 else f'${p:.2f}'

    try:
        gold, silver = price('XAU'), price('XAG')
        if gold is None and silver is None:
            return [format_lines('METALS', 'OFFLINE', '')]
        if rows == 1:
            pages = []
            if gold is not None:
                pages.append(f'GOLD {fmt(gold)}/OZ'[:cols].center(cols))
            if silver is not None:
                pages.append(f'SILVER {fmt(silver)}/OZ'[:cols].center(cols))
            return pages
        if rows == 2:
            return [format_lines(f'GOLD   {fmt(gold)}', f'SILVER {fmt(silver)}')]
        return [format_lines('SPOT PRICE /OZ', f'GOLD   {fmt(gold)}', f'SILVER {fmt(silver)}')]
    except Exception:
        return [format_lines('METALS', 'OFFLINE', '')]
