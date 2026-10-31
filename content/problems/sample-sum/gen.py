import random


def _make(rng: random.Random, n: int, max_v: int) -> str:
    return f"{n}\n" + " ".join(str(rng.randint(0, max_v)) for _ in range(n)) + "\n"


def tests() -> list[tuple[int, str]]:
    rng = random.Random(7)
    cases = ["1\n0\n", "1\n1000\n", "1000\n" + " ".join(["1000"] * 1000) + "\n"]
    cases += [_make(rng, rng.randint(1, 1000), 1000) for _ in range(5)]
    return [(1, case) for case in cases]


def random_small(rng: random.Random) -> str:
    return _make(rng, rng.randint(1, 8), 10)


def validate(data: str, group: int) -> None:
    lines = data.strip().splitlines()
    n = int(lines[0])
    values = list(map(int, lines[1].split()))
    assert 1 <= n <= 1000 and len(values) == n and len(lines) == 2
    assert all(0 <= v <= 1000 for v in values)
