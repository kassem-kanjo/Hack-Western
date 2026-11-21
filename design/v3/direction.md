# Dark Fleet V2: visual direction ("instrument")

Mockups `watch/hormuz/evidence/patrol.png` (1440 x 900, live API data, 21 Nov 21:16Z). Tokens `tokens.css`; components `kit.css` + `components.md`.

## Why the current UI reads cheap
It isn't the darkness or the density. It's that **nothing on screen carries more weight than anything else**.
Every row is 13 px, every kind is a coloured square, ships are identical dots, the reach area is a flat purple
disc, and the Watch screen uses `system-ui` while the other screens use Plex. Two type systems, six hues,
browser checkboxes, three rows of wrapping chips. The references (Gotham and Foundry maps, Lattice, Skylight,
GFW) are quiet in the same way: grey chrome, one family of type, hairlines. They feel expensive because
**only the thing that needs a decision has colour, and every glyph has a meaning you can read off its shape**.

## Principles
1. **Colour means state.** Chrome, text and ordinary ships are grey. The only hues are the six state colours
   (dark, sanctioned, spoof, radar, ok, degraded). Selection and focus are ice-white, never a hue, so a
   selected row can't be mistaken for an alert.
2. **Shape carries meaning before colour.** Ship = chevron pointed by course (dot when stopped or no course).
   Dark = broken ring. Listed ship stopped = solid square. Spoof = crossed square. Radar = diamond.
   Meeting = two linked rings. The screens still read in greyscale.
3. **One figure per view, set large and light.** Plex Sans 300 at 48-64 px, with a plain sentence under it
   (41% / 5,624 to 19 / 1 h 21 min dark). Everything else is 11-14 px.
4. **One family of type, two voices.** IBM Plex Sans for words and figures (tabular numbers); JetBrains
   Mono only for things you would copy: MMSI, UTC times, coordinates. Sentence case everywhere, no
   all-caps labels.
5. **Hairlines and three elevations, not boxes.** Docked panels are flat (`surface-1`, 1 px `line-1`).
   Floating map panels are `surface-2` with a 1 px top highlight and a soft shadow. Only overlays
   (`surface-3`) cast a real shadow. Radii: 0 docked, 2 px controls, 4 px floating.
6. **The map is a chart, not a picture.** Land is one step lighter than the sea, the coast is a hairline,
   there's a graticule with degree labels in mono, a scale bar, a cursor readout and a locator inset.
   Uncertainty is drawn precisely: the reach area is a 1 px ice line with a faint hatch, not a tinted blob.
   Radar density is crisp 0.1° tiles, not a blur.
7. **Label things on the map, not only in the legend.** The focused ship, its last broadcast, hour ticks
   on its track, the reach radius ("Could have reached: 40 km") and each ship it could have met
   (name, distance, state) are labelled where they are, with a 3 px halo.
8. **Time is a first-class axis.** Every investigative view has a timeline: track pings, the dark
   interval (dashed amber), our receivers' outages (degraded bands), and a "now" handle.
9. **Every sentence is a finding.** Panel titles say what the data shows ("Radar near a dark ship is no
   better than chance"), the "why flagged" line names the evidence, and every caveat sits beside its number.
10. **4 px grid, fixed rhythm.** Controls 28 px, chips 22 px, table rows 28 px, queue rows 52 px, panel
    headers 36 px, top bar 40 px, status bar 24 px.

## Remove from the current UI
- `system-ui` in `watch.css` (3 places): use the token fonts everywhere.
- Coloured squares as the only kind marker; wrapping three-row chip block; native checkboxes in the map
  layer box; the severity-sorted list (group by "Happening now" / "Earlier" instead).
- Purple reach disc and purple prism tokens; teal AIS dots; magenta meeting colour. Ships go neutral.
- The world-extent opening view (open on the focused area with a locator inset).
- Uppercase tracked section headers ("EVENTS", "WATCHED AREAS", "SOURCES").
- Pills and 8 px radii; the amber `LIVE` outline badge (use the dot + word badge).
- "Unnamed · MMSI ..." strings: show `MMSI 261003561` in regular weight instead.
- Blurred heatmap blobs on Hormuz (use the cell grid).
- Duplicate meeting rows: GFW lists each meeting once per ship; show one row per pair.

**Motion** (only where it explains a change): row select 160 ms; focus panel slides in 240 ms `ease-out`; map camera fly 900 ms `ease-move` from area to
ship; the reach ring grows with elapsed time once per minute; the live dot pulses at 2 s. All off under
`prefers-reduced-motion`.
## Open questions for Chief
- `ships[]` in `/api/watch` has no course. Chevrons need `"cog": last.cog` (one line in
  `scripts/build_watch.py`, ships builder). Mockup courses were read from the raw AIS hour files.
- Patrol: no real frame or detection exists yet; the frame and box in `patrol.png` are stand-ins.
