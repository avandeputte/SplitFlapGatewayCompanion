# Attribution

SplitFlap Gateway Companion is a **derivative work** and is licensed under
**Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International
(CC BY-NC-SA 4.0)** — see [LICENSE](LICENSE).

## Upstream projects

- **[csader/splitflap-os](https://github.com/csader/splitflap-os)** — CC BY-NC-SA 4.0.
  The companion vendors splitflap-os's **app plugins** (`apps/*`) and reimplements a
  **behavior-identical plugin runtime**: the `fetch(settings, format_lines, get_rows,
  get_cols)` / `trigger(settings, conditions)` contract, the `format_lines` / `get_rows`
  / `get_cols` helpers, the manifest settings schema and field renderer, the
  `search_chips` helper endpoints, the emoji colour-tile map, and the
  transition/animation orderings are all ports of splitflap-os. Keeping them
  identical is what makes plugins interchangeable in both directions. (Final
  character normalization deliberately differs — see [Compatibility](https://github.com/avandeputte/SplitFlapGateway/wiki/Compatibility).)

  **Pinned upstream:** commit `12df2773cbbe9890a7d6f92fdc60d2be920129bd`
  (splitflap-os `VERSION` 0.3.0, 2026-06-24). See [Compatibility](https://github.com/avandeputte/SplitFlapGateway/wiki/Compatibility).

- **[Adam G Makes — Split-Flap Display](https://github.com/adamgmakes/SplitFlapDisplay)**
  — CC BY-NC-SA 4.0. The underlying hardware platform that splitflap-os is built on.

- **[avandeputte/SplitFlapGateway](https://github.com/avandeputte/SplitFlapGateway)** and
  **[SplitFlapUniversalFirmware](https://github.com/avandeputte/SplitFlapUniversalFirmware)**
  — the ESP32 gateway/firmware this companion targets.

## Originally authored here

The engine (play loop, config store, state), the transports (REST / sim), the
gateway reverse-proxy, the SPA UI, and the packaging are original to this project.
