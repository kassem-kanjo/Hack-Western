# Risk register

| # | Risk | Likelihood | Impact | Mitigation | Owner / trigger |
|---|---|---|---|---|---|
| R1 | GFW outage spreads to events/SAR during the demo | M | H | Everything ingested is disk-cached; snapshot mode serves frozen JSON with no network | Chief; snapshot built by CP3 |
| R2 | Recorder stops (laptop closed, network) → holes in Baltic data | H | M | Coverage-aware gap detection (#19); move recorder to Vultr VM (#15); health loop checks every 30 min | Kassem signs up to Vultr Sat AM |
| R3 | Venue wifi fails at the table | M | H | Demo runs from snapshot on localhost | CP3 rehearsal offline |
| R4 | Claude tokens run out | M | H | DeepSeek coders do the code; Haiku leads; Opus only at checkpoints | every brief |
| R5 | OpenCode quota (5 h cap) hit | M | M | Max 2 coders per lead; Sonnet leads fall back until reset | Builder reports `provider.quota` |
| R6 | False positives look silly to judges (gear tags, oil-field boats) | H | M | Filters (gear type, stationary off≈on, MMSI blocks) + caveats panel on every case | #25 |
| R7 | Scope creep | H | H | Cut order in plan.md; CP2 decides | Chief at CP2 |
| R8 | No QNX Pi | H | L | Edge runs on laptop; QNX entry only if Pi | Kassem asks around Sat AM |
| R9 | A number in the pitch can't be reproduced | L | H | Every number comes from a script whose output is committed (docs/) | Chief before submit |
| R10 | Keys leak (public repo) | L | H | .env gitignored; keys only server-side; `/security-review` before deploy | Chief |
