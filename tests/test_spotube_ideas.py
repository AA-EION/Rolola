"""Tests for the features adapted from Spotube: matching, LRC parsing, links."""

from rolola.lyrics import parse_lrc
from rolola.matcher import norm, rank, score
from rolola.models import Track
from rolola.providers.spotify_links import parse_link


def T(title, artist=None, duration=None, source="youtube", id=None):
    return Track(source=source, id=id or title, title=title, artist=artist, duration=duration)


WANTED = T("Around the World", "Daft Punk", 429, source="spotify_links", id="x")


def test_official_audio_with_right_duration_wins():
    candidates = [
        T("Daft Punk - Around The World (Live 1997)", "SomeFan", 512),
        T("Around the World - Karaoke Version", "Karaoke Hits", 430),
        T("Daft Punk - Around the World (Official Audio)", "Daft Punk", 430, id="good"),
        T("Daft Punk - Around The World (Official Music Video Remastered)", "Daft Punk", 242),
    ]
    best = rank(candidates, WANTED)[0][1]
    assert best.id == "good"


def test_topic_channel_and_local_bonus():
    topic = T("Around the World", "Daft Punk - Topic", 429, id="topic")
    random_upload = T("around the world daft punk", "uploader123", 429, id="rnd")
    assert score(topic, WANTED) > score(random_upload, WANTED)


def test_unrelated_song_scores_low():
    assert score(T("Get Lucky (Official Audio)", "Daft Punk", 369), WANTED) < 3


def test_variant_requested_is_not_penalised():
    wanted = T("Around the World (Live)", "Daft Punk", 500, source="spotify_links")
    live = T("Daft Punk - Around the World (Live)", "Daft Punk", 502)
    assert score(live, wanted) >= 4


def test_norm_strips_feat_and_brackets():
    assert norm("Señorita (feat. Someone) [Remastered 2011]") == "senorita"


def test_parse_lrc_multi_timestamps_sorted():
    lines = parse_lrc("[00:12.50]hello\n[00:05.00][01:00.00]chorus\nnot a lyric line\n")
    assert [l["t"] for l in lines] == [5.0, 12.5, 60.0]
    assert lines[0]["text"] == "chorus"


def test_spotify_link_parsing():
    assert parse_link("https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC?si=abc") == ("track", "4uLU6hMCjMI75M1A2tKUQC")
    assert parse_link("https://open.spotify.com/intl-es/playlist/37i9dQZF1DXcBWIGoYBM5M") == ("playlist", "37i9dQZF1DXcBWIGoYBM5M")
    assert parse_link("spotify:album:1ATL5GLyefJaxhQzSPVrLX") == ("album", "1ATL5GLyefJaxhQzSPVrLX")
    assert parse_link("daft punk") is None
