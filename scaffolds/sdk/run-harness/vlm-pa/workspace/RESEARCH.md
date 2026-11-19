# Research question

Can a vision-language model predict whether it will answer a question correctly
before it generates an answer? How does this task differ from verifying the
correctness of an already generated answer?

# Context

Vision-language models (VLMs) often answer visual questions incorrectly, and a system
that could flag likely failures would be useful for deciding when to abstain, defer
to a stronger model, or ask for human review. Failure can be anticipated in two ways:
by judging a candidate answer after it has been generated, or by judging the question
before any answer exists. The latter is a judgment about the model itself rather than
about a particular answer, and it must be made from the image and question alone.
This also separates it from calibration, which evaluates the confidence attached to
an answer the model has already produced. A successful project designs and runs
experiments that answer both parts of the research question with evidence. Success
means a clearly specified methodology, results on appropriate baselines and metrics,
and conclusions stated only as far as the evidence supports.
