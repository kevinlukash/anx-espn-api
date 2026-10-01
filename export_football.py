"""Export accessible football league records to CSV; see EXPORT_FOOTBALL.md."""

import argparse
import csv
from datetime import date, datetime
from getpass import getpass
import json
from pathlib import Path
import sys

from espn_api.football import League
from espn_api.football.constant import TRANSACTION_TYPES
from espn_api.football.team import Team


def serializable(value):
    # Team schedules point to other Teams, so serialize references, not cycles.
    if isinstance(value, Team):
        return {"team_id": value.team_id, "team_name": value.team_name}
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): serializable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [serializable(v) for v in value]
    if hasattr(value, "__dict__"):
        return serializable(attributes(value))
    return value


def attributes(obj, exclude=()):
    return {
        k: v for k, v in vars(obj).items() if not k.startswith("_") and k not in exclude
    }


def cell(value):
    value = serializable(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    # Keep names supplied by league members as text when opened in Excel.
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def write_csv(path, rows):
    columns = ["record_type", "league_id", "season", "week"]
    columns += sorted({key for row in rows for key in row} - set(columns))
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows({k: cell(v) for k, v in row.items()} for row in rows)


def export_season(league, rows, issues):
    def add(kind, values, week="", **context):
        rows.append(
            {
                **serializable(values),
                **context,
                "record_type": kind,
                "league_id": league.league_id,
                "season": league.year,
                "week": week,
            }
        )

    def attempt(label, action):
        try:
            action()
        except Exception as exc:
            # Never print exception text, which could contain request details.
            message = f"{league.year}: {label} unavailable ({type(exc).__name__})"
            issues.append(message)
            print("  WARNING: " + message)

    add(
        "league",
        {
            "current_week": league.current_week,
            "previous_seasons": league.previousSeasons,
            "first_scoring_period": league.firstScoringPeriod,
            "final_scoring_period": league.finalScoringPeriod,
        },
    )
    add("settings", attributes(league.settings))
    for member in league.members:
        add("member", member)
    for team in league.teams:
        add("team", attributes(team, exclude=("roster",)))
        for player in team.roster:
            add(
                "roster",
                attributes(player),
                week=league.current_week,
                team_id=team.team_id,
                team_name=team.team_name,
            )
    for pick in league.draft:
        add("draft", attributes(pick))

    # Preserve the full schedule, including future games and matchup-period IDs.
    def schedule():
        data = league.espn_request.league_get(params={"view": "mMatchup"})
        for matchup in data.get("schedule", []):
            add("schedule", matchup)

    attempt("full schedule", schedule)

    player_team_cache = {}
    seen_transactions = set()
    for week in range(max(1, league.firstScoringPeriod), league.current_week + 1):
        print(f"  {league.year}: week {week}/{league.current_week}", flush=True)

        def weekly_rosters():
            league.load_roster_week(week)
            for team in league.teams:
                for player in team.roster:
                    add(
                        "weekly_roster",
                        attributes(player),
                        week=week,
                        team_id=team.team_id,
                        team_name=team.team_name,
                    )

        attempt(f"week {week} rosters", weekly_rosters)

        def boxes():
            for index, box in enumerate(league.box_scores(week, player_team_cache)):
                add(
                    "matchup",
                    attributes(box, ("home_lineup", "away_lineup")),
                    week=week,
                    matchup_index=index,
                )
                for side in ("home", "away"):
                    team = getattr(box, side + "_team")
                    for player in getattr(box, side + "_lineup"):
                        add(
                            "lineup",
                            attributes(player),
                            week=week,
                            matchup_index=index,
                            side=side,
                            team_id=getattr(team, "team_id", team),
                            team_name=getattr(team, "team_name", ""),
                        )

        if league.year >= 2019:
            attempt(f"week {week} box scores", boxes)

        def transactions():
            # Raw records retain fields that the parsed Transaction discards.
            data = league.espn_request.league_get(
                params={"view": "mTransactions2", "scoringPeriodId": week},
                headers={
                    "x-fantasy-filter": json.dumps(
                        {
                            "transactions": {
                                "filterType": {"value": sorted(TRANSACTION_TYPES)}
                            }
                        }
                    )
                },
            )
            if "transactions" not in data:
                raise ValueError("ESPN omitted transaction data")
            for transaction in data["transactions"] or []:
                key = json.dumps(transaction, sort_keys=True)
                if key not in seen_transactions:
                    seen_transactions.add(key)
                    add(
                        "transaction",
                        transaction,
                        week=transaction.get("scoringPeriodId", week),
                    )

        attempt(f"week {week} transactions", transactions)

    def activities():
        offset = 0
        seen = set()
        while True:
            batch = league.recent_activity(size=100, offset=offset)
            if not batch:
                break
            new = 0
            for activity in batch:
                values = serializable(attributes(activity))
                key = json.dumps(values, sort_keys=True)
                if key not in seen:
                    seen.add(key)
                    new += 1
                    add("activity", values)
            if not new:
                raise ValueError("Activity pagination stopped advancing")
            offset += len(batch)

    if league.year >= 2019:
        attempt("activity history", activities)
    else:
        issues.append(
            f"{league.year}: library does not support box scores or activity before 2019"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league-id", type=int)
    parser.add_argument(
        "--years", type=int, nargs="+", help="Seasons, e.g. 2024 2025 2026"
    )
    parser.add_argument("--output", type=Path, default=Path("exports"))
    args = parser.parse_args()
    league_id = args.league_id or int(input("ESPN league ID: ").strip())
    years = args.years or [
        int(y) for y in input("Season years (space separated): ").split()
    ]
    if league_id <= 0 or not years or any(y < 2000 or y > 2100 for y in years):
        parser.error(
            "Provide a positive league ID and season years between 2000 and 2100."
        )
    print(
        "Paste each cookie value separately. Input is hidden; press Enter after each."
    )
    swid = getpass("SWID (include its braces): ").strip()
    espn_s2 = getpass("espn_s2: ").strip()
    if not swid or not espn_s2:
        parser.error("Both cookie values are required for this private-league export.")

    folder = args.output / f"league_{league_id}_{datetime.now():%Y%m%d_%H%M%S_%f}"
    folder.mkdir(parents=True, exist_ok=False)
    rows, issues = [], []
    for year in sorted(set(years)):
        print(f"Loading league {league_id}, season {year}...", flush=True)
        try:
            league = League(league_id=league_id, year=year, swid=swid, espn_s2=espn_s2)
            export_season(league, rows, issues)
        except Exception as exc:
            message = f"{year}: season export failed ({type(exc).__name__}); check league ID, season and cookies."
            print(message)
            issues.append(message)
        # Save progress after each season, including any partial results.
        write_csv(folder / "league_data.csv", rows)
        (folder / "export_notes.txt").write_text(
            "Requested seasons: " + ", ".join(map(str, years)) + "\n"
            "Exports contain only data returned by ESPN. Empty history does not prove completeness.\n"
            "Historical lineups and transactions may be incomplete; current-week scores are provisional.\n"
            + "\n".join(issues)
            + "\n",
            encoding="utf-8",
        )
    for kind in sorted({r["record_type"] for r in rows}):
        write_csv(folder / f"{kind}.csv", [r for r in rows if r["record_type"] == kind])
    print(f"Saved {len(rows)} records to {folder.resolve() / 'league_data.csv'}")
    print("Separate CSVs by record type and export_notes.txt are in the same folder.")
    if issues:
        print(f"Export has {len(issues)} warnings; review export_notes.txt.")
    return 1 if issues or not rows else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        sys.exit("Export canceled.")
