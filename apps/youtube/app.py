def fetch(settings, format_lines, get_rows, get_cols, i18n=None):
    import requests
    import xml.etree.ElementTree as ET

    def t(s):
        return i18n.t(s, "media") if i18n is not None else s

    channel_id = settings.get('yt_channel_id', '')
    if not channel_id:
        return [format_lines('YouTube', t('No channel'), t('Set ID'))]
    try:
        url = f'https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}'
        r = requests.get(url, timeout=10)
        root = ET.fromstring(r.content)
        ns = {'a': 'http://www.w3.org/2005/Atom', 'yt': 'http://www.youtube.com/xml/schemas/2015'}
        name = root.find('a:title', ns).text
        entries = root.findall('a:entry', ns)
        # The keyless RSS feed carries no subscriber count — with an API key we
        # show real subs; without one we say what we actually counted: recent
        # uploads. ("N videos" from a 15-entry feed was neither subs nor videos.)
        count = None
        api_key = settings.get('yt_api_key', '')
        if api_key:
            try:
                cr = requests.get(
                    'https://www.googleapis.com/youtube/v3/channels',
                    params={'part': 'statistics', 'id': channel_id, 'key': api_key},
                    timeout=8).json()
                subs = int(cr['items'][0]['statistics']['subscriberCount'])
                n = i18n.number(subs, 0) if i18n is not None else f'{subs:,}'
                count = f'{n} {t("subs")}'
            except Exception:
                count = None
        if count is None:
            count = f'{len(entries)} {t("recent uploads")}'
        rows = get_rows()
        if rows >= 4 and entries:
            # The feed carries the latest upload — worth a line when the wall is tall.
            latest = entries[0].find('a:title', ns)
            title = (latest.text or '') if latest is not None else ''
            extra = [t('Latest'), title[:get_cols()]] if title else []
            return [format_lines('YouTube', name, count, *extra[:rows - 3])]
        return [format_lines('YouTube', name, count)]
    except Exception:
        return [format_lines('YouTube', t('Error'), t('Check ID'))]


def trigger(settings, conditions):
    """Fire when a new video is posted or a video crosses a view milestone."""
    import requests
    import xml.etree.ElementTree as ET

    channel_id = settings.get('yt_channel_id', '')
    api_key = settings.get('yt_api_key', '')
    condition_type = conditions.get('condition_type', 'new_video')
    if not channel_id:
        return False

    state = getattr(trigger, '_state', None)
    if state is None:
        state = {'last_video_id': None, 'fired_milestones': set()}
        setattr(trigger, '_state', state)

    try:
        url = f'https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}'
        r = requests.get(url, timeout=10)
        root = ET.fromstring(r.content)
        ns = {'a': 'http://www.w3.org/2005/Atom', 'yt': 'http://www.youtube.com/xml/schemas/2015'}
        entries = root.findall('a:entry', ns)
        if not entries:
            return False
        latest_id_el = entries[0].find('yt:videoId', ns)
        if latest_id_el is None:
            return False
        vid_id = latest_id_el.text

        if condition_type == 'new_video':
            if state['last_video_id'] is None:
                state['last_video_id'] = vid_id
                return False
            if vid_id != state['last_video_id']:
                state['last_video_id'] = vid_id
                return True

        elif condition_type == 'view_milestone' and api_key:
            milestone = int(conditions.get('view_milestone', 1000000))
            # Check view count via YouTube Data API
            vr = requests.get(
                'https://www.googleapis.com/youtube/v3/videos',
                params={'part': 'statistics', 'id': vid_id, 'key': api_key},
                timeout=8
            ).json()
            items = vr.get('items', [])
            if not items:
                return False
            views = int(items[0].get('statistics', {}).get('viewCount', 0))
            key = f"{vid_id}:{milestone}"
            if views >= milestone and key not in state['fired_milestones']:
                state['fired_milestones'].add(key)
                return True

    except Exception:
        raise
    return False
