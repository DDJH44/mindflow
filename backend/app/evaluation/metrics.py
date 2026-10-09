def recall_at_k(
    retrieved_ids: list[int],
    expected_ids: list[int],
    k: int,
) -> float:
    if not expected_ids:
        return 0.0

    top_k = retrieved_ids[:k]

    hits = sum(
        1
        for expected_id in expected_ids
        if expected_id in top_k
    )

    return hits / len(expected_ids)


def reciprocal_rank(
    retrieved_ids: list[int],
    expected_ids: list[int],
) -> float:
    for rank, retrieved_id in enumerate(retrieved_ids, start=1):
        if retrieved_id in expected_ids:
            return 1.0 / rank

    return 0.0

def filter_accuracy(
    results: list[dict],
    project_id: int | None,
    document_type: str | None,
) -> float:
    if not results:
        return 1.0

    correct = 0

    for result in results:
        project_match = (
            project_id is None
            or result["project_id"] == project_id
        )

        document_type_match = (
            document_type is None
            or result["document_type"] == document_type
        )

        if project_match and document_type_match:
            correct += 1

    return correct / len(results)