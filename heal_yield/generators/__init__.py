"""Generators that satisfy the subprocess contract in `heal_yield.generator`.

`anthropic_gen` is the reference loop's generator and costs real money.
`stub_gen` is deterministic, offline and free; the whole test suite runs on it,
which is how the harness can be exercised end to end inside the constraint it
sells.
"""
