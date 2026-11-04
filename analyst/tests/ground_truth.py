"""Expected metric values computed with plain pandas, independently of the SQL.

Each metric is written out by hand from the raw tables. Churn rate is computed
from subscriptions directly, not from the customer_months table the SQL uses,
so a bug in that table would show up as a mismatch.
"""

from datetime import date

import pandas as pd

DIMS = ["region", "segment", "plan"]


class GroundTruth:
    def __init__(self, con):
        df = lambda sql: con.execute(sql).df()
        customers = df("SELECT * FROM customers WHERE NOT is_internal")
        self.invoices = df("SELECT * FROM invoices").merge(customers, on="customer_id")
        self.subs = df("SELECT * FROM subscriptions").drop(columns="plan").merge(
            customers, on="customer_id")
        users = df("SELECT user_id, customer_id FROM users")
        self.events = df("SELECT * FROM events").merge(users, on="user_id").merge(
            customers, on="customer_id")

    def rows(self, metric, start, end):
        """Rows in the period with a 'time' column, plus what the metric needs."""
        between = lambda col: (col >= pd.Timestamp(start)) & (col < pd.Timestamp(end))
        if metric in ("revenue", "paying_customers", "arpu"):
            r = self.invoices.assign(time=self.invoices.invoice_month)
        elif metric == "new_customers":
            r = self.subs.assign(time=self.subs.start_date)
        elif metric == "churned_customers":
            r = self.subs.assign(time=self.subs.end_date)
        elif metric == "churn_rate":
            r = self.churn_rows(start, end)
        elif metric == "active_users":
            r = self.events[self.events.event_type.isin(["login", "report_created"])]
            r = r.assign(time=r.event_ts)
        elif metric == "reports_created":
            r = self.events[self.events.event_type == "report_created"]
            r = r.assign(time=r.event_ts)
        r = r[between(pd.to_datetime(r.time))]
        return r.assign(period=pd.to_datetime(r.time).dt.to_period("M").dt.to_timestamp())

    def churn_rows(self, start, end):
        parts, m = [], pd.Timestamp(start)
        while m < pd.Timestamp(end):
            nxt = m + pd.offsets.MonthBegin(1)
            s, e = pd.to_datetime(self.subs.start_date), pd.to_datetime(self.subs.end_date)
            active = self.subs[(s < m) & (e.isna() | (e >= m))]
            ended = pd.to_datetime(active.end_date)
            parts.append(active.assign(time=m, churned=(ended.notna() & (ended < nxt))))
            m = nxt
        return pd.concat(parts)

    @staticmethod
    def aggregate(metric, g):
        if metric == "revenue":
            return float(g.amount.sum())
        if metric == "paying_customers":
            return g.customer_id.nunique()
        if metric == "arpu":
            return float(g.amount.sum()) / g.customer_id.nunique()
        if metric in ("new_customers", "churned_customers", "reports_created"):
            return len(g)
        if metric == "churn_rate":
            return g.churned.sum() / len(g)
        if metric == "active_users":
            return g.user_id.nunique()
        raise KeyError(metric)

    def expected(self, plan):
        """{(period?, dim values...): value}, the same shape as the SQL result."""
        r = self.rows(plan.metric, plan.start, plan.end)
        for f in plan.filters:
            r = r[r[f.dimension].isin(f.values)]
        keys = (["period"] if plan.grain == "month" else []) + list(plan.dimensions)
        if not keys:
            return {(): self.aggregate(plan.metric, r)} if len(r) else {}
        out = {}
        for k, g in r.groupby(keys):
            k = tuple(x.date() if isinstance(x, pd.Timestamp) else x for x in k)
            out[k] = self.aggregate(plan.metric, g)
        return out
