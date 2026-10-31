# Independent check: build the list first, then add it up from the end.
def solve(data: str) -> str:
    values = [int(x) for x in data.split()[1:]]
    total = 0
    while values:
        total += values.pop()
    return f"{total}\n"
