from datetime import date

from newsletter_podcast.main import recent_sunday


def test_sunday_run_uses_itself():
    assert recent_sunday(date(2026, 10, 25)) == date(2026, 10, 25)


def test_monday_retry_shares_the_sunday_key():
    assert recent_sunday(date(2026, 10, 26)) == date(2026, 10, 25)


def test_midweek_run_maps_back_to_that_weeks_sunday():
    assert recent_sunday(date(2026, 10, 28)) == date(2026, 10, 25)
    assert recent_sunday(date(2026, 10, 31)) == date(2026, 10, 25)


def test_next_sunday_is_a_new_window():
    assert recent_sunday(date(2026, 11, 1)) == date(2026, 11, 1)
