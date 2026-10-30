# skypoints-data-pipeline
I run a global airline loyalty program called SkyPoints, with lounges and partner airlines across the world. Every  member enrolled in the program is issued a Membership Card that lets them access any partner lounge worldwide, redeem miles, and track their tier status.

## Member staging and country tables

Before the first member load, run `sql/create_member_tables.sql` in the target
database and schema. It creates the `MEMBER_RAW` landing table and defines the
staging-table columns. Set `SNOWFLAKE_TABLE=MEMBER_RAW` for the supplied DDL and
loader configuration. The named stage and file format must also exist.

Run `sql/load_member_staging.sql` after loading the member feed into
`MEMBER_RAW`. It rebuilds `MEMBER_STAGING` with the derived `AGE` and
`STALE_MEMBER` columns. Then run `sql/split_member_countries.sql` in the same
database and schema. That script creates or replaces a
`MEMBER_COUNTRY_<Country>` target table for every country currently or
previously represented in staging, selecting one row per `Member_Id`. The
target table DDL is generated from `MEMBER_STAGING` so it follows the staged
profile schema. A registry lets a country table be emptied when its last
member moves away.

The country-splitting SQL uses the greatest `Last_Flight_Date` as the available
proxy for record recency, with `Enrollment_Date` as a tie-breaker. Neither field
is a true record-update timestamp. If a member has conflicting country records
with both dates equal, the source data cannot establish which country is latest;
add a source/load timestamp or sequence to resolve that case reliably.

## Redemption transactions and member profile

Run `code/load_redemptions.py` to validate each redemption JSON feed and load
one transaction per row into `REDEMPTIONS_RAW`. The loader creates the raw
table if needed and safely replaces rows for incoming `(Member_Id, Txn_Id)`
keys when retried. The feed-level `member_id` and `feed_date` are copied onto
each object in the `redemptions` array, along with `txn_id`, `txn_date`,
`partner`, `miles_redeemed`, and `status`.

After refreshing `MEMBER_STAGING`, run `sql/flatten_redemptions.sql` to rebuild
`REDEMPTIONS_FLAT`. The query joins on `Member_Id` and uses a left join so
transactions remain available even when a member profile is missing. It first
selects one profile row per member using `Last_Flight_Date`, then
`Enrollment_Date`, as the available recency ordering. As with country
assignment, equal dates cannot prove which profile version is latest; a true
source update timestamp or sequence is needed to resolve that ambiguity.

## Data quality validations

Run `sql/validate_member_redemption_data.sql` after refreshing
`MEMBER_STAGING` and loading `REDEMPTIONS_RAW`. It replaces
`DATA_QUALITY_ISSUES` with the current findings and returns them for review.
An empty report means these checks passed. `ERROR` findings should block
downstream processing; `WARNING` findings need review. The SQL reports findings
and does not itself stop the upstream load.

The report checks required member and redemption fields, unique member IDs,
unique `(Member_Id, Txn_Id)` redemption keys, numeric member IDs, ID-length
outliers, date ordering, age/staleness derivations, non-negative miles, and
redemptions that have no matching staged profile. The sample has member IDs of
different lengths (`223457`, `22345`, `2256`) and a four-letter country value
(`PHIL`), which the report flags for investigation. Country-code shape is only
a heuristic; use an authoritative country-code reference table to check actual
codes. ID-length outliers are warnings based on the most common observed length,
not proof that the value is invalid. Concentrated DOB/enrollment/flight dates
are also warnings, since uniform sample data can indicate placeholder values
but may be legitimate for a batch. If `MEMBER_STAGING` intentionally retains
multiple historical versions per member, duplicate `Member_Id` findings are
expected; check uniqueness after selecting the current version instead.

## Run SQL independently

The generic Snowflake SQL runner can execute a SQL file independently of the
scheduled pipeline:

```powershell
python code\run_sql.py sql\load_member_staging.sql
python code\run_sql.py sql\validate_member_redemption_data.sql --fail-on-error
```

It executes every statement in a `.sql` file in order and reports SQL result
row counts. `--fail-on-error` additionally fails if
`DATA_QUALITY_ISSUES` contains any `ERROR` rows.

## Run on Windows with Task Scheduler

