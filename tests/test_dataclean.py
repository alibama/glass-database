"""Explore data hygiene: geo/id columns excluded, outliers trimmed."""
import pandas as pd

from explore.dataclean import classify, clean_numeric, is_excluded


def test_geo_and_id_columns_excluded():
    assert is_excluded("lat") and is_excluded("lng")
    assert is_excluded("latitude") and is_excluded("longitude")
    assert is_excluded("id") and is_excluded("studio_id") and is_excluded("created_timestamp")
    assert not is_excluded("country")


def test_classify_keeps_categoricals_drops_coordinates():
    df = pd.DataFrame({
        "country": ["USA", "Italy", "USA", "UK", "Italy"],
        "type": ["Hot shop", "Studio", "Hot shop", "Kiln", "Studio"],
        "lat": [38.0, 45.4, 45.5, 51.4, 41.9],
        "lng": [-78.7, 12.3, -122.6, -2.5, 12.4],
    })
    cats, nums = classify(df)
    assert "country" in cats and "type" in cats
    assert "lat" not in nums and "lng" not in nums   # coordinates never chartable


def test_clean_numeric_trims_year_outliers():
    # realistic birth-year column with a couple of typo outliers
    years = list(range(1900, 1996)) + [80, 2958]
    s, is_year = clean_numeric(pd.Series(years))
    assert is_year
    assert s.min() >= 1850 and s.max() <= 2026
    assert 80 not in set(s) and 2958 not in set(s)


def test_clean_numeric_percentile_clip_for_nonyears():
    vals = list(range(0, 100)) + [100000]   # one huge outlier
    s, is_year = clean_numeric(pd.Series(vals))
    assert not is_year
    assert s.max() < 100000
