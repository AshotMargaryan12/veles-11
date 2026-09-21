"""Методы поиска каналов (раздел 3 ТЗ)."""

from __future__ import annotations

from conftest import FakeGateway, make_posts, make_snapshot
from tghunter.discovery import (
    candidates_from_posts,
    folder_candidates,
    keyword_candidates,
    manual_candidates,
    mention_candidates,
    similar_candidates,
)
from tghunter.discovery.manual import read_channel_list
from tghunter.models import Candidate, Post


def test_keyword_search_collects_and_dedups():
    gateway = FakeGateway(
        keyword_hits={
            "трейдинг": [Candidate(username="a", method="keywords"),
                         Candidate(username="b", method="keywords")],
            "крипта": [Candidate(username="b", method="keywords"),
                       Candidate(username="c", method="keywords")],
        }
    )
    found = keyword_candidates(gateway, ["трейдинг", "крипта", "  "])
    assert sorted(c.username for c in found) == ["a", "b", "c"]
    assert gateway.search_calls == ["keyword:трейдинг", "keyword:крипта"]


def test_similar_channels_from_seeds():
    gateway = FakeGateway(
        similar_hits={
            "seed_one": [Candidate(username="look1", method="similar"),
                         Candidate(username="look2", method="similar")],
            "seed_two": [Candidate(username="look2", method="similar")],
        }
    )
    found = similar_candidates(gateway, ["@seed_one", "https://t.me/seed_two", "@seed_one"])
    assert sorted(c.username for c in found) == ["look1", "look2"]
    assert all(c.method == "similar" for c in found)
    # каждый seed опрошен ровно один раз
    assert gateway.search_calls == ["similar:seed_one", "similar:seed_two"]


def test_similar_does_not_return_seed_itself():
    gateway = FakeGateway(
        similar_hits={"seed_one": [Candidate(username="seed_one", method="similar"),
                                   Candidate(username="new_one", method="similar")]}
    )
    found = similar_candidates(gateway, ["@seed_one"])
    assert [c.username for c in found] == ["new_one"]


def test_mentions_extracted_from_posts():
    posts = [
        Post(id=1, date=make_posts()[0].date, text="Читайте @partner_chan и t.me/another_one"),
        Post(id=2, date=make_posts()[0].date, text="Спам от @telegram"),
        Post(id=3, date=make_posts()[0].date, text="репост", forward_from_channel_id=777),
    ]
    snapshot = make_snapshot(username="source_chan", channel_id=1, posts=posts)
    found = candidates_from_posts(snapshot)

    usernames = {c.username for c in found if c.username}
    assert usernames == {"partner_chan", "another_one"}  # @telegram отфильтрован
    assert any(c.channel_id == 777 for c in found)
    assert all(c.source_channel == "@source_chan" for c in found)
    assert all(c.method == "mentions" for c in found)


def test_mentions_ignore_self_reference():
    posts = [Post(id=1, date=make_posts()[0].date, text="Подпишись на @self_chan")]
    snapshot = make_snapshot(username="self_chan", posts=posts)
    assert candidates_from_posts(snapshot) == []


def test_mention_graph_depth_one_does_not_expand():
    gateway = FakeGateway(
        mention_hits={
            "seed": [Candidate(username="level1", method="mentions")],
            "level1": [Candidate(username="level2", method="mentions")],
        }
    )
    found = mention_candidates(gateway, ["@seed"], depth=1)
    assert [c.username for c in found] == ["level1"]
    assert gateway.search_calls == ["mentions:seed"]


def test_mention_graph_depth_two_expands():
    gateway = FakeGateway(
        mention_hits={
            "seed": [Candidate(username="level1", method="mentions")],
            "level1": [Candidate(username="level2", method="mentions")],
        }
    )
    found = mention_candidates(gateway, ["@seed"], depth=2)
    assert sorted(c.username for c in found) == ["level1", "level2"]


def test_folder_candidates():
    url = "https://t.me/addlist/ABC"
    gateway = FakeGateway(folder_hits={url: [Candidate(username="in_folder", method="folders")]})
    found = folder_candidates(gateway, [url, "  ", "https://t.me/not_a_folder"])
    assert [c.username for c in found] == ["in_folder"]


def test_manual_list_from_plain_text(tmp_path):
    path = tmp_path / "list.txt"
    path.write_text(
        "# комментарий\n@one\nhttps://t.me/two\nt.me/three\n\nONE\n", encoding="utf-8"
    )
    assert read_channel_list(path) == ["one", "two", "three"]


def test_manual_list_from_csv(tmp_path):
    path = tmp_path / "crm.csv"
    path.write_text(
        "title,username,subs\nКанал А,@alpha,1000\nКанал Б,https://t.me/beta,2000\n",
        encoding="utf-8",
    )
    assert read_channel_list(path) == ["alpha", "beta"]


def test_manual_list_from_csv_with_link_column(tmp_path):
    path = tmp_path / "crm.csv"
    path.write_text("name,link\nA,https://t.me/gamma\n", encoding="utf-8")
    assert read_channel_list(path) == ["gamma"]


def test_manual_candidates_have_manual_method(tmp_path):
    path = tmp_path / "l.txt"
    path.write_text("@solo\n", encoding="utf-8")
    candidates = manual_candidates(path)
    assert candidates[0].username == "solo"
    assert candidates[0].method == "manual"
