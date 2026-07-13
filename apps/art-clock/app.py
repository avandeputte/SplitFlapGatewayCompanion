"""Art Clock - time displayed as color pixel art for Split-Flap Display."""

def fetch(settings, format_lines, get_rows, get_cols):
    from datetime import datetime
    import pytz

    tz = pytz.timezone(settings.get('timezone', 'US/Eastern'))
    now = datetime.now(tz)
    h = now.hour % 12
    if h == 0:
        h = 12
    m = now.minute

    rows, cols = get_rows(), get_cols()

    # 3x5 pixel font — used when the wall is tall enough to want it. A 5-row wall showing a
    # 3-row clock wastes 40% of the modules it was bought for.
    font5 = {
        '0': ['###', '# #', '# #', '# #', '###'],
        '1': [' # ', '## ', ' # ', ' # ', '###'],
        '2': ['###', '  #', '###', '#  ', '###'],
        '3': ['###', '  #', '###', '  #', '###'],
        '4': ['# #', '# #', '###', '  #', '  #'],
        '5': ['###', '#  ', '###', '  #', '###'],
        '6': ['###', '#  ', '###', '# #', '###'],
        '7': ['###', '  #', '  #', '  #', '  #'],
        '8': ['###', '# #', '###', '# #', '###'],
        '9': ['###', '# #', '###', '  #', '###'],
    }
    colon5 = [' ', '#', ' ', '#', ' ']

    # 3x3 pixel font for digits 0-9 (# = filled, space = empty)
    font = {
        '0': ['###', '# #', '###'],
        '1': [' # ', '## ', ' # '],
        '2': ['###', ' ##', '## '],
        '3': ['###', ' ##', '###'],
        '4': ['# #', '###', '  #'],
        '5': ['###', '## ', '###'],
        '6': ['# #', '## ', '###'],
        '7': ['###', '  #', '  #'],
        '8': ['###', '###', '###'],
        '9': ['###', '###', '  #'],
    }

    colon = [' ', '#', ' ']

    # Color palette cycles with the hour
    colors = ['r', 'o', 'g', 'b', 'p', 'w']
    c1 = colors[h % len(colors)]
    c2 = colors[(h + 3) % len(colors)]

    h_str = f'{h:02d}'
    m_str = f'{m:02d}'

    # Each row is D D : D D = 3+1+3+1+3+1+3 = 15 chars wide. On a taller wall use the taller
    # font; otherwise the 3-row one.
    glyphs, sep, height = (font5, colon5, 5) if rows >= 5 else (font, colon, 3)

    lines = []
    for row in range(height):
        line = ''
        line += glyphs[h_str[0]][row].replace('#', c1)
        line += ' '
        line += glyphs[h_str[1]][row].replace('#', c1)
        line += sep[row].replace('#', 'w')
        line += glyphs[m_str[0]][row].replace('#', c2)
        line += ' '
        line += glyphs[m_str[1]][row].replace('#', c2)
        lines.append(line)

    # Through format_lines, not returned raw: it centres the block on the wall, both
    # horizontally (the digits are 15 wide, whatever the wall is) and vertically. Returned
    # raw, a 3-row clock sat in the top-left corner of anything that was not exactly 3x15.
    return [format_lines(*lines)]
