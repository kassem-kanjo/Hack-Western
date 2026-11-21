# Dark Fleet console: design

Files: `tokens.css` (all values; `web/` imports it), `wire.css` (wireframe components, tokens only), `01`..`06` `*.html` (open in a browser at 1440 x 900; decision notes in the right margin). Placeholder data; coastline shapes are stand-ins.
1. **Grid:** 1440 x 900. 48 px top bar; 1000 px map column; 440 px right rail. KPI strip (104 px) sits on top of the map; case rail on the right. 4 px spacing base, 2/4/8 px radii.
2. **The 10-second read:** KPI numbers (44 px mono) say how much; the map shows where (dashed gap, violet lens, diamonds, pink ring); the ranked rail says who first.
3. **Colours = meaning:** teal dot = ship broadcasting (AIS); amber dashed line = AIS gap, last ping to reappearance; violet translucent lens = where it could have been (space-time prism); blue diamond = radar detection (SAR, ~1 km cell, date only, labelled by day; matched against the union of the lens over that whole day); white diamond with blue ring = detection inside a lens (candidate); pink double ring = possible meeting (prisms overlap).
4. **Colour-blind safe:** the amber / blue / pink trio is also separated by shape (dash / diamond / ring). Risk is always chip text plus bar length (green low, amber medium, red high). Every text colour is at least 5.2:1 on its panel.
5. **Status colours:** green LIVE badge; violet-white SNAPSHOT badge with capture time; red ERROR; amber DEGRADED / PARTIAL DATA.
6. **Type:** IBM Plex Sans for words, JetBrains Mono for every number, id and time (bundle both locally for offline demos). Scale 12/13/14/16/20/28/44/72.
7. **Always on screen:** "Powered by Global Fishing Watch" bottom-left of every map; mode badge top right.
8. **Components to build once:** Kpi, CaseRow, RiskChip, Legend, MapAttribution, ModeBadge, Timeline (single and two-track), LensFrame, FactorRow, SourceRow, Banner (error, partial).
9. **Motion:** only the lens scrubber and the live pulse; both off under `prefers-reduced-motion`.
10. **Self-check done:** greyscale read (shapes carry meaning), one-colour read, 1 m read (smallest map label 12 px, key numbers 44 px and up). Desktop only; phone layout is out of scope.

## Chief decisions on open questions (Fri 23:50)
1. Fonts: bundle IBM Plex Sans + JetBrains Mono via @fontsource (OFL), no CDN at demo time.
2. Rendezvous = lenses overlap at the same instant (Sharma & Shekhar 2022). Narrow windows are expected and honest.
3. Risk score 0-100; bands: < 40 low, 40-69 medium, >= 70 high. Factors and weights defined in core (#12).
4. Sanctions wording approved: "Possible sanctions list match · needs analyst confirmation".
5. No phone layout; demo video records the desktop console.
6. Region: investigation views use Strait of Hormuz / Gulf of Oman; 04-live uses the Hormuz watchlist (data probe, docs/feasibility-dark-fleet.md). Fix the right-rail overflow at 1440 px in the build.
