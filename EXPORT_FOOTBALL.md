# Export your ESPN fantasy football league

Open a PowerShell terminal in this folder and run:

```powershell
.\.venv\Scripts\python.exe .\export_football.py
```

The existing virtual environment contains this project's dependencies. For a fresh
checkout without that environment, run `uv sync` first.

Enter your league ID, the season years separated by spaces (for example
`2024 2025 2026`), and each cookie value when prompted. Your league ID is the
number after `leagueId=` in your ESPN league URL. Paste SWID including its
`{braces}`, then paste espn_s2 separately. Paste only the values, without cookie
names or surrounding quotes. Cookie input is hidden and is not saved by the script.

You can also supply the league and seasons on the command line:

```powershell
.\.venv\Scripts\python.exe .\export_football.py --league-id 123456 --years 2024 2025 2026
```

Replace `123456` with your league ID. The script still asks privately for cookies.

Each run creates a new folder inside `exports`, containing:

- **league_data.csv**: a combined file; filter `record_type`, `season`, and `week`.
- Separate CSVs for each returned record type: league, settings, member, team,
  roster, draft, schedule, weekly_roster, matchup, lineup, transaction, activity.
- **export_notes.txt**: requested seasons, limitations, and any failures.

The `team` records contain standings and team totals. `lineup` records contain
weekly player points, projections, and lineup slots, including bench players.
Nested data such as full stat breakdowns, scoring rules, and transaction items
is preserved as JSON text inside CSV cells. Team references contain ID and name.
The combined file has blank cells where columns do not apply to a record type.
The CSVs use UTF-8 with a BOM for Excel. Names that start with spreadsheet formula
characters are prefixed with an apostrophe so Excel treats them as text.

This exports accessible league data for the seasons you specify, through each
season's current scoring week. It does not discover other leagues in your account
or export the entire NFL free-agent pool, private messages, or every ESPN endpoint.
ESPN can omit historical rosters, transactions, and trade details. This library
does not support box scores or activity before 2019, and documents box scores for
the most recent season; older results should be checked. An empty result is not
a guarantee that no history exists. Current-week points can change. The script
keeps successful results when another section fails and exits with status 1 if
warnings occur. Review the notes before treating an export as complete.

Exports include league member data. The `exports/` directory is ignored by Git.
