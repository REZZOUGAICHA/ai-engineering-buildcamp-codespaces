"""Q5: find trace IDs with SQL. Needs LOGFIRE_READ_TOKEN in .env."""
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from logfire.query_client import LogfireQueryClient

load_dotenv()
client = LogfireQueryClient(read_token=os.environ['LOGFIRE_READ_TOKEN'])

# In this logfire version, min_timestamp is required (the lesson's code
# omits it). Look back 7 days, which covers all homework sessions.
SINCE = datetime.now(timezone.utc) - timedelta(days=7)

# 1) Grouped sessions: the parent span we created in Q4.
sessions = client.query_json_rows(sql="""
    SELECT trace_id, start_timestamp, duration
    FROM records
    WHERE span_name = 'trivia_session'
    ORDER BY start_timestamp DESC
""", min_timestamp=SINCE)
print("trivia_session spans (newest first):")
for r in sessions['rows']:
    print(f"  {r['trace_id']}  start={r['start_timestamp']}  duration={r['duration']:.1f}s")

# 2) Cross-check: how many agent runs sit inside each trace?
#    The grouped session should have 6; the Q3 ungrouped traces have 1 each.
runs = client.query_json_rows(sql="""
    SELECT trace_id, COUNT(*) AS agent_runs, MIN(start_timestamp) AS first_run
    FROM records
    WHERE span_name = 'agent run' OR span_name LIKE 'invoke_agent %'
    GROUP BY trace_id
    ORDER BY first_run DESC
    LIMIT 15
""", min_timestamp=SINCE)
print("\nagent runs per trace (newest first):")
for r in runs['rows']:
    print(f"  {r['trace_id']}  agent_runs={r['agent_runs']}  first_run={r['first_run']}")
