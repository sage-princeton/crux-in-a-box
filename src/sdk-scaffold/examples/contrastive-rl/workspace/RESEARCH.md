# Research question

What is the correct geometry for representing complex reasoning tasks?

# Context

CRTR is a method for solving complex reasoning tasks, based on representations
learnt through contrastive reinforcement learning. The representations that were
learnt so far are symmetric --- they are as though going forward in time has the
same probability as going backward in time. Based on this codebase, figure out a
good parametrization that would allow us to model the fact that these probabilities
aren't necessarily equal. When designing the method like that, we also need to have
an intuitive/mathematical/experimental justification for why a certain form should
be chosen over a different one, make sure to keep that in mind.

The accompanying CRTR repository in `crtr/` provides the reference implementation,
training/evaluation configurations and example checkpoints; use it as the starting
codebase and retain its original symmetric scorer as a baseline.
