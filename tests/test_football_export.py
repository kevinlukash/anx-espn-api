import csv
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import export_football
from espn_api.football.team import Team
from export_football import export_season, waiver_players, write_csv


def test_csv_handles_team_cycles_nested_stats_and_excel_names(tmp_path):
    team = Team.__new__(Team)
    team.team_id = 1
    team.team_name = "=My team"
    team.schedule = [team]
    path = tmp_path / "league.csv"
    write_csv(
        path,
        [
            {
                "record_type": "lineup",
                "league_id": 123,
                "season": 2026,
                "week": 1,
                "team": team,
                "name": "=My team",
                "points": -2,
                "stats": {1: {"points": 12.5}},
            }
        ],
    )
    with path.open(encoding="utf-8-sig", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert json.loads(row["team"]) == {"team_id": 1, "team_name": "=My team"}
    assert json.loads(row["stats"])["1"]["points"] == 12.5
    assert row["name"] == "'=My team"
    assert row["points"] == "-2"


def example_league():
    team = Team.__new__(Team)
    team.team_id, team.team_name = 1, "Example"
    team.roster = [SimpleNamespace(playerId=99, name="Player")]
    team.schedule = [team]
    transaction = {"id": "tx1", "scoringPeriodId": 1, "items": []}
    request = Mock()

    def response(params, **kwargs):
        if params["view"] == "kona_player_info":
            return {"players": []}
        if params["view"] == "mMatchup":
            return {"schedule": [{"id": 7, "matchupPeriodId": 1}]}
        return {"transactions": [transaction]}

    request.league_get.side_effect = response
    box = SimpleNamespace(
        home_team=team,
        away_team=None,
        home_score=12.5,
        away_score=0,
        home_lineup=[SimpleNamespace(playerId=99, points=12.5)],
        away_lineup=[],
    )
    return SimpleNamespace(
        league_id=123,
        year=2026,
        current_week=2,
        previousSeasons=[2025],
        firstScoringPeriod=1,
        finalScoringPeriod=18,
        settings=SimpleNamespace(name="Example"),
        members=[],
        teams=[team],
        draft=[],
        espn_request=request,
        load_roster_week=Mock(),
        box_scores=Mock(side_effect=[RuntimeError("private request detail"), [box]]),
        recent_activity=Mock(
            side_effect=[
                [SimpleNamespace(date=123, actions=[])],
                [],
            ]
        ),
    )


def test_export_keeps_other_weeks_and_deduplicates_transactions():
    league = example_league()
    rows, issues = [], []
    export_season(league, rows, issues)
    assert len([r for r in rows if r["record_type"] == "transaction"]) == 1
    assert [r["week"] for r in rows if r["record_type"] == "lineup"] == [2]
    assert len([r for r in rows if r["record_type"] == "weekly_roster"]) == 2
    assert len(issues) == 1
    assert "private request detail" not in issues[0]
    assert league.recent_activity.call_args_list[1].kwargs["offset"] == 1


def pool_entry(player_id, status="FREEAGENT"):
    return {
        "id": player_id,
        "status": status,
        "onTeamId": 0,
        "waiverProcessDate": 123456,
        "player": {
            "id": player_id,
            "fullName": f"Player {player_id}",
            "eligibleSlots": [0, 7, 20],
            "proTeamId": 1,
            "ownership": {"percentOwned": 12.5},
            "stats": [
                {
                    "seasonId": 2026,
                    "scoringPeriodId": 2,
                    "statSourceId": 0,
                    "appliedTotal": 8.5,
                }
            ],
        },
    }


def test_waiver_players_paginates_deduplicates_and_preserves_status_and_stats():
    league = example_league()
    league.espn_request.league_get.side_effect = [
        {"players": [pool_entry(1), pool_entry(2, "WAIVERS")]},
        {"players": [pool_entry(2, "WAIVERS"), pool_entry(3)]},
        {"players": []},
    ]
    players = list(waiver_players(league, page_size=2))
    assert [p["playerId"] for p in players] == [1, 2, 3]
    assert [p["availability_status"] for p in players] == [
        "FREEAGENT",
        "WAIVERS",
        "FREEAGENT",
    ]
    assert players[1]["player_pool_entry"]["waiverProcessDate"] == 123456
    assert players[1]["stats"][2]["points"] == 8.5
    filters = []
    for call in league.espn_request.league_get.call_args_list:
        assert call.kwargs["params"] == {
            "view": "kona_player_info",
            "scoringPeriodId": 2,
        }
        filters.append(
            json.loads(call.kwargs["headers"]["x-fantasy-filter"])["players"]
        )
    assert [f["offset"] for f in filters] == [0, 2, 4]
    assert all(f["filterStatus"]["value"] == ["FREEAGENT", "WAIVERS"] for f in filters)


def test_waiver_players_handles_server_page_size_cap():
    league = example_league()
    league.espn_request.league_get.side_effect = [
        {"players": [pool_entry(1)]},
        {"players": [pool_entry(2)]},
        {"players": []},
    ]
    assert len(list(waiver_players(league, page_size=100))) == 2
    assert league.espn_request.league_get.call_count == 3


def test_waiver_players_detects_repeated_pages():
    league = example_league()
    league.espn_request.league_get.return_value = {"players": [pool_entry(1)]}
    league.espn_request.league_get.side_effect = None
    players = waiver_players(league)
    assert next(players)["playerId"] == 1
    with pytest.raises(ValueError, match="pagination stopped advancing"):
        next(players)
    assert league.espn_request.league_get.call_count == 2


@pytest.mark.parametrize("response", [{}, {"players": None}, {"players": {}}, []])
def test_waiver_players_rejects_missing_or_invalid_data(response):
    league = example_league()
    league.espn_request.league_get.side_effect = None
    league.espn_request.league_get.return_value = response
    with pytest.raises(ValueError, match="omitted available player data"):
        list(waiver_players(league))


def test_export_keeps_partial_waivers_after_request_failure(capsys):
    league = example_league()
    original_response = league.espn_request.league_get.side_effect
    pool_response = Mock(
        side_effect=[
            {"players": [pool_entry(1, "WAIVERS")]},
            RuntimeError("private request detail"),
        ]
    )

    def response(params, **kwargs):
        if params["view"] == "kona_player_info":
            return pool_response()
        return original_response(params, **kwargs)

    league.espn_request.league_get.side_effect = response
    rows, issues = [], []
    export_season(league, rows, issues)
    waivers = [r for r in rows if r["record_type"] == "waiver"]
    assert len(waivers) == 1
    assert (waivers[0]["league_id"], waivers[0]["season"], waivers[0]["week"]) == (
        123,
        2026,
        2,
    )
    assert any("available player pool unavailable (RuntimeError)" in i for i in issues)
    assert any(r["record_type"] == "lineup" for r in rows)
    assert "private request detail" not in capsys.readouterr().out


def test_export_skips_waivers_before_2019():
    league = example_league()
    league.year = 2018
    rows, issues = [], []
    export_season(league, rows, issues)
    assert not any(r["record_type"] == "waiver" for r in rows)
    assert any("waiver pool before 2019" in i for i in issues)
    assert all(
        call.kwargs["params"]["view"] != "kona_player_info"
        for call in league.espn_request.league_get.call_args_list
    )


@pytest.mark.parametrize("pool_result", ["empty", "players", "failure"])
def test_main_writes_waiver_csv_for_multiple_seasons(
    tmp_path, monkeypatch, pool_result
):
    has_players = pool_result == "players"

    def make_league(**kwargs):
        league = example_league()
        league.year = kwargs["year"]
        league.box_scores = Mock(return_value=[])
        league.recent_activity = Mock(return_value=[])
        original_response = league.espn_request.league_get.side_effect
        pages = iter(
            [
                (
                    {"players": [pool_entry(1, "WAIVERS")]}
                    if has_players
                    else {"players": []}
                ),
                {"players": []},
            ]
        )

        def response(params, **headers):
            if params["view"] == "kona_player_info":
                if pool_result == "failure":
                    raise RuntimeError("private request detail")
                return next(pages)
            return original_response(params, **headers)

        league.espn_request.league_get.side_effect = response
        return league

    monkeypatch.setattr(export_football, "League", make_league)
    monkeypatch.setattr(export_football, "getpass", lambda prompt: "test-cookie")
    monkeypatch.setattr(
        export_football.sys,
        "argv",
        [
            "export_football.py",
            "--league-id",
            "123",
            "--years",
            "2025",
            "2026",
            "--output",
            str(tmp_path),
        ],
    )
    assert export_football.main() == (1 if pool_result == "failure" else 0)
    folder = next(tmp_path.iterdir())
    with (folder / "waiver.csv").open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames[:4] == ["record_type", "league_id", "season", "week"]
        waivers = list(reader)
    with (folder / "league_data.csv").open(encoding="utf-8-sig", newline="") as stream:
        combined = [r for r in csv.DictReader(stream) if r["record_type"] == "waiver"]
    assert len(waivers) == len(combined) == (2 if has_players else 0)
    if has_players:
        assert [r["season"] for r in waivers] == ["2025", "2026"]
        assert all(r["availability_status"] == "WAIVERS" for r in waivers)
    assert "most recent season only" in (folder / "export_notes.txt").read_text()
    if pool_result == "failure":
        notes = (folder / "export_notes.txt").read_text()
        assert notes.count("available player pool unavailable (RuntimeError)") == 2
        assert "private request detail" not in notes
