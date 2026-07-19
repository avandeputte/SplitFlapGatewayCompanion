"""Animation — play a short looping GIF on a Matrix panel, ON-DEVICE.

A canvas app: it uploads the GIF's frames ONCE (PUT /api/canvas/anim) and the panel plays
the loop itself from PSRAM at the set rate, with nothing streamed frame by frame — so it is
smooth and costs no ongoing WiFi. The panel's spare PSRAM holds ~48 frames at 256×64, so a
longer GIF is sub-sampled to fit. On a wall without the native animation it falls back to
the first frame as a still.
"""

_MAX_FRAMES = 48


def _fit(img, w, h, mode):
    from PIL import Image
    iw, ih = img.size
    if mode == "contain":
        s = min(w / iw, h / ih)
        img = img.resize((max(1, int(iw * s)), max(1, int(ih * s))))
        out = Image.new("RGB", (w, h), (0, 0, 0))
        out.paste(img, ((w - img.size[0]) // 2, (h - img.size[1]) // 2))
        return out
    s = max(w / iw, h / ih)                                # cover: fill, centre-crop
    img = img.resize((max(1, int(iw * s)), max(1, int(ih * s))))
    left, top = (img.size[0] - w) // 2, (img.size[1] - h) // 2
    return img.crop((left, top, left + w, top + h))


def _load(url, w, h, mode):
    """Fetch a GIF and return (frames, fps): its frames fitted to the panel, sub-sampled to
    the frame cap, and the frame rate to play them at (from the GIF's own timing)."""
    import io
    import requests
    from PIL import Image
    data = requests.get(url, timeout=15,
                        headers={"User-Agent": "SplitFlapGatewayCompanion/1.0"}).content
    im = Image.open(io.BytesIO(data))
    n = getattr(im, "n_frames", 1)
    step = max(1, -(-n // _MAX_FRAMES))                    # keep every step-th frame, evenly
    frames, durs = [], []
    for i in range(0, n, step):
        if len(frames) >= _MAX_FRAMES:
            break
        im.seek(i)
        frames.append(_fit(im.convert("RGB"), w, h, mode))
        durs.append(int(im.info.get("duration", 0) or 0))
    good = [d for d in durs if d > 0]
    avg = sum(good) / len(good) if good else 0
    # A kept frame stands in for `step` original frames, so it plays for step*avg ms.
    fps = round(1000.0 / (step * avg)) if avg > 0 else 12
    return frames, max(1, min(30, fps))


def fetch(settings, format_lines, get_rows, get_cols, canvas=None):
    if canvas is None:
        return None

    # A stored gateway animation is the first choice: it already lives on the panel (saved to
    # its library, firmware 2.1), so we just tell it to play — nothing is fetched or uploaded.
    lib = str(settings.get("library_anim", "") or "").strip()
    if lib and getattr(canvas, "can_anim_library", False):
        if canvas.play_anim(lib).get("ok"):
            return 3600.0                                        # loops on-device; nothing to do

    url = str(settings.get("gif_url", "") or "").strip()
    mode = "contain" if str(settings.get("fit", "cover")).lower() == "contain" else "cover"
    if not url:
        canvas.frame(canvas.vgrad((40, 40, 60), (10, 10, 20)))   # a backdrop until a URL is set
        return 10.0

    # Fast path (firmware 2.1): the panel decodes the GIF itself. Send the raw bytes once — no
    # client-side unpacking, no frame cap, the GIF's own timing and transparency preserved. A GIF
    # larger than the panel is refused, so we fall through to the unpack-and-fit path below.
    if getattr(canvas, "can_gif", False):
        try:
            import requests
            data = requests.get(url, timeout=15,
                                headers={"User-Agent": "SplitFlapGatewayCompanion/1.0"}).content
            if canvas.gif(data).get("ok"):
                return 3600.0
        except Exception:
            pass

    try:
        frames, gif_fps = _load(url, canvas.width, canvas.height, mode)
    except Exception:
        canvas.frame(canvas.blank((30, 0, 0)))                   # a broken URL still shows something
        return 30.0

    try:                                                         # a set FPS wins; else the GIF's own
        fps = int(settings.get("fps", 0) or 0)
    except (TypeError, ValueError):
        fps = 0
    fps = max(1, min(30, fps or gif_fps))

    if getattr(canvas, "can_anim", False) and len(frames) > 1:
        canvas.anim(frames, fps=fps, loop=True)                  # uploaded once, plays on-device
        return 3600.0
    canvas.frame(frames[0])                                       # no native anim: the first frame
    return 30.0
