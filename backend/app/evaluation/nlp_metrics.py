import re
from collections import Counter


def tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold(), flags=re.UNICODE)


def token_f1(reference: str, prediction: str) -> float:
    reference_tokens = tokenize(reference)
    prediction_tokens = tokenize(prediction)
    if not reference_tokens or not prediction_tokens:
        return float(reference_tokens == prediction_tokens)

    overlap = sum((Counter(reference_tokens) & Counter(prediction_tokens)).values())
    precision = overlap / len(prediction_tokens)
    recall = overlap / len(reference_tokens)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def rouge_l(reference: str, prediction: str) -> float:
    reference_tokens = tokenize(reference)
    prediction_tokens = tokenize(prediction)
    if not reference_tokens or not prediction_tokens:
        return float(reference_tokens == prediction_tokens)

    previous = [0] * (len(prediction_tokens) + 1)
    for reference_token in reference_tokens:
        current = [0]
        for index, prediction_token in enumerate(prediction_tokens, start=1):
            if reference_token == prediction_token:
                current.append(previous[index - 1] + 1)
            else:
                current.append(max(previous[index], current[-1]))
        previous = current

    lcs_length = previous[-1]
    precision = lcs_length / len(prediction_tokens)
    recall = lcs_length / len(reference_tokens)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)
