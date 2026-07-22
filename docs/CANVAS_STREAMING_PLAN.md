# Plan — adopt the Matrix Gateway v3.2 canvas stream (and status SSE)

_Studied against MatrixPortalGateway firmware **3.2.0** (API 3.1.0) — `src/web.cpp`, `openapi.yaml`,
`RELEASE_NOTES.md`. The companion currently targets 3.1 (delta rects + named atlases)._

> **Status (2.10.0-beta.2):** Phases **1–3 implemented** — `canvas.stream` detection, the
> `CanvasStream` transport, and the engine wiring that runs fast frame-push apps over the stream (all
> gated on the capability, so ≤3.1 walls are untouched). Unwired: Phase 4 (ops/sprite apps). Not
> exercised on hardware yet — needs a wall on 3.2.

## What's new in the gateway (3.2.0)

1. **`PUT /api/canvas/stream` — a persistent TLV draw channel.** One long-lived PUT carries draw
   records back-to-back, executed by an on-device pump as they arrive. **No per-frame HTTP round
   trip and no per-record response**, so the ~40 ms request/response delayed-ACK floor is gone.
   Firmware measured **28 fps client-paced** over one connection vs our current ~8 fps HTTP ceiling.
   - **Record framing** (big-endian): `u8 type, u24 payloadLength, payload`.
     | type | payload | effect |
     |------|---------|--------|
     | `0x01` | `u8 fmt (2=rgb565 BE, 3=rgb888)` + W×H×bpp pixels | full frame — draw, no present |
     | `0x02` | the `PUT /api/canvas/rects` body **verbatim** | rect deltas — draw, no present |
     | `0x03` | a JSON array (same as `POST /api/canvas/ops`) | ops; presents only via its `show` op |
     | `0x04` | sheet name | bind a named atlas sheet |
     | `0x05` | *(empty)* | present the back buffer |
     | `0x00` | *(empty)* | end — gateway replies `200 {"ok":true,"records":N}` and closes |
   - **Constraints:** declare a large placeholder `Content-Length` (no chunked inbound); **send the
     first record in the same socket write as the request head** (a bare body-carrying head
     parse-blocks the single HTTP worker for its 8 s socket timeout); **one stream at a time** — while
     open, `PUT /api/canvas/frame`, `/rect`, `/rects` answer **409**; a malformed/oversized record
     aborts it (panel keeps its last frame); **idle > 30 s drops** it.
   - Advertised as **`canvas.stream: true`** in `GET /api/capabilities` (`web.cpp:750`).
   - `GET /api/canvas/stream` reports channel state (`open, records, lastClose, …`).

2. **SSE `status` events.** `GET /api/events` now also pushes the `GET /api/status` JSON every 5 s
   (alongside `display` events). This lets a client that *polls* status stand its poller down — which
   is the **gateway's own dashboard**, not us (see "Status SSE — nothing to do" below).

3. Firmware-internal (no companion action): glyph run-blitter, pre-gzipped dashboard.

### Which endpoints 409 while a stream is open

Verified in `web.cpp` — **only** `PUT /api/canvas/frame`, `/rect`, `/rects`. **Not blocked:**
`POST /api/canvas/ops`, `PUT /api/canvas/qoi`, `PUT /api/canvas/atlas/<name>`, and `GET /api/canvas/frame`
(readback). So an atlas can still be uploaded, and the preview can still read back, with a stream open.

### Compression, honestly

The stream has **no QOI record** — a full frame in-stream is raw `0x01` (rgb565 = 2 B/px → a 256×64
keyframe is ~32 KB, vs ~2.7 KB over `PUT /api/canvas/qoi`). The stream's compression is the **delta
rects** (`0x02`): tiny when the change is localized. So the stream is a **latency/frame-rate** win,
and a **bandwidth** win only for apps whose frames mostly change in small regions. Keep the HTTP QOI
path for one-shot and slow-redraw frames.

## Where the companion is today

`backend/app/canvas.py` pushes **one HTTP request per frame**: `CanvasSurface._push_rgb` diffs vs
`_LAST_FRAME` and calls `put_rects` (delta), `put_qoi`/`put_frame` (keyframe), keyframing every
`_KEYFRAME_EVERY=20`. `draw_ops` posts ops apps' batches. The engine's `_canvas_loop` runs the app on
its own return-value cadence and hands the panel back on switch. `device.from_capabilities` parses
`canvas.rects/rect/anim/ticker/ops/readback` but **not** `canvas.stream`.

## The plan

Capability-gated, additive, with a clean fallback to today's per-frame HTTP path on any older wall or
any stream error. Phased so each step ships and soaks on its own.

### Phase 1 — capability detection (small, no behaviour change)
- `device.Capabilities`: add `canvas_stream: bool`; parse `canvas.get("stream")` in
  `from_capabilities`. Thread it through `plugins.build_canvas_surface(... stream=caps.canvas_stream)`
  into `CanvasSurface` (a `self.can_stream` flag), exactly like `can_rects`.
- Tests: `test_canvas.py` — a `stream:true` doc sets `can_stream`; absent ⇒ `False`.

