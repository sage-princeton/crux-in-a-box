"""Label-free starting baselines; see RESEARCH.md for the multiclass SP caveat."""

import math
from collections import Counter


def aggregate(samples: list[dict], options: list[str]) -> dict[str, str]:
    if not samples or len(options) < 2 or len(set(options)) != len(options):
        raise ValueError("nonempty samples and distinct options required")
    for sample in samples:
        prediction = sample["prediction"]
        values = [*prediction.values(), sample["confidence"]]
        if (sample["answer"] not in options or set(prediction) != set(options)
                or any(isinstance(x, bool) or not isinstance(x, (int, float))
                       or not math.isfinite(x) or not 0 <= x <= 1 for x in values)
                or abs(sum(prediction.values()) - 1) > 1e-6):
            raise ValueError("invalid answer, confidence or crowd probability distribution")
    votes = Counter(sample["answer"] for sample in samples)
    observed = {a: votes[a] / len(samples) for a in options}
    predicted = {a: sum(s["prediction"][a] for s in samples) / len(samples) for a in options}

    def choose(scores):
        return min(options, key=lambda answer: (-scores[answer], answer))

    most_confident = min(enumerate(samples), key=lambda item: (-item[1]["confidence"], item[0]))[1]
    return {"majority": choose(observed),
            "sp_gap": choose({a: observed[a] - predicted[a] for a in options}),
            "sp_ratio": choose({a: observed[a] / max(predicted[a], 1e-12) for a in options}),
            "best_of_n_confidence": most_confident["answer"]}
