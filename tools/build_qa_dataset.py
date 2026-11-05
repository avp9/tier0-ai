
import json
import re
from pathlib import Path

from datasets import load_dataset

DATASETS = [
    "strova-ai/hr-policies-qa-dataset",
    "EmbraceCoder/HR_Policy",
    "EmbraceCoder/New_Hr_Policy",
    "SaumyaGupta/hr-policies-qa-dataset",
    "Console-AI/IT-helpdesk-synthetic-tickets",
    "KameronB/synthetic-it-callcenter-tickets",
    "parth1609/IT_ticket_dataset",
    "interneuronai/it_support_ticket_classification_pegasus_dataset",
    "w1z4rd3k/it-support-l1-ticket-classification",
]

QUESTION_KEYS = [
    "question", "user_message", "query", "prompt",
    "short_description", "issue/request", "body",
    "text", "ticket_description", "description", "content",
]

ANSWER_KEYS = [
    "answer", "expected_response", "response",
    "resolution", "resolution_steps", "close_notes",
    "output", "assistant_answer", "solution",
]

CATEGORY_KEYS = [
    "category", "department",
    "label", "ticket_category", "assignment_group",
]

SUBCATEGORY_KEYS = ["subcategory"]

RESOLVED_AT = [
    "resolved_at",
]

def clean(value):
    """Convert supported values into readable text."""
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(
            clean(item) for item in value if clean(item)
        )
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return re.sub(r"\s+", " ", str(value)).strip()


def get_value(row, keys):
    """Return the first nonempty matching field."""
    normalized = {
        str(key).lower().strip(): value
        for key, value in row.items()
    }
    for key in keys:
        value = clean(normalized.get(key.lower()))
        if value:
            return value
    return ""



def get_category(row, source):

    # if category:
    #     return category

    name = source.lower()
    if "hr" in name or "policy" in name:
        return "HR Policy"

    return "IT Support"

def get_subcategory(row, source):
    category = get_value(row, CATEGORY_KEYS)
    subcategory = get_value(row, SUBCATEGORY_KEYS)

    return f"{category} {subcategory}"

def get_resolution_time(row, source):
    resolution = get_value(row, RESOLVED_AT)
    if resolution:
        return resolution
    return None

def extract_messages(row, source):
    """Extract conversational pairs when messages are available."""
    messages = row.get("messages")

    if isinstance(messages, list):
        pairs = []
        pending_question = ""

        for message in messages:
            if not isinstance(message, dict):
                continue

            role = str(message.get("role", "")).lower()
            content = clean(message.get("content"))

            if role in ("user", "human"):
                pending_question = content
            elif role in ("assistant", "bot") and pending_question:
                if content:
                    pairs.append((pending_question, content))
                pending_question = ""

        if pairs:
            return pairs

    # Handle text-based conversations such as
    # "<human>: ... <bot>: ..."
    text = get_value(row, ["text", "conversation", "dialogue"])
    if text and "<human>:" in text and "<bot>:" in text:
        matches = re.findall(
            r"<human>:\s*(.*?)\s*<bot>:\s*(.*?)(?=<human>:|$)",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if matches:
            return [
                (clean(q), clean(a))
                for q, a in matches
                if clean(q) and clean(a)
            ]

    return []


def convert_row(row, source, tick_num):
    category = get_category(row, source)
    subcategory = get_subcategory(row, source)
    resolution_time = get_resolution_time(row, source)
    ticketid = f"TICKET_{tick_num}"

    # Prefer explicit conversation pairs.
    pairs = extract_messages(row, source)
    if pairs:
        return [
            {
                "ticketid": ticketid,
                "question": question,
                "answer": answer,
                "category": category,
                "source": source,
                "resolution_time": resolution_time,
                "subcategory": subcategory,
            }
            for question, answer in pairs
            if question and answer
        ]

    question = get_value(row, QUESTION_KEYS)
    answer = get_value(row, ANSWER_KEYS)

    # A classification dataset may not contain a response.
    # Preserve its label instead of inventing a solution.
    if not answer:
        label = get_value(row, CATEGORY_KEYS)
        if label and question:
            answer = f"Ticket category: {label}."
        else:
            return []

    if not question:
        return []

    return [{
        "ticketid": ticketid,
        "question": question,
        "answer": answer,
        "category": category,
        "source": source,
        "resolution_time": resolution_time,
        "subcategory": subcategory,
    }]


def load_splits(dataset_id):
    """Load available splits, with a train-split fallback."""
    dataset = load_dataset(dataset_id)

    if hasattr(dataset, "keys"):
        splits = list(dataset.keys())
        preferred = [
            split for split in ("train", "validation", "test")
            if split in splits
        ]
        return [(name, dataset[name]) for name in (preferred or splits)]

    return [("train", dataset)]


def main():
    records = []
    seen = set()
    failures = []
    tick_num = 0

    for dataset_id in DATASETS:
        print(f"Loading {dataset_id}...")

        try:
            splits = load_splits(dataset_id)

            for split_name, dataset in splits:
                for row in dataset:
                    tick_num += 1
                    for item in convert_row(row, dataset_id, tick_num):
                        key = (
                            item["question"].casefold(),
                            item["answer"].casefold(),
                            item["category"].casefold(),
                        )

                        if key not in seen:
                            seen.add(key)
                            records.append(item)

            print(f"  Processed {dataset_id}")

        except Exception as exc:
            failures.append({
                "dataset": dataset_id,
                "error": str(exc),
            })
            print(f"  FAILED: {exc}")

    output = Path("qa_dataset.json")
    output.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\nOutput: {output.resolve()}")
    print(f"Total unique records: {len(records)}")
    print(f"Failed datasets: {len(failures)}")

    if failures:
        Path("dataset_errors.json").write_text(
            json.dumps(failures, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print("See dataset_errors.json for failed downloads.")


if __name__ == "__main__":
    main()