### Phase 2 — the stream transport (`canvas.py`)
A small `CanvasStream` session over a **raw socket** (full control of the "first record with the
head" write; `httpx` streaming request bodies are awkward for this):
- `open(url, first_record_bytes)` — connect, `sendall` the request head + the first TLV record in one
  write, with `Content-Length: <large placeholder>`.
- `frame(fmt, pixels)` → `0x01`; `rects(body)` → `0x02` (reuse `put_rects`' body builder, factored
  out); `ops(json_bytes)` → `0x03`; `bind(name)` → `0x04`; `present()` → `0x05`.
- `close()` → `0x00`, read the `200 {"records":N}`, close the socket.
- Robustness: any `send` error, or a non-200 open (409/503/507), tears the session down and signals
  the caller to fall back to per-frame HTTP for the rest of the app's run. Respect the **30 s idle**:
  if the next frame would be > ~25 s away, `present()` a keepalive or just don't stream that app.
- Sim mode + `_SIM_URLS`: a stream is never opened; frames are cached for the preview as today.

### Phase 3 — drive frame-push apps over the stream (engine)
- In `_canvas_loop` (and `_play_canvas_entry`), when `caps.canvas_stream` and the app is **frame-push**
  and **fast** (heuristic: it returned a short hold last pass, or a per-app/manifest opt-out), open a
  stream on take-over.
- Route `CanvasSurface._push_rgb` through the stream: same diff logic, but emit `0x02` for a delta and
  `0x01` (rgb565) for a keyframe/too-big, then `0x05` present — instead of `put_rects`/`put_qoi`/`put_frame`.
  Keep `_remember_frame` so the live preview cache is unchanged.
- **Hand-back is mandatory:** send the `0x00` end record (and close) in the same place the loop
  releases the panel (`_release_canvas`/task-cancel), so a switch never leaves the stream — and thus
  the 409 lock — dangling. Belt-and-braces: a stream also self-drops after 30 s idle.
- Slow frame-push apps (Date Card → holds to midnight, World Time → to the minute, Stock Graph → 60 s,
  Image) **stay on HTTP** — a persistent stream buys them nothing and would fight the idle timeout.

### Phase 4 — ops/sprite apps (optional, later)
Aquarium, Dashboard, Scoreboard use `POST /ops` + atlas uploads. The stream can carry their ops
(`0x03`) and atlas binds (`0x04`) while atlas **uploads** still go over REST (not 409-blocked). Lower
priority — the frame-push win is bigger and simpler; revisit once Phase 3 has soaked.

### Status SSE — nothing to do (and why)
The v3.2 status-over-SSE is **not** a companion win, because the companion isn't a status *observer*:
- It calls the gateway's `/api/status` **once, at connect** (`transport/rest.py`), only to set the
  reachability pill — there is no periodic status poller to stand down.
- Its live preview is pushed to the browser over the **companion's own** `/api/events` SSE
  (`app/events.py`), built from the companion's own display state — it never consumes the *gateway's*
  `/api/events`.

So this capability improves the **gateway's own web dashboard** (whose 3 s status poller can now ride
the stream), and the companion needs no change. The **only** thing that would flip this is a future
"show live gateway health (heap / temperature / WiFi RSSI / uptime) in the companion UI" feature — at
that point, consuming the gateway's `/api/events` status frames instead of polling `/api/status` would
be the efficient path. Not in scope here.

## Guardrails / risks
- **One stream, panel-wide.** Multi-display already keys transport per gateway URL; ensure only the
  active canvas app on a given wall holds the stream, and it is always closed on switch/stop/error.
- **Keyframes are raw.** Accept ~32 KB rgb565 keyframes in-stream (infrequent); the frame-rate/latency
  win is the point. Do **not** try to interleave `PUT /qoi` mid-stream.
- **First-write requirement** is easy to get wrong — unit-test the exact bytes of the opening
  `sendall` (head + first record together).
- **Firmware floor.** Everything is gated on `canvas.stream`; walls on ≤3.1 keep today's path
  untouched. The dev panel (`192.168.1.204`) must be on **3.2.0** to exercise this end-to-end.

## Test strategy
- Unit: a fake socket capturing bytes — assert record framing (`type,u24 len,payload`), the combined
  first write, delta-vs-keyframe selection, and clean `0x00` teardown.
- Integration: a stub gateway accepting the stream and replaying `GET /api/canvas/stream` state.
- Off-device render parity unchanged (the frames are identical bytes; only the transport differs).
- Live: point one animated app (Weather Sky / Aquarium / a sweeping clock) at 3.2 hardware and confirm
  the higher frame rate and a clean hand-back (`GET /api/canvas/stream` → `open:false` after switch).

## Expected payoff
- **~8 fps → ~28 fps** for animated canvas apps (smoother rain/cloud/fish/second-sweep), because the
  per-frame HTTP round trip is gone.
- **Less bus contention** for localized-change apps (clocks): a colon/digit delta as one small `0x02`
  record, no HTTP framing around it.
- **Fewer connections** overall (one stream vs N PUTs; plus the status-SSE poller standing down).
