"""Compare generated plans to the preserved historical corpus."""


def check(root):
    generated = {
        path.read_text(): path
        for path in (root / "runs/learn").glob("**/learn/*/*.yaml")
    }
    missing = 0
    for path in sorted((root / "fixtures/reproductions").glob("*.yaml")):
        if "indirect" in path.name:
            continue
        match = generated.get(path.read_text())
        if match is None:
            print(f"{path}: no matching learned plan")
            missing += 1
        else:
            print(f"{path} <= {match}")
    return 1 if missing else 0
