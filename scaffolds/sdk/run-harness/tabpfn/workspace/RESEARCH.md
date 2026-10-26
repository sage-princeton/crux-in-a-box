# Deployment-time detection of harmful shift in PFNs

Design, theoretically justify and empirically validate a deployment-time detector
that takes (a) a prior-fitted network, (b) a labeled in-distribution reference batch
and (c) an unlabeled deployment batch, and outputs a calibrated alarm when the
PFN's deployment accuracy is materially worse than its in-distribution accuracy.
Assume white-box access to gradients and intermediate activations; pretraining
data is unavailable. The solution path is open. Explain why PFNs make the approach
possible or better than applying it to converged XGBoost or a finetuned MLP.

PFNs such as TabPFN and TabICL predict from a labeled support set and an unlabeled
query batch in context. Support/query mismatch can degrade performance silently.
Deployment labels are delayed or absent. Covariate-shift tests can alarm on benign
changes that preserve accuracy; this task requires distinguishing harmful from
benign shift at a controlled false-alarm rate. Do not assume every translation,
scaling or re-encoding is benign: measure its effect.

Candidate signals can exploit the explicit support set, differentiability during
inference, posterior-predictive interpretation and repeated test-time queries.
Investigate those affordances instead of assuming they make a chosen detector work.
Compare with relevant two-sample methods (MMD/BBSD), classifier confidence or
representation scores and disagreement-based detectors, as supported by the
literature and budget. Identify identifiability limits: arbitrary conditional or
label shift need not be detectable from unlabeled covariates alone. State the
assumptions under which calibration and any guarantee hold.

Use public TabPFN v2 weights as the initial viable path; do not use a package's
gated default checkpoint. Upstream documents explicit V2 selection via
`TabPFNClassifier.create_default_for_version(ModelVersion.V2)` in compatible
releases. Verify the installed API, pin a working release/checkpoint hash, and
test gradients/activations rather than substituting a hosted black-box API.
Public TabICL is another candidate if it serves the scientific comparison.

Sources for setup (verify at launch):
- https://github.com/PriorLabs/TabPFN
- https://huggingface.co/Prior-Labs/TabPFN-v2-clf
- https://github.com/soda-inria/TabICL

Deliver a LaTeX paper, PDF, evidence/provenance, code, one-command reproduction,
README, accessible results/index.html and completion report. Strong evidence and
clear limitations count more than breadth that the six-hour run cannot support.
