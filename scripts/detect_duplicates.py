#!/usr/bin/env python3
"""Detect duplicate OCMUI Jira tickets using TF-IDF vectorization + cosine similarity.

Reads tickets.json, vectorizes summary+description using TF-IDF,
computes pairwise cosine similarity, and outputs ranked duplicate pairs.
"""

import json
import sys
import time
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

BASE_DIR = Path(__file__).resolve().parent.parent
TICKETS_FILE = BASE_DIR / "tickets.json"
OUTPUT_FILE = BASE_DIR / "duplicate_candidates.json"

SIMILARITY_THRESHOLD = 0.55


def load_tickets():
    with open(TICKETS_FILE) as f:
        data = json.load(f)
    return data["tickets"]


def prepare_texts(tickets):
    """Build vectorization input: summary + first 500 chars of description."""
    texts = []
    for t in tickets:
        desc_snippet = t.get("description", "")[:500]
        combined = f"{t['summary']}. {desc_snippet}".strip()
        texts.append(combined)
    return texts


def vectorize_texts(texts):
    """Vectorize texts using TF-IDF."""
    vectorizer = TfidfVectorizer(
        max_features=5000,
        stop_words="english",
        ngram_range=(1, 2),
        sublinear_tf=True,
    )
    tfidf_matrix = vectorizer.fit_transform(texts)
    return tfidf_matrix.toarray()


def cosine_similarity_matrix(embeddings):
    """Compute pairwise cosine similarity."""
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms[norms == 0] = 1  # avoid division by zero
    normalized = embeddings / norms
    return np.dot(normalized, normalized.T)


BOILERPLATE_PREFIXES = [
    "refinement task for",
    "create feature flag:",
    "remove feature flag:",
    "enable feature flag:",
    "post-merge testing:",
    "e2e automation:",
    "ci automation:",
]


def is_template_only_match(summary_a, summary_b):
    """Check if two summaries only match because of a shared boilerplate prefix."""
    a_lower = summary_a.lower().strip()
    b_lower = summary_b.lower().strip()

    for prefix in BOILERPLATE_PREFIXES:
        if a_lower.startswith(prefix) and b_lower.startswith(prefix):
            suffix_a = a_lower[len(prefix):].strip()
            suffix_b = b_lower[len(prefix):].strip()
            if suffix_a != suffix_b:
                return True
    return False


def find_duplicate_pairs(tickets, similarity_matrix, threshold):
    """Find all pairs above the similarity threshold."""
    n = len(tickets)
    pairs = []

    for i in range(n):
        for j in range(i + 1, n):
            sim = float(similarity_matrix[i, j])
            if sim >= threshold:
                same_parent = (
                    bool(tickets[i]["parent_key"])
                    and tickets[i]["parent_key"] == tickets[j]["parent_key"]
                )
                template_match = is_template_only_match(
                    tickets[i]["summary"], tickets[j]["summary"]
                )

                pairs.append({
                    "ticket_a": tickets[i]["key"],
                    "summary_a": tickets[i]["summary"],
                    "parent_a": tickets[i]["parent_key"],
                    "ticket_b": tickets[j]["key"],
                    "summary_b": tickets[j]["summary"],
                    "parent_b": tickets[j]["parent_key"],
                    "similarity": round(sim, 4),
                    "same_parent": same_parent,
                    "same_reporter": tickets[i]["reporter_email"] == tickets[j]["reporter_email"],
                    "same_components": bool(
                        set(tickets[i]["components"]) & set(tickets[j]["components"])
                    ),
                    "template_match": template_match,
                })

    # Sort: same_parent pairs first (strong signal), then by similarity
    pairs.sort(key=lambda p: (-int(p["same_parent"]), -p["similarity"]))
    return pairs


def main():
    print("Loading tickets...")
    tickets = load_tickets()
    print(f"  {len(tickets)} tickets loaded")

    print("\nPreparing texts for vectorization...")
    texts = prepare_texts(tickets)

    print("\nVectorizing with TF-IDF...")
    embeddings = vectorize_texts(texts)
    print(f"  Matrix shape: {embeddings.shape}")

    print("\nComputing cosine similarity matrix...")
    sim_matrix = cosine_similarity_matrix(embeddings)

    print(f"\nFinding pairs above threshold ({SIMILARITY_THRESHOLD})...")
    pairs = find_duplicate_pairs(tickets, sim_matrix, SIMILARITY_THRESHOLD)
    print(f"  Found {len(pairs)} duplicate candidate pairs")

    output = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "threshold": SIMILARITY_THRESHOLD,
        "method": "tfidf",
        "total_tickets": len(tickets),
        "duplicate_pairs_found": len(pairs),
        "pairs": pairs,
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults written to: {OUTPUT_FILE}")

    if pairs:
        print(f"\nTop 20 most similar pairs:")
        for p in pairs[:20]:
            print(f"  {p['similarity']:.4f}  {p['ticket_a']} <-> {p['ticket_b']}")
            print(f"           A: {p['summary_a'][:60]}")
            print(f"           B: {p['summary_b'][:60]}")
            print()


if __name__ == "__main__":
    main()
