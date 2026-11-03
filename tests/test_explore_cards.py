"""The Directory as cards: the pure helpers behind the explorer's card rows, filters and
search (explore/cards.py imports without Streamlit)."""
import pandas as pd

from explore import cards


def _artists():
    return pd.DataFrame([
        {"_row_id": "a1", "artist_name": "Rae Sutter", "based_in": "Crozet, VA", "primary_technique": "Blown / cane",
         "birth_year": "1984", "website": "https://example.com/rae", "phone": "555-0100"},
        {"_row_id": "a2", "artist_name": "Theo Park", "based_in": "Norfolk, VA", "primary_technique": "Kiln-formed",
         "birth_year": "", "website": "", "phone": ""},
        {"_row_id": "a3", "artist_name": "Bea Lindqvist", "based_in": "Bristol, UK", "primary_technique": "Blown / cane",
         "birth_year": "1990", "website": "www.bea.example", "phone": ""},
    ])


def test_title_kind_place_and_fields():
    row = _artists().iloc[0].to_dict()
    assert cards.title_of(row) == "Rae Sutter" and cards.kind_word("artists") == "artist"
    assert cards.kind_word("studios") == "studio" and cards.kind_word("opportunities") == "opportunity"
    assert cards.kind_word("museum_artists") == "museum artist" and cards.kind_word("open_calls") == "open call"
    # a museum's artist list names the artist, not the museum, even when the museum column comes first
    assert cards.title_of({"museum": "Corning Museum of Glass", "artist": "Nicole Chesney"}) == "Nicole Chesney"
    assert cards.place_of(row) == "Crozet, VA"
    labels = {"based_in": "Based in", "primary_technique": "Technique", "birth_year": "Born", "website": "Website"}
    fields = cards.pick_fields(row, labels, hidden=["birth_year"])
    cols = [c for c, _, _ in fields]
    assert cols[:2] == ["based_in", "primary_technique"] and "birth_year" not in cols and "_row_id" not in cols
    assert "website" not in cols                                   # the website is a button, not a field
    assert ("phone", "phone", "555-0100") in fields
    assert cards.title_of({"_row_id": "x", "url": "https://x.example", "notes": "a note"}) == "a note"
    assert cards.title_of({"_row_id": "x"}) == "Untitled"
    long = {"name": "Long", "description": "word " * 60}
    assert cards.pick_fields(long, {})[0][2].endswith("…")


def test_links_and_card_html_escape_and_point_at_the_record():
    row = _artists().iloc[0].to_dict()
    links = cards.links_of(row, "artists")
    assert links[0] == ("Record", "/record/gdb:artists:a1", True)
    assert ("Maker card", "/maker.html?m=gdb%3Aartists%3Aa1", False) in links
    assert any(label == "Website" and href == "https://example.com/rae" for label, href, _ in links)
    studio = {"_row_id": "s1", "name": "Blue Ridge Hot Glass", "city": "Crozet", "country": "USA", "lat": "38", "lng": "-78"}
    slinks = dict((label, href) for label, href, _ in cards.links_of(studio, "studios"))
    assert slinks["Record"] == "/record/gdb:studios:s1" and slinks["Map"].startswith("/studios.html?q=Blue")
    html = cards.card_html({"_row_id": "e1", "name": "<b>Evil</b>", "city": "A & B", "website": "www.bea.example"}, "studios", {})
    assert "&lt;b&gt;Evil&lt;/b&gt;" in html and "A &amp; B" in html and "<b>Evil" not in html
    assert 'href="https://www.bea.example"' in html and "gdb-card" in html and "/record/gdb:studios:e1" in html
    assert "lat" not in cards.card_html(studio, "studios", {})           # coordinates stay on the map
    rendered = cards.card_html(row, "artists", {"based_in": "Based in"}, hidden=["birth_year"])
    assert "Rae Sutter" in rendered and "1984" not in rendered and "Based in" in rendered


def test_facets_and_search():
    df = _artists()
    cats = ["artist_name", "based_in", "primary_technique", "birth_year"]
    facets = cards.facet_columns(df, cats)
    assert "artist_name" not in facets and "birth_year" not in facets
    assert facets[0] == "primary_technique" and "based_in" in facets          # preferred names first
    assert cards.facet_columns(df, ["phone"]) == []                            # one distinct value is no filter
    assert list(df[cards.matches(df, "crozet")]["artist_name"]) == ["Rae Sutter"]
    assert list(df[cards.matches(df, "BLOWN")]["artist_name"]) == ["Rae Sutter", "Bea Lindqvist"]
    assert cards.matches(df, "").all() and not cards.matches(df, "nothing here").any()
    assert cards.PAGE == 12 and cards.PER_ROW == 3 and "<style>" in cards.CSS


