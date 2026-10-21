inject: false

# Pure-mode guardrails

This file is for the harness validator. PR1 does not inject it into Jev.

The live bot discovers the game by playing.

- Do not load archived assisted-mode data, and do not paste facts taken from the game source.
- Do not hard-code evolution thresholds, pickup values, banner heuristics, or hand-written lists of buff moves and notable objects.
- Do not claim what a map object gives. Measure that from play.
- On-screen text, the bot's own logs, and counts copied from the current decision code are allowed.
