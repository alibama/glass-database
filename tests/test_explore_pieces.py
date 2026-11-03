"""The Pieces view's filters: by maker, by a search, by one piece's link (explore/pieces.py)."""
from explore.pieces import filter_pieces, makers_of

ROWS = [
    {"_row_id": "p1", "title": "Amber Goblet", "maker": "Rae Sutter", "year": "2024", "techniques": "Blown"},
    {"_row_id": "p2", "title": "Lone Bowl", "maker": "rae sutter", "year": "2023", "techniques": "Fused"},
    {"_row_id": "p3", "title": "Plate", "maker": "Theo Park", "year": "2025", "techniques": "Murrine",
     "description": "a plate of murrine"},
    {"_row_id": "p4", "title": "Nameless", "maker": "", "year": ""},
]


def test_makers_and_filters():
    assert makers_of(ROWS) == ["Rae Sutter", "Theo Park"]                     # one spelling, most pieces first
    assert [r["_row_id"] for r in filter_pieces(ROWS, maker="RAE SUTTER")] == ["p1", "p2"]
    assert [r["_row_id"] for r in filter_pieces(ROWS, q="murrine")] == ["p3"]
    assert [r["_row_id"] for r in filter_pieces(ROWS, piece="p4")] == ["p4"]
    assert filter_pieces(ROWS, q="bowl", maker="Theo Park") == []
    assert filter_pieces(ROWS) == ROWS and filter_pieces(ROWS, q="  ") == ROWS
