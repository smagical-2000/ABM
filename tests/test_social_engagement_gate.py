"""Competitor-engagement noise gate — pure per-person tally + threshold.

Rule: a competitor engager surfaces only on ANY comment OR >= MIN likes. Magical's
own posts are exempt (the caller never runs this gate for them).
"""

from types import SimpleNamespace

from auto_search.social import engagement_gate
from auto_search.social.engagement_gate import EngagementTally


def _like(url):
    return SimpleNamespace(linkedin_url=url, comment_text=None)


def _comment(url, text="we looked at this"):
    return SimpleNamespace(linkedin_url=url, comment_text=text)


def test_tally_counts_likes_and_comments_per_person():
    t = engagement_gate.tally_by_person([
        _like("/in/a"), _like("/in/a"), _comment("/in/a"),   # a: 2 likes + 1 comment
        _like("/in/b"),                                       # b: 1 like
    ])
    assert t["/in/a"] == EngagementTally(likes=2, comments=1)
    assert t["/in/b"] == EngagementTally(likes=1, comments=0)


def test_tally_normalizes_url_and_drops_blank():
    t = engagement_gate.tally_by_person([_like("/IN/A "), _like(""), _like(None)])
    assert set(t) == {"/in/a"} and t["/in/a"].likes == 1


def test_comment_alone_qualifies():
    assert EngagementTally(likes=0, comments=1).qualifies


def test_two_likes_qualify_one_does_not():
    assert EngagementTally(likes=2, comments=0).qualifies
    assert not EngagementTally(likes=1, comments=0).qualifies


def test_competitor_engager_qualifies_helper():
    assert engagement_gate.competitor_engager_qualifies(EngagementTally(comments=1))
    assert engagement_gate.competitor_engager_qualifies(EngagementTally(likes=2))
    assert not engagement_gate.competitor_engager_qualifies(EngagementTally(likes=1))
    assert not engagement_gate.competitor_engager_qualifies(None)


def test_threshold_is_env_tunable(monkeypatch):
    monkeypatch.setattr(engagement_gate, "MIN_COMPETITOR_LIKES", 3)
    assert not EngagementTally(likes=2, comments=0).qualifies
    assert EngagementTally(likes=3, comments=0).qualifies
