import csv
import json
from types import SimpleNamespace
from unittest.mock import Mock

from espn_api.football.team import Team
from export_football import export_season, write_csv


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


def test_export_keeps_other_weeks_and_deduplicates_transactions():
    team = Team.__new__(Team)
    team.team_id, team.team_name = 1, "Example"
    team.roster = [SimpleNamespace(playerId=99, name="Player")]
    team.schedule = [team]
    transaction = {"id": "tx1", "scoringPeriodId": 1, "items": []}
    request = Mock()
    request.league_get.side_effect = lambda params, **kwargs: (
        {"schedule": [{"id": 7, "matchupPeriodId": 1}]}
        if params["view"] == "mMatchup"
        else {"transactions": [transaction]}
    )
    box = SimpleNamespace(
        home_team=team,
        away_team=None,
        home_score=12.5,
        away_score=0,
        home_lineup=[SimpleNamespace(playerId=99, points=12.5)],
        away_lineup=[],
    )
    league = SimpleNamespace(
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
    rows, issues = [], []
    export_season(league, rows, issues)
    assert len([r for r in rows if r["record_type"] == "transaction"]) == 1
    assert [r["week"] for r in rows if r["record_type"] == "lineup"] == [2]
    assert len([r for r in rows if r["record_type"] == "weekly_roster"]) == 2
    assert len(issues) == 1
    assert "private request detail" not in issues[0]
    assert league.recent_activity.call_args_list[1].kwargs["offset"] == 1
