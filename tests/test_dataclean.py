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


def test_person_identifying_columns():
    from explore.dataclean import is_dob, is_person_name, person_identifying
    assert is_person_name("artist_name") and is_person_name("artist") and is_person_name("led_by")
    assert is_person_name("mentor_name") and not is_person_name("name")        # studios keep their name
    assert not is_person_name("website") and not is_person_name("studio_name")
    assert is_dob("birth_year") and is_dob("dob") and is_dob("death_year") and is_dob("date_of_birth")
    assert not is_dob("founded") and not is_dob("year")
    names, dobs = person_identifying(["artist_name", "based_in", "birth_year", "website"])
    assert names == ["artist_name"] and dobs == ["birth_year"]
    # a form's "where were you born and where do you reside" is a place, not a date of birth
    assert is_dob("birth_year_what_year_were_you_born") and is_dob("year_of_death_are_you_still_alive")
    assert not is_dob("nationality_base_where_were_you_born_and_where_d")


def test_demographics_never_per_row():
    from explore.dataclean import is_demographic
    assert is_demographic("what_s_your_ethnicity") and is_demographic("gender") and is_demographic("age_range")
    assert is_demographic("q7", "What is your Gender?")                    # by the label when the column is opaque
    assert not is_demographic("stage_name") and not is_demographic("nationality_base")
    assert not is_demographic("primary_technique") and not is_demographic("based_in", "Based In")


def test_labels_read_as_nouns_not_questions():
    from explore.dataclean import clean_label
    cases = {
        ("what_s_your_ethnicity", "What's your Ethnicity?"): "Ethnicity",
        ("what_is_your_gender", "What is your Gender?"): "Gender",
        ("birth_year_what_year_were_you_born", "Birth Year (What year were you born?)"): "Birth Year",
        ("nationality_base_where_were_you_born_and_where_d",
         "Nationality / Base (Where were you born and where do you currently reside?)"): "Nationality / Base",
        ("year_of_death_are_you_still_alive", "Year of Death (are you still alive?)"): "Year of Death",
        ("primary_focus_what_is_your_primary_glass_discipline",
         "Primary Focus What is your primary glass discipline? Primary is defined by the discipline that "
         "makes up 50% or more of your work."): "Primary Focus",
        ("what_is_your_primary_glass_discipline",
         "What is your primary glass discipline? Primary is defined by the discipline."): "Primary focus",
        ("link_to_your_website_instagram_tiktok_or_other_s",
         "Link to your website (Instagram, Tiktok or other social)"): "Website",
        ("who_did_you_study_or_work_under_and_or_where_hav",
         "Who did you study or work under and/or where have you worked? (Is there someone you would call "
         "a mentor?)"): "Studied or worked under",
        ("primary_technique_medium_add_your_own",
         "Primary Technique / Medium\n\nAdd your own if your practice incorporates multiple techniques"):
            "Primary Technique / Medium",
        ("training_education", "Training / Education (Where did you study? Did you get a graduate or master's "
                               "degree? What"): "Training / Education",
        ("status_did_you_used_to_be_a_glass_maker", "Status (Did you used to be a glass maker?)"): "Status",
        ("roles", "Your role(s) in the glass field"): "Roles",
        ("based_in", "Based In"): "Based In",
        ("do_you_teach", "Do you teach?"): "Teaching",
        ("studio_name", ""): "Studio name",
    }
    for (col, label), want in cases.items():
        assert clean_label(col, label) == want, (col, label, clean_label(col, label))