Use `code/run_pipeline.py` as the scheduled entry point. It waits for both feed
types, validates and loads them, runs the SQL transformations and quality gate,
archives the batch, then sends a success email. Schedule the script with
Windows Task Scheduler after completing a successful manual run.

Create a Windows virtual environment from PowerShell:

```powershell
Set-Location 'V:\Data Engineer Pipeline\skypoints-data-engineering'
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Use the Python version supported by your installed Snowflake Connector if it
differs from 3.13. Put these settings in the project's `.env` file or the
Windows account environment:

- Snowflake connection: `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`,
  `SNOWFLAKE_PASSWORD`, `SNOWFLAKE_WAREHOUSE`, `SNOWFLAKE_DATABASE`, and
  `SNOWFLAKE_SCHEMA`; `SNOWFLAKE_ROLE` is optional.
- Member feed: `SNOWFLAKE_STAGE`, `SNOWFLAKE_TABLE`,
  `SNOWFLAKE_FILE_FORMAT`, `MEMBER_FILE_PATTERN=member_*.txt`,
  `MEMBER_DELIMITER=|`, and `MEMBER_COLUMNS`.
- Redemption feed: `SNOWFLAKE_REDEMPTIONS_TABLE`.

The member target table, named stage, and file format must exist in Snowflake
before the first run. The redemption raw table is created automatically if it
does not exist. Also set:

- `SKYPOINTS_DATA_DIR` and `SKYPOINTS_ARCHIVE_DIR` (optional; default to the
  project's `data` and `archive` directories)
- `SKYPOINTS_SUCCESS_EMAIL`, `SMTP_HOST`, and `SMTP_FROM`
- `SMTP_PORT` (optional, defaults to `587`), `SMTP_USE_TLS` (optional,
  defaults to `true`), and optionally `SMTP_USER` plus `SMTP_PASSWORD`

Configure SMTP credentials in a protected environment or secret store. Do not
commit them. The pipeline writes logs to `logs/pipeline.log` and prevents
overlapping runs with a Windows file lock. Member loads replace existing
`MEMBER_RAW` rows for IDs present in the incoming batch, while preserving
duplicate IDs within the batch for the quality check; this makes reruns safe
without masking duplicates in the current feeds.

Test a single run manually before scheduling it. Place only the intended batch
of member and redemption files in the input directory, then run:

```powershell
.\.venv\Scripts\python.exe code\run_pipeline.py --wait-timeout-seconds 300 --poll-interval-seconds 10
```

After confirming the test output in Snowflake, create a scheduled task:

1. Open **Task Scheduler** and select **Create Task**.
2. On **General**, give it a name such as `SkyPoints Data Pipeline`; choose the
   Windows account that has access to the project and Snowflake configuration.
   Select **Run whether user is logged on or not** if it should run unattended.
3. On **Triggers**, add the required schedule (for example, daily at the time
   the feeds are expected). The script itself waits up to 24 hours for each
   feed, so schedule it when the batch is expected to arrive.
4. On **Actions**, select **Start a program**:
   - **Program/script:** `V:\Data Engineer Pipeline\skypoints-data-engineering\.venv\Scripts\python.exe`
   - **Add arguments:** `"V:\Data Engineer Pipeline\skypoints-data-engineering\code\run_pipeline.py"`
   - **Start in:** `V:\Data Engineer Pipeline\skypoints-data-engineering`
5. On **Settings**, choose **Do not start a new instance** if the task is
   already running. The script also has its own lock as a second safeguard.
6. Save the task, run it once with **Run**, and inspect `logs\pipeline.log`
   and the Task Scheduler **Last Run Result**.

If the project or input folder is on a mapped drive such as `V:`, make sure the
Task Scheduler account can access that drive when logged off. A UNC path is
usually more reliable for unattended runs. The runner can wait up to 24 hours
for each feed type, so one run may remain active for almost 48 hours; schedule
the task and its **Do not start a new instance** setting accordingly.

Do not configure Task Scheduler to automatically restart failed runs without
reviewing why they failed. The runner returns a non-zero exit code on failure.
Processing or archival failures leave unarchived files available for inspection
or rerun. The success email is sent after the archive step; if the SMTP server
fails at that point, the data has completed and the files are already archived,
so inspect the log and send/confirm the notification separately rather than
blindly rerunning the whole batch.
