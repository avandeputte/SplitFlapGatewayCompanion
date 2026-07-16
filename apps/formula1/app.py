"""Formula 1 — next Grand Prix & championship leader (keyless: Jolpica / Ergast)."""


def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    import requests
    from datetime import datetime, timezone
    rows, cols = get_rows(), get_cols()

    def t(s, ctx="sports"):
        return i18n.t(s, ctx) if i18n is not None else s

    def u(k):                                 # localized D/H duration suffix
        return i18n.unit(k) if i18n is not None else k

    pages = []
    try:
        nxt = requests.get('https://api.jolpi.ca/ergast/f1/current/next.json', timeout=10).json()
        races = nxt.get('MRData', {}).get('RaceTable', {}).get('Races', [])
        if races:
            r = races[0]
            name = str(r.get('raceName', '')).replace('Grand Prix', 'GP')
            cd = ''
            try:
                dt = datetime.fromisoformat(
                    f"{r.get('date', '')}T{r.get('time', '00:00:00Z')}".replace('Z', '+00:00'))
                secs = int((dt - datetime.now(timezone.utc)).total_seconds())
                if secs > 0:
                    d, h = secs // 86400, (secs % 86400) // 3600
                    in_ = t('In', 'time')
                    cd = f'{in_} {d}{u("D")} {h}{u("H")}' if d else f'{in_} {h}{u("H")}'
                else:
                    cd = t('Race weekend')
            except ValueError:
                pass
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
        st = requests.get('https://api.jolpi.ca/ergast/f1/current/driverStandings.json', timeout=10).json()
        lists = st.get('MRData', {}).get('StandingsTable', {}).get('StandingsLists', [])
        ds = lists[0].get('DriverStandings', []) if lists else []
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
