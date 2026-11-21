# Demo script v1 (3:00, expo table) — story: the fleet nobody declares

Decisions 0001 (honest evidence) + 0002 (Hormuz leads). Every number below comes from a committed run;
`[TBC]` marks a number that the next data rebuild will confirm before CP3. One idea per beat; the screen
always shows the thing being said. Presentation mode (UI V2) walks these beats with keyboard → / ←.

| # | Time | Screen | Say (plain, first person) | Proof on screen |
|---|---|---|---|---|
| 1 | 0:00 | Global → Hormuz block | "Every big ship must broadcast where it is. Around the Strait of Hormuz, radar satellites see ships every day, and on a typical day about 4 in 10 of them aren't broadcasting." | daily chart, 84 days, median [TBC from #101 rebuild] |
| 2 | 0:25 | Hormuz radar layer | "Global Fishing Watch, the best public source, logged one 'dark' event inside the strait in six months. Their rule only counts ships far offshore. The strait is too narrow for that rule." | density map; "1 gap event in 6 months" |
| 3 | 0:45 | Live → Baltic, hero number | "So we built our own. Since Friday night we've recorded every ship in six shadow-fleet hotspots. This is the Baltic: 3,800 ships, tens of thousands of positions an hour." | live map, coverage line |
| 4 | 1:05 | Live → spoof event | "Physics catches what statements can't. These N unrelated ships all reported the same point, on land, near St Petersburg. Ships can't be on land: their GPS was spoofed." | spoof event detail, ship list [TBC after #100 fix] |
| 5 | 1:30 | Live → clone event | "This identity was in two places 46 km apart, ten times in five hours. One ship can't do that. Two transmitters share one ID." | clone detail: 46 km, 10 switches, 435 kn needed |
| 6 | 1:50 | Live → watchlist alert | "And we check every ship's IMO number against sanctions lists: 18 sanctioned or shadow-fleet ships sailing the Baltic right now, e.g. SAPFIR, under Canada's Russia sanctions." | red watchlist tag on a live ship |
| 7 | 2:10 | Global funnel + "How sure are we?" | "We also tested ourselves. Of 5,624 dark gaps worldwide, 109 had radar nearby; physics cleared 90. And radar near a dark ship turns out to be no better than chance — so we don't accuse on it. Location we do well: 0.87." | funnel; evidence panel 1.07×, AUC 0.87 |
| 8 | 2:40 | Close | "It's a tool for compliance teams and coast guards that separates what physics proves from what's just suspicious. Ask me about any dot." | back to the live map |

QNX sponsor table only (Sun 10:00): Patrol unit tab, kill the detector live, recovery 0.75 s.

## Rehearsal rules
Wifi off (snapshot + offline basemap). Two runs, timed. Every `[TBC]` resolved or the line cut.