def test_facets_skip_demographics_and_rank_by_meaning():
    focus = "primary_focus_what_is_your_primary_glass_discipline"
    df = pd.DataFrame([
        {"_row_id": "1", "artist_name": "A", "what_s_your_ethnicity": "x", "status_did_you": "Active", focus: "Blown"},
        {"_row_id": "2", "artist_name": "B", "what_s_your_ethnicity": "y", "status_did_you": "Retired", focus: "Kiln"},
    ])
    labels = {"what_s_your_ethnicity": "What's your Ethnicity?", "status_did_you": "Status (Did you…?)",
              focus: "Primary Focus What is your primary glass discipline?"}
    facets = cards.facet_columns(df, ["artist_name", "what_s_your_ethnicity", "status_did_you", focus], labels=labels)
    assert "what_s_your_ethnicity" not in facets and "artist_name" not in facets
    assert facets == [focus, "status_did_you"]                                  # a discipline leads a status
    fields = cards.pick_fields(df.iloc[0].to_dict(), labels)
    assert [c for c, _, _ in fields] == ["status_did_you", focus]                # ethnicity is not on the card


def test_a_technique_grid_is_one_field_one_filter_and_searchable():
    from explore.dataclean import labelled_columns
    cols = [("artist_name", "Artist Name"), ("based_in", "Based In"),
            ("hot_offhand", "Hot Glass & Flameworking Techniques [Offhand Blown Glass]"),
            ("hot_cane", "Hot Glass & Flameworking Techniques [Cane & Murrine]"),
            ("kiln_fusing", "Kiln, Cold Work & Architectural Techniques [Fusing & Slumping]")]
    lc = labelled_columns(cols)
    df = pd.DataFrame([
        {"_row_id": "1", "artist_name": "A", "based_in": "Crozet", "hot_offhand": "Primary Focus", "hot_cane": "Occasional Use",
         "kiln_fusing": ""},
        {"_row_id": "2", "artist_name": "B", "based_in": "Norfolk", "hot_offhand": "Secondary", "hot_cane": "", "kiln_fusing": ""},
        {"_row_id": "3", "artist_name": "C", "based_in": "Bristol", "hot_offhand": "", "hot_cane": "", "kiln_fusing": ""},
    ])
    fields = cards.pick_fields(df.iloc[0].to_dict(), lc.labels, groups=lc.groups)
    assert fields == [("based_in", "Based In", "Crozet"),
                      ("group:Techniques", "Techniques", "Offhand Blown Glass (primary), Cane & Murrine (occasional)")]
    html = cards.card_html(df.iloc[1].to_dict(), "artists", lc.labels, groups=lc.groups)
    assert "Offhand Blown Glass (secondary)" in html and "hot_offhand" not in html
    assert "Techniques" not in cards.card_html(df.iloc[2].to_dict(), "artists", lc.labels, groups=lc.groups)
    # the grid's columns never become filters of their own; the group offers its ticked rows, most ticked first
    assert cards.facet_columns(df, ["based_in", "hot_offhand", "hot_cane"], labels=lc.labels, skip=lc.grid_cols) == ["based_in"]
    assert cards.group_options(df, lc.groups["Techniques"]) == [("hot_offhand", "Offhand Blown Glass", 2),
                                                                ("hot_cane", "Cane & Murrine", 1)]
    assert list(df[cards.ticked(df["hot_offhand"])]["artist_name"]) == ["A", "B"]
    # "murrine" finds the person who ticked Cane & Murrine although the cell says "Occasional Use"
    text = cards.grid_text(df, lc.groups)
    assert list(df[cards.matches(df, "murrine", extra=text)]["artist_name"]) == ["A"]
    assert not cards.matches(df, "murrine").any()


def test_the_explorer_uses_the_cards():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "explore" / "app.py").read_text(encoding="utf-8")
    assert "from explore.cards import" in src and "card_html(" in src and "facet_columns(" in src
    assert src.index("card_html(") < src.index("st.dataframe(")                # cards first, the table in an expander
    assert "Show " in src and "st.session_state" in src
