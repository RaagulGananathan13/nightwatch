# 0018. Predictive warnings: straight-line trends against known limits

- Status: accepted
- Date: 2026-10-30
- Spec sections: F12 (Phase 8)

## Context

Most incidents start suddenly, but some can be seen days ahead:
- a free tier that will run out before the month ends;
- an error budget used up a little each day;
- reads creeping toward a table's provisioned capacity.

CloudWatch already keeps the daily numbers needed to spot them. The spec asks for plain
statistics, not paid anomaly detection.

## Decision

1. **Forecast only against limits NightWatch can name.** A trend means something only next to
   a real limit:
   - **Lambda requests** this month, against the free 1,000,000;
   - **Lambda compute** this month, against the free 400,000 GB-seconds (each function's
     duration times its configured memory);
   - **CloudWatch log ingestion** this month, against the free 5 GB;
   - **Lambda concurrency:** the most functions running at once each day, against the
     account's limit;
   - **each service's error budget:** failed requests over the last 14 days, against the
     availability target in `services.yaml` (`slo: 99.5` for ShopLite);
   - **each watched table's read capacity:** the busiest hour's reads per second, against
     what is provisioned. On-demand tables are skipped because they have no provisioned
     capacity to run out of.
2. **A least-squares straight line through the last 14 complete days.** The spec allows linear
   or Holt-Winters. Two weeks of a lab's traffic has no seasonality for Holt-Winters to learn,
   and a straight line can be checked by hand. Today's partial day counts toward what has been
   used, never toward the trend. Days before anything ran are left out, so a new service's
   empty past doesn't flatten its trend.
3. **Four statuses.**
   - **ok:** nothing near the limit.
   - **watch:** a free tier heading for 80% by the month's end, a peak at 80% of capacity,
     or half the error budget spent.
   - **warn:** the trend reaches the limit (this month for free tiers, within 30 days for
     capacity), or three quarters of the error budget is spent.
   - **over:** the limit has already been reached.
4. **Daily, and Discord hears only what got worse.** EventBridge Scheduler runs the forecast
   at 07:00 UTC every day, and `make forecast` runs it on demand. Each report is kept for 90
   days, and the dashboard's **Forecasts** page shows the latest. Discord gets a message only
   when a forecast moves up to warn or over, so a known problem doesn't post every morning.
5. **Free calls only.** `ListFunctions`, `GetAccountSettings` and `DescribeTable` are free.
   `GetMetricStatistics` falls within CloudWatch's free million API requests a month; a run
   makes a few dozen. No metric math, no anomaly detection, no `GetMetricData`, all of which
   are billed.
6. **Not forecast yet.**
   - **Storage:** DynamoDB keeps no history of table size. `DescribeTable` gives only the
     current size, refreshed about every six hours. The tables hold kilobytes against a 25 GB
     free tier anyway. A trend could later be built from the stored daily reports.
   - **Cost:** spend has its own feature, the cost guardian (F17).

## Consequences

- One small Lambda run a day plus a few dozen API calls: free.
- The free-tier forecasts count this region (us-east-2), where all of NightWatch's functions
  run. The free tier itself is shared across regions, so functions elsewhere would be missed.
- In the lab, the chaos experiments drive the warnings. Injected faults spend ShopLite's error
  budget many times over, and the surge tests make read capacity look like it is climbing.
  Both are true of those weeks; read the report knowing what ran.
- A straight line overreacts when a quiet fortnight ends with a few busy days. That is
  acceptable for a warning a person reads; forecasts never act on their own.
