# No better than chance

I built a finder for dark ships at a hackathon: vessels that switch off their position broadcast so nobody can follow them. Radar satellites still see them, so the idea looked simple. Find a ship that went quiet. Look at the water around it while it was quiet. If radar saw something there, that is your ship.

I tested whether that last step works. The test is the part I would keep.

The first test was a null test. I shuffled the times and checked whether the matches stayed just as common. For each silence I moved the search area 7 to 21 days forward or back, then counted the radar again: same water, same shape, same radar, wrong week. If the real count is no higher than the shifted counts, the radar was not telling me about the ship. It was telling me about that patch of ocean.

The real count was 108 matches against 100.5 expected from the shifted runs. The ratio was 1.07, with an interval of 0.91 to 1.25. That interval includes 1, so I cannot say radar near a dark ship beats chance. In the two Brazilian cells that held most of the hits, real and shifted counts were the same: traffic and platforms that sit there every week.

I had also built a second model, a Brownian bridge, that says where a ship most likely was between two fixes. It works as a locator: it put the true spot above a moved one 87.0% of the time. But when I scored radar cells in real gaps with it, the result matched the shifted gaps: ratio 1.03.

Then I checked the prism itself, the area a ship could have reached. I moved a known position 50 to 150 km in a random direction and asked how often it still landed inside. It did 98.4% of the time. So "inside the prism" carried almost no information on its own. A typical 40 hour silence covers hundreds of kilometres of ocean.

The worst moment was my best case. CL YICHUN looked like the perfect opener: dark for 22.9 hours off Guyana, with two candidates. Then a reviewer did the arithmetic. Reaching those dots needed an average of 18.4 knots, against 10.8 knots from the straight line, and the dots were only reachable in a 75-minute window that did not match the satellite pass. My showcase ship failed my own physics. A dramatic story is not evidence.

I could have kept the loose rule. Instead I built a tool that rules cases out, then recommends a closer look.

Of 5,624 gaps of 6 to 96 hours, 109 had an unmatched radar detection inside the ship's possible-reach area. Of those 109, 90 are cleared with a reason each: the ship never moved, the radar dot was outside its reach at the satellite's real pass time, no dot was reachable at the speed its ship type allows, or the only pass fell outside the gap. 19 remain unexplained. I would rather hand an analyst 19 open cases with 90 reasons than 109 alerts with none.

I trust the other direction too: claims that break physics. In one Baltic morning four unrelated ships each jumped 40 to 51 km off their route to the same point on land near St Petersburg, and back, at speeds no ship can reach. My first rule flagged 784 places, almost all ports. The rule I kept asks for an impossible jump in and out, and it left one point. That is a spoofed position that never happened, not a guess.

The tool I ended with checks every claim against arithmetic before it shows it. It says where a ship probably was, and when the evidence is no better than chance. It gives the reason a case was cleared, so an analyst can argue with the reason instead of trusting a score.

What I take from it:

- Use radar to open a question, not to answer it.
- Prefer physics, which can be checked, to a suggestive dot.
- Publish the null test next to the count.

What I keep thinking about is not ships. Any system that flags people or vessels makes the same bet: that a coincidence in the data means something. Radar near a dark ship is a coincidence until it survives a test. A face at a protest, a car near a border, a name on a list: each looks like signal in a busy enough dataset. The only way to tell is to break the link and see if the pattern survives.

A monitoring system that will not run its null test is asking to be believed. It should publish the number, the interval, and what it did when the number came back as 1.07.
