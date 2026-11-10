# CPU-only research question

On the public scikit-learn breast-cancer and wine classification datasets, does a
classifier's mean confidence detect harmful feature corruption more reliably than
a simple feature-distribution distance? Study standardized logistic regression;
optional random forests are a secondary comparison. Use fixed train/calibration/test
splits and multiple seeds. Include an unshifted control, increasing corruption of
informative features, and at least one plausibly harmless perturbation. Never give
test labels to a deployment-time detector; use them only to evaluate actual accuracy
drop. Calibrate any alarm threshold on held-out reference data.

Report detection vs actual accuracy degradation, false alarms on harmless shifts,
uncertainty across seeds, and failure cases. Retrieve a small number of relevant
primary papers and explain the limits of this toy study. This is a harness
compatibility study with a real reproducible CPU experiment, not evidence that
these findings transfer to TabPFN or contrastive RL. Use bundled datasets so network
availability cannot block the core experiment. Keep the first complete experiment
small enough to finish in two minutes; expand only after its output is verified.
