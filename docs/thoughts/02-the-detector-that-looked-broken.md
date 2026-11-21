# The detector that looked broken

I wanted to know whether my "went dark" rule could find a ship that stopped talking. So I hid stretches of real ships' tracks and asked the rule to find them.

The tracks were my own: 271,535 positions my recorder had heard in the Baltic. I cut 512 random stretches of one to six hours out of 200 ships' tracks. The ping before each cut and the ping after it stayed visible, and every ping inside was hidden truth. Then I ran the rule on what was left and counted how many of the gaps I hid it found. That count is the recall, and in plain terms it is just that: how many of the gaps I hid it found.

It found 43. That is 8.4%. I thought the rule was broken.

Then I read what the rule actually claims. It only reports a silence that begins while a ship is moving, at 2 knots or more. A moored ship going quiet is not news; it is a ship at a berth. Of my 512 cuts, only 83 began while the ship was underway. The rule found 43 of those 83. Inside its own scope it was about half, not 8.4%.

Half still looked low, so I traced every miss. I ran it again on the recording as it stood later and got 92 gaps that began while a ship was moving: 55 found, 37 missed. Every one of the 37 fell in minutes when my own recorder heard nothing at all, at start-up and after a restart. The rule was not failing to see a dark ship. It was declining to call a ship dark during my own outage. Where the receivers stayed up for the whole gap, it found 55 of 55. Counts move slightly between runs because the recording keeps growing, so the two runs do not have to agree.

Two things come out of this:

- A recall number without its scope means nothing.
- The most important input to a surveillance rule is knowing when I myself was blind.

The first is not a quibble. 8.4% and 55 of 55 describe the same rule on the same water. The only difference is whether I asked it to find things it never claimed. A number quoted without its scope can make a sound rule look broken, or a weak one look fine, and the reader cannot tell which one they are looking at.

The second is the part I did not expect. A gap in the data has two possible authors: the ship, or me. The rule cannot tell them apart from the gap alone, so it has to read its own listening. Mine does, and the answer was that all of my misses were my silence, not the ship's.

I read the first number as a verdict on the rule when it was a verdict on my question. That is an easy mistake, and a common one.

The point is not about ships. Any system that flags people, whether it matches a face, a car near a border or a name on a watchlist, is reading a silence in data it did not fully collect. If it does not log when its own sensors were down, it will fill that silence with a guess, and the guess will land on a person. A system that accuses has to keep a record of its own blind spots and refuse to infer from them. For me, evidence before accusation now means one concrete thing: the rule has to say, inside the data, when it was not watching.
