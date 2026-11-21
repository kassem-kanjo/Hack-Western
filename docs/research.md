# Dark Fleet: sourced research (issue #9)

Scout, 2026-11-20. Every claim has a URL. **[S]** = secondary (news, vendor, blog or search summary; primary not read). **[U]** = unverified, check before the pitch.

## Answer
1. **The dark-rendezvous idea already exists in the literature, so our claim has to be "public, visual, explainable tool", not "new method".** Space-time prism overlap as a "possible rendezvous region" was published in 2022 ([Sharma & Shekhar, ACM TIST](https://doi.org/10.1145/3467977)). Overlapping AIS gaps plus proximity as a ship-to-ship (STS) signal was published in 2025 ([NBER w33486](https://www.nber.org/papers/w33486)). Commercial tools already fuse SAR and AIS for dark STS. We found no public tool that draws prisms, matches SAR detections inside the lens and maps rendezvous.
2. **Best Baltic demo case: Flora 1 (IMO 9307815, April 2026).** Its AIS signal stopped on 3 April off Ystad, and it was detained by Sweden [S][mezha.net](https://mezha.net/eng/news/swedish_coast_guard/). It is a dated gap with a known start. **Eagle S** is the best-known story, but the brief's IMO 9304033 is not supported: every registry result gives **IMO 9329760** [VesselFinder](https://www.vesselfinder.com/vessels/details/9329760). Whether Eagle S's AIS was off on 25 Dec 2024 is not confirmed in my sources [U].
3. **Sanctions data: OpenSanctions is free only for non-commercial use** (CC BY-NC 4.0). A hackathon demo is probably fine, but a sales pitch to banks or insurers needs a paid licence [OpenSanctions](https://www.opensanctions.org/datasets/maritime), [FAQ](https://www.opensanctions.org/faq/32/). Get that answered before the Devpost goes out.

## 1. Prior art
| Player | What it does | Infers where a dark ship went? | Infers hidden meetings of two dark ships? |
|---|---|---|---|
| Global Fishing Watch (GFW) map | Public AIS gap and disabling counts (12 h, 24 h), encounters, IUU listings [GFW](https://globalfishingwatch.org/?p=392) | No prism; gaps only | No; encounters need AIS on both ships [U] |
| GFW research, Welch et al. 2022 | 55,000+ suspected disabling events, 2017-2019, obscuring ~5 million fishing hours [S][GFW](https://globalfishingwatch.org/article/hotspots-of-unseen-fishing-vessels-qa/); paper [DOI 10.1126/sciadv.abq2109](https://doi.org/10.1126/sciadv.abq2109) | No | No |
| Skylight (Ai2) | Free for governments and conservationists; SAR dark-vessel detection from xView3 work; flags loitering and transshipment with an AIS vessel [S][Skylight](https://skylight.global/news/this-ai-is-set-to-help-stop-illegal-fishing----heres-how) | No published prism | Partly: AIS-side transshipment alerts; vendor claims, ~80% accuracy [S] |
| Windward | All-source AIS + SAR + EO + RF; says SAR/EO show two hulls alongside each other even when dark [S][Windward](https://windward.ai/blog/how-to-track-dark-vessels-when-ais-goes-silent/) | Gap triage by location; no prism claim | Yes (imagery), vendor claim; dashboard 1,358 "dark fleet" vessels, Aug 2026 [S] |
| Kpler (absorbed MarineTraffic and FleetMon) | AIS gaps, dark STS, SAR cross-check [S][Satnews](https://satnews.com/2026/03/02/kpler-marine-leverages-real-time-ais-data-to-map-dark-fleet-movements-amid-u-s-iran-conflict/) | Not published | Yes, dark STS; vendor claims [Kpler](https://www.kpler.com/zh/blog/dark-sts-transfers-compliance-risk) |
| Spire | AIS + SAR fusion for sanctions; dark STS and spoofing pitch; mostly 2022-23 material [S][Spire](https://insights.spire.com/maritime/dark-shipping-detection) | Not published | Pitched, not documented |
| SkyTruth Cerulean | Free; SAR oil slicks matched to ships and platforms, using GFW's dark-vessel dataset; coastal EEZ only [SkyTruth](https://skytruth.org/Cerulean/), [methods](https://skytruth.org/cerulean-methods/) | No | No |
| Starboard | Tracks shadow-fleet tankers and reflagging [S][Starboard](https://starboard.nz/indo-pacific-2023) | No method found [U] | No method found [U] |

**Conclusion:** the published method (prisms, rendezvous from gap overlap) is old, but not in any public tool we found. Commercial vendors do dark STS with SAR, and none publishes its method. Our defensible claim is a public, explainable product that shows the lens, the SAR match and the rendezvous, and states its limits. Our searches were not exhaustive (no Google Scholar pass).

## 2. Method prior art
- **Time geography.** Hägerstrand (1970) is the origin; Miller (1991, IJGIS 5(3): 287-302) projected the prism onto the ground as the "potential path area" [S][Wikipedia](https://en.wikipedia.org/wiki/Time_geography), [Miller](https://u.osu.edu/miller.81/files/2017/03/Miller-time-geography-and-space-time-prism-1rp3u1q.pdf).
- **Rendezvous from gaps.** Sharma & Shekhar (2022): the intersection of two prisms is a possible rendezvous region; the ellipse is an upper bound, and the lens at any instant is tighter. They use time slicing and pruning [arXiv 2206.12756](https://arxiv.org/pdf/2206.12756), [DOI](https://doi.org/10.1145/3467977). **Copy:** the per-instant lens, and slice-and-prune for search speed.
- **Prisms for gaps.** Physics-guided gap detection bounds gaps with prisms and uses space-time indexing [arXiv 2403.06268](https://arxiv.org/pdf/2403.06268). An environment-kinetic prism for multi-ship risk in confined water appeared in the Journal of Navigation (2025) [Cambridge](https://www.cambridge.org/core/product/identifier/S0373463325101045/type/journal_article).
- **SAR-to-AIS matching.** xView3-SAR used an AIS-to-SAR matching algorithm to label 243,018 objects in 991 Sentinel-1 scenes [arXiv 2206.00897](https://arxiv.org/abs/2206.00897). **Copy:** its matching method; we have not read its Methods [U].
- **Paolo et al. 2024, Nature.** About 75% of industrial fishing is not publicly tracked by AIS. SAR detection is >70% for 25 m vessels and >90% for 50 m and up [S][ESA](https://www.esa.int/Applications/Observing_the_Earth/Copernicus/Sentinel-1/Sentinel-1_and_AI_reveal_75_of_fishing_vessels_not_tracked), [Nature](https://www.nature.com/articles/s41586-023-06825-8). **Against:** a SAR miss is not evidence of absence.
- **Gap rules to copy (GFW).** Naive gap at 6 h or more; likely disabling at 12 h or more, starting at least 50 nm offshore, with reception above 10 positions/day and at least 14 positions in the prior 12 h [GFW technical docs](https://globalfishingwatch.github.io/GFW_technical_documentation/Gaps.html). This handbook's cover says staff-only, so cite the public API instead [GFW API](https://globalfishingwatch.org/our-apis/documentation/docs/v3/events) [U].
- **Match radius.** No source found. We must set it and justify it ourselves.

## 3. Real cases for the demo
**Baltic (first choice)**
- **Flora 1, IMO 9307815.** Swedish coast guard detained it in April 2026 after an oil slick east of Gotland. Last signal 3 April off Ystad; likely departed Ust-Luga [S][mezha.net](https://mezha.net/eng/news/swedish_coast_guard/). **Best for the prism:** dated gap, both ends known [U: confirm fixes in GFW data].
- **Eagle S, IMO 9329760.** Cook Islands flag. Estlink 2 cut on 25 Dec 2024; police boarded on 26 Dec; anchor lifted on 6 Jan 2025 [S][Al Jazeera](https://www.aljazeera.com/news/2024/12/27/did-a-russian-shadow-ship-cut-the-finland-estonia-undersea-baltic-cable), [NPR](https://www.npr.org/2024/12/27/g-s1-40114/finland-russia-vessel-undersea-cables). Capacity fell from 1,016 to 358 MW [S][Wikiwand](https://www.wikiwand.com/en/articles/2024_Estlink_2_incident). A Helsinki court ruling on jurisdiction (Oct 2025) is reported in one secondary source [U].
- **Rina and Zircone bunkering, 2024-25.** Investigation: 286 ships serviced at sea; more than 160 had called at Primorsk or Ust-Luga [S][LRT](https://www.lrt.lt/en/news-in-english/19/2726119/baltic-companies-secretly-fuelling-russia-s-shadow-fleet). **Best for the rendezvous:** named STS pairs to test against AIS.
- **Green Admire, July 2026.** Russia detained it in the Gulf of Finland after it left an Estonian port; AIS showed signs of spoofing [S][Moscow Times](https://www.themoscowtimes.com/2026/07/27/estonia-says-russia-detained-greek-owned-tanker-in-gulf-of-finland). Good for the "spoofing, not a gap" flag. Date detail [U].

**Backups**
- **Gulf of Oman / Hormuz STS.** Transfers continue outside the strait; Reuters imagery on 18 July 2026 showed one pair [S][Gulf News](https://gulfnews.com/world/mena/ship-to-ship-transfers-keep-gulf-oil-moving-outside-hormuz-as-direct-traffic-hits-multi-month-low-1.500652385), [Al-Monitor](https://www.al-monitor.com/index.php/originals/2026/07/oil-transfer-activity-gulf-oman-slows-following-ship-attacks-data-shows). Volatile; war context.
- **Black Sea.** Four tankers broadcast false positions near Novorossiysk; GFW compared AIS with imagery [S][GFW](https://globalfishingwatch.org/?p=36082). Windward counted six dark tankers hugging the Turkish coast [S][HSN](https://www.hellenicshippingnews.com/?p=1117720).
- **Argentine EEZ.** Oceana/GFW: 800+ vessels that appeared to switch off AIS, 6,000+ gap events over 24 h, 2018 to April 2021 [S][Oceana](https://mx.oceana.org/wp-content/uploads/sites/17/2021/06/oceana_argentina_mini_report_finalupdated.pdf). CNN (2025) named 198 vessels in the area [S][KRDO/CNN](https://krdo.com/news/2025/03/10/why-argentinas-military-is-deploying-to-surveil-hundreds-of-chinese-fishing-boats-off-its-coast-2/).

## 4. Sanctions data
- **OpenSanctions maritime.** Licence: CC BY-NC 4.0, free for non-commercial use; businesses need a licence, and compliance screening counts as commercial [OpenSanctions](https://www.opensanctions.org/datasets/maritime), [exemption page](https://www.opensanctions.org/docs/commercial/exemption/). Downloads: CSV `https://data.opensanctions.org/artifacts/maritime/20261120150306-max/maritime.csv` (dated path); index `https://data.opensanctions.org/datasets/latest/maritime/index.json`. Fields: `caption` (name), `imo`, `mmsi`, `flag`. Updated daily. Attribution required [older terms, U].
- **OFAC SDN.** Published in XML, fixed-width and delimited files, including `SDN_ENHANCED.XML` [OFAC data](https://ofac.treasury.gov/ofac-sanctions-lists). Vessels carry IMO in a vessel registration identification field [example entry](https://sanctionssearch.ofac.treas.gov/Details.aspx?id=57014). Public-domain status is standard for US government data [U, not sourced]. Jan 2025 action: 183 vessels (143 tankers) [S][Baltic Exchange](https://www.balticexchange.com/kyc/en/news-and-events/news/kyc-news/2025/us-sanctions-january-25.html).

## 5. Max speeds by class
- **Tankers.** Crude carriers ~13-17 kn; bulk ~12-15 kn. Source is a 2018 industry table quoted in [arXiv 2202.01000](https://arxiv.org/pdf/2202.01000) [S]. Aframax: blog sources give 12-17 kn [S, weak] [Marine Insight](https://www.marineinsight.com/speed-of-a-ship-at-sea/).
- **Container ships.** 14-24 kn across sources [S][Marine Insight](https://www.marineinsight.com/speed-of-a-ship-at-sea/).
- **Fishing.** No source found [U]. Compute the 99th percentile from our own AIS data.
- **Cleaning.** Speed over ground above 80 kn is an erroneous transmission [arXiv 2509.18109](https://arxiv.org/html/2509.18109v1) [S].
- **Proposal (ours, not sourced):** tanker cap 17 kn, bulk 15 kn, each with margin; label as our assumption in the UI.

## 6. Judge angle
1. About 75% of industrial fishing is invisible to AIS [S][Nature 2024](https://www.nature.com/articles/s41586-023-06825-8).
2. About 17% of the world's oil tankers are estimated to be shadow fleet, Dec 2024 [Atlantic Council](https://www.atlanticcouncil.org/in-depth-research-reports/report/the-threats-posed-by-the-global-shadow-fleet-and-how-to-stop-it/). Definition varies.
3. Dark STS vessels were sanctioned within a year 32.1% of the time, versus 80% for AIS spoofers [S, vendor][Kpler](https://www.kpler.com/zh/blog/dark-sts-transfers-compliance-risk). Also: IUU fishing losses of $10-23.5 bn a year [Agnew 2009, dated](https://pmc.ncbi.nlm.nih.gov/articles/PMC2646833).

## Against / risks
- **Geometry.** GFW's "likely disabling" rule needs 50 nm offshore. Much of the Gulf of Finland is probably closer than that [U, check the map], so GFW's own filter would drop most Baltic gaps. Use the raw 6 h gap rule and say so.
- **Spoofing.** Baltic spoofing (Green Admire) means a "prism inconsistent" result can be spoofing, not a gap.
- **SAR misses small boats.** Absence of a detection proves nothing (see Paolo et al.).
- **Licence.** Non-commercial data; pitch and demo must match.
- **Unverified.** Eagle S IMO conflict; Starboard method; speed figures; xView3 matching details.

## Skills
Not swept (outside this brief). Loadout: `~/.claude/skills/research-first/SKILL.md`.
