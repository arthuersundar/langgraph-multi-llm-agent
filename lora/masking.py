"""Train only on what the assistant writes.

The loss should cover the assistant's tool calls and answers, not the system prompt, the user
question or the tool results (which are retrieved text and live data, not something to imitate).
We render the conversation with the model's own chat template, find the character span each
assistant turn occupies, and keep labels only for tokens inside those spans.

Pure functions: `render` and the token offsets are passed in, so the logic is tested offline
with a toy tokenizer (test_data.py) and run for real with a Hugging Face tokenizer (train.py).
"""
IGNORE = -100


def assistant_spans(messages: list[dict], render) -> tuple[str, list[tuple[int, int]]]:
    """render(messages, add_generation_prompt) -> str. Returns the full text and, for every
    assistant message, the (start, end) character span it occupies in that text. The span
    starts where the generation prompt ends, so it holds exactly what the model must produce,
    including the end-of-turn token."""
    full = render(messages, False)
    spans = []
    for i, m in enumerate(messages):
        if m["role"] != "assistant":
            continue
        prefix = render(messages[:i], True)
        upto = render(messages[: i + 1], False)
        if not upto.startswith(prefix):
            raise ValueError(f"message {i}: the template does not render the generation prompt as a prefix of the turn")
        if not full.startswith(upto):
            raise ValueError(f"message {i}: rendering a prefix of the chat differs from rendering the whole chat")
        spans.append((len(prefix), len(upto)))
    return full, spans


def build_labels(ids: list[int], offsets: list[tuple[int, int]], spans: list[tuple[int, int]]) -> list[int]:
    """Keep the token id where the token lies inside an assistant span, else IGNORE."""
    labels = []
    for tid, (a, b) in zip(ids, offsets):
        inside = b > a and any(a >= s and b <= e for s, e in spans)
        labels.append(tid if inside else IGNORE)
    return labels


def encode_example(messages, render, tokenizer, max_len: int):
    """Returns (input_ids, labels) or None when the example is longer than max_len
    (dropped, never truncated, so a half-written answer is never trained on)."""
    full, spans = assistant_spans(messages, render)
    enc = tokenizer(full, add_special_tokens=False, return_offsets_mapping=True)
    if len(enc["input_ids"]) > max_len:
        return None
    return enc["input_ids"], build_labels(enc["input_ids"], enc["offset_mapping"], spans)
