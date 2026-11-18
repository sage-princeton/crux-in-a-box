# Research question

When test-time compute is scaled by sampling many independent answers from language
models and no verifier can check them, how should the samples be combined into a
single answer that beats majority vote on hard multiple-choice questions?

# Context

Test-time scaling samples many completions and aggregates them into a final answer.
On hard multiple-choice questions with no verifier, combining independent predictions
is the only path to improving over any single sample, and the standard recipe is
majority vote: pick the most popular option. Majority vote works when independent
errors cancel, but hard questions often produce shared misconceptions across model
families, so the most popular option can be wrong even when some samples are right.
The Surprisingly Popular (SP) algorithm of Prelec, Seung and McCoy (2017) also elicits
each respondent's prediction of how the crowd will answer and selects the option whose
actual share most exceeds its predicted share, letting a correct minority win when its
members expect most others to miss the answer. Treat SP as a starting point: its
optimality rests on assumptions about the crowd that a pool of language models need
not satisfy. At inference the aggregation may use only the independently generated
responses to the question (no labels, verifier, reward model, judging call or exchange
between samples); labels may be used for development and evaluation, kept separate
from what the aggregation sees at inference. Choose and justify your scope, explain
why your method should work, and decide what it would take to show convincingly that
it beats majority vote.
