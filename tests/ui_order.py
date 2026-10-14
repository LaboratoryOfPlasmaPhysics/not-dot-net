"""Layout assertions for NiceGUI user-fixture tests."""


def drawn_before(a, b) -> bool:
    """Whether `a` sits in an earlier branch of the page than `b`."""
    def ancestors(el):
        chain = [el]
        while el.parent_slot is not None:
            el = el.parent_slot.parent
            chain.append(el)
        return chain

    chain_a, chain_b = ancestors(a), ancestors(b)
    common = next(el for el in chain_a if el in chain_b)
    branch_a = chain_a[chain_a.index(common) - 1]
    branch_b = chain_b[chain_b.index(common) - 1]
    return common.default_slot.children.index(branch_a) < common.default_slot.children.index(branch_b)
