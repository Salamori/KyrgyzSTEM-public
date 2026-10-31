import sys


def solve(data: str) -> str:
    it = iter(data.split())
    n = int(next(it))
    total = 0
    for _ in range(n):
        total += int(next(it))
    return f"{total}\n"


if __name__ == "__main__":
    sys.stdout.write(solve(sys.stdin.read()))
