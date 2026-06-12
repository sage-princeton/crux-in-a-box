# BRIEF.md — The Task

Team: David Africa and Kozzy Voudouris, UK AISI

Paper Title: Persona Cartography: Charting Language Model Personality Traits in Weight Space

Research Question: Can large language model (LLM) personas be decomposed, measured, and controlled as positions in a structured "trait space" using weight-space interventions?

Relevant Context: LLMs exhibit stable behavioral patterns ("personas") that affect how they generalize, and these patterns are important for safety reasons. Current control methods are either brittle (prompting, steering) or expensive/inflexible (full retraining). We lack tools to decompose personas into independently controllable components, measure them rigorously, and compose them, except in the steering domain, which is flawed for a variety of reasons [Most uncertain about including this as scope.] The agent should produce (a) a method for inducing targeted behavioral shifts, (b) evidence about whether the induced dimensions are independent/composable, and (c) at least one test of whether these dimensions affect a downstream behavior the agent didn't directly train for.

Resources: We used open source models and generated data from publicly available papers and repos. Compute wasn't super intensive, we just used runpod. Training runs would bottleneck most of this, because we did a lot of training and iterations. Estimated time to completion of 2 weeks going full sprint.
