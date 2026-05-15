from __future__ import annotations

from typing import List, Optional


def integer_partitions_nonincreasing(n: int, max_part: Optional[int] = None) -> List[List[int]]:
    """Generate non-increasing integer partitions of n.

    Example n=4:
        [4], [3,1], [2,2], [2,1,1], [1,1,1,1]
    """
    if n == 0:
        return [[]]
    if n < 0:
        return []
    if max_part is None:
        max_part = n
    out: List[List[int]] = []
    for first in range(min(max_part, n), 0, -1):
        for rest in integer_partitions_nonincreasing(n - first, first):
            out.append([first] + rest)
    return out


def integer_compositions(n: int) -> List[List[int]]:
    """Generate all ordered positive integer compositions of n."""
    if n == 0:
        return [[]]
    if n < 0:
        return []
    out: List[List[int]] = []
    for first in range(1, n + 1):
        for rest in integer_compositions(n - first):
            out.append([first] + rest)
    return out


def validate_partition(partition: List[int], rank: int) -> List[int]:
    """Validate and normalize a CW layer partition."""
    p = [int(x) for x in partition]
    if not p:
        raise ValueError("partition 不能为空")
    if any(x <= 0 for x in p):
        raise ValueError(f"partition 中每个值必须为正整数，当前 {partition}")
    if sum(p) != int(rank):
        raise ValueError(f"partition 之和必须等于 rank={rank}，当前 {partition} sum={sum(p)}")
    return p


def partition_to_layer_groups(partition: List[int]) -> List[List[int]]:
    """Convert partition to contiguous layer groups.

    Example: [2,1,1] -> [[0,1], [2], [3]]
    """
    p = validate_partition(partition, sum(int(x) for x in partition))
    groups = []
    cursor = 0
    for size in p:
        groups.append(list(range(cursor, cursor + size)))
        cursor += size
    return groups
