"""On-device panel effects for a Matrix wall (the `canvas`/`effects` capability).

The panel renders these itself, at its native ~70 fps, with nothing on the
network — the companion just names the effect once. A canvas app: it draws to
the framebuffer through the injected `canvas` helper instead of returning flap
pages, and the engine only runs it on a wall that has a framebuffer.
"""


def fetch_matrix(settings, canvas):
    effect = str(settings.get('effect', 'plasma') or 'plasma').lower()
    if canvas.effects and effect not in canvas.effects:
        effect = canvas.effects[0]        # this panel doesn't have that one — use its first

    # Self-describing wall (effectDefs): the wall names exactly the params THIS
    # effect consumes, with types and ranges — serialize those and nothing else. A blank
    # int keeps the effect's own default (the key is omitted); a bool goes explicitly
    # true/false so a def with default:true can still be turned off.
    d = next((d for d in getattr(canvas, 'effect_defs', ()) or ()
              if d.get('id') == effect), None)
    if d:
        params = {}
        for pd in d.get('params') or []:
            key, typ = str(pd.get('key') or ''), str(pd.get('type') or '')
            raw = str(settings.get(key, '') or '').strip().lower()
            if not key or not raw:
                continue
            if typ == 'bool':
                params[key] = raw in ('yes', 'on', '1', 'true')
            elif typ == 'int':
                try:
                    v = int(float(raw))
                except (TypeError, ValueError):
                    continue
                if pd.get('min') is not None:
                    v = max(int(pd['min']), v)
                if pd.get('max') is not None:
                    v = min(int(pd['max']), v)
                params[key] = v
            # an unknown type is skipped — that knob keeps its on-device default
        canvas.effect(effect, params=params)
        return None

    # Older firmware: the flat knob list. speed always; hue (0-255) and density (1-100)
    # only where the wall advertises them (caps.effect_params) and only when actually set —
    # blank means "keep the effect's own default look".
    speed = canvas.num(settings, 'speed', 5)
    knob_names = getattr(canvas, 'effect_params', ())

    def _opt(key, lo, hi):
        raw = str(settings.get(key, '') or '').strip()
        if not raw or key not in knob_names:
            return None
        try:
            return max(lo, min(hi, int(float(raw))))
        except (TypeError, ValueError):
            return None

    canvas.effect(effect, speed, hue=_opt('hue', 0, 255), density=_opt('density', 1, 100))
    # The panel renders on its own now; re-affirm only occasionally (loop_delay).
    return None
