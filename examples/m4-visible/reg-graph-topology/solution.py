def order(graph: dict[str, list[str]]) -> list[str]:
    nodes = sorted(set(graph).union(*(set(value) for value in graph.values())))
    incoming = {node: 0 for node in nodes}
    for dependencies in graph.values():
        for dependency in dependencies:
            incoming[dependency] += 1
    ready = [node for node in nodes if incoming[node] == 0]
    result: list[str] = []
    while ready:
        node = ready.pop(0)
        result.append(node)
        for dependency in graph.get(node, []):
            incoming[dependency] -= 1
            if incoming[dependency] == 0:
                ready.append(dependency)
                ready.sort()
    if len(result) != len(nodes):
        raise ValueError("cycle")
    return result
