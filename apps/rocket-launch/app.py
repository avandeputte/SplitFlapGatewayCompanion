"""Next orbital rocket launch (Launch Library 2 / The Space Devs, keyless)."""


def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    import requests
    from datetime import datetime, timezone
    rows, cols = get_rows(), get_cols()

    def t(s, ctx="space"):
        return i18n.t(s, ctx) if i18n is not None else s

    def u(k):                                 # localized D/H/M duration suffix
        return i18n.unit(k) if i18n is not None else k

    try:
        data = requests.get('https://ll.thespacedevs.com/2.2.0/launch/upcoming/',
                            params={'limit': 1, 'mode': 'list'}, timeout=10).json()
        res = data.get('results') or []
        if not res:
            return [format_lines(t('NEXT LAUNCH'), t('NONE'), t('SCHEDULED'))]
        r = res[0]
        name = str(r.get('name', ''))
        rocket, _, mission = name.partition('|')
        rocket = rocket.strip() or 'ROCKET'
        mission = mission.strip() or rocket
        cd, when = '', ''
        net = r.get('net')
        if net:
            try:
                dt = datetime.fromisoformat(str(net).replace('Z', '+00:00'))
                # The countdown says how long; on a tall wall there is room to say WHEN.
                # net is UTC, so it has to be moved into the user's zone or a launch late
                # tonight reads as tomorrow.
                try:
                    import pytz
                    local = dt.astimezone(pytz.timezone(settings.get('timezone', 'US/Eastern')))
                except Exception:
                    local = dt
                if i18n is not None:
                    when = f'{i18n.weekday(local, short=True)} {i18n.time(local, ampm_space=False)}'
                else:
                    when = local.strftime('%a %I:%M%p').lstrip('0')
                secs = int((dt - datetime.now(timezone.utc)).total_seconds())
                if secs <= 0:
                    cd = t('IMMINENT')
                else:
                    d, h, m = secs // 86400, (secs % 86400) // 3600, (secs % 3600) // 60
                    in_ = t('IN', 'time')
                    cd = (f'{in_} {d}{u("D")} {h}{u("H")}' if d
                          else (f'{in_} {h}{u("H")} {m}{u("M")}' if h
                                else f'{in_} {m}{u("M")}'))
            except ValueError:
                cd = ''
        if rows == 1:
            return [f'{rocket} {cd}'[:cols].center(cols)]
        if rows == 2:
            return [format_lines(t('NEXT LAUNCH'), rocket), format_lines(mission, cd)]
        if rows == 3:
            return [format_lines(t('NEXT LAUNCH'), rocket, cd),
                    format_lines(t('MISSION'), mission, cd)]

        # Four rows or more: it all fits at once. Splitting the rocket from its mission
        # across two pages was a three-row compromise — on a taller wall it just means
        # waiting for a page turn to read the other half of one sentence.
        lines = [t('NEXT LAUNCH'), rocket, mission]
        if rows >= 5 and when:
            lines.append(when)
        lines.append(cd)
        return [format_lines(*lines)]
    except Exception:
        return [format_lines(t('NEXT LAUNCH'), t('OFFLINE'), '')]
