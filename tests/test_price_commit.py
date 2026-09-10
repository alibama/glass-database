"""Confidential-but-assertable pricing commitments (prototype)."""
import secrets

from central import price_commit as pc


def test_hash_commit_hides_and_binds():
    c = pc.hash_commit(4200, "USD")
    assert pc.hash_verify(c["commitment"], 4200, "USD", c["salt"]) is True
    assert pc.hash_verify(c["commitment"], 9999, "USD", c["salt"]) is False   # can't change the number
    assert pc.hash_verify(c["commitment"], 4200, "EUR", c["salt"]) is False   # currency bound too


def test_pedersen_commit_verifies():
    c = pc.pedersen_commit(150000)
    assert pc.pedersen_verify(c["commitment"], 150000, int(c["r"], 16)) is True
    assert pc.pedersen_verify(c["commitment"], 150001, int(c["r"], 16)) is False


def test_pedersen_homomorphic_total_hides_parts():
    prices = [420000, 1500000, 80000]
    rs = [secrets.randbelow(pc._Q) for _ in prices]
    coms = [pc.pedersen_commit(m, r) for m, r in zip(prices, rs)]
    sumC = pc.pedersen_sum([c["commitment"] for c in coms])
    assert pc.verify_total(sumC, sum(prices), sum(rs)) is True      # total proven
    assert pc.verify_total(sumC, sum(prices) + 1, sum(rs)) is False  # wrong total rejected


def test_assertion_carries_no_prices():
    coms = [pc.hash_commit(4200, "USD"), pc.hash_commit(9000, "USD")]
    a = pc.assertion(coms)
    assert a["label"] == "org.glassdatabase.price-commitment"
    assert "4200" not in str(a) and "9000" not in str(a)            # only commitments, no prices
