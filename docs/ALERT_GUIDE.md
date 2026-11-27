# AutoRelister alert guide

All monitoring emails come from one sender (`team_alerts/alerting.py`). Each
issue produces **one email when it starts**, a **`[STILL FAILING]` reminder
only if it is still broken after 24 hours** (`ALERT_REMIND_HOURS`), and one
**`[RESOLVED]` email when it is fixed**. A `[TEST]` email goes to both lists
every Monday 09:00 UTC; if it stops arriving, the alert system itself is broken.

Recipient lists are set in `.env`: `KAZ_ONLY` and `KAZ_AND_TEAM`
(comma-separated). `support@autorelister.com.au` is never used.

**Who acts:** *Kaz → dealer* means Kaz messages the dealer; no technical work.
*Tech team* means a developer or server action is needed.

## KAZ_ONLY — dealer and Facebook issues

| Email subject starts with | What it means | Who acts |
|---|---|---|
| `[ALERT] Facebook account restricted — <dealer>` | Facebook shows none of the dealer's published cars; the account is locked or restricted. Publishing is paused. | Kaz → dealer: check the Facebook account, resolve the restriction. |
| `[ALERT] Facebook security checkpoint — <dealer>` | Facebook sent the dealer to a security check / verification page. Nothing can be published until it is completed. | Kaz → dealer: complete the Facebook checkpoint, then click Check Again. |
| `[ALERT] Logged out of Facebook — <dealer>` | The dealer's browser has no Facebook login. | Kaz → dealer: log in to Facebook in Chrome. |
| `[ALERT] Gumtree check failing — <dealer>` | That dealer's Gumtree fetch has failed twice in a row (ZenRows/network/parse error), so their listings are not being synced. One email per dealer until a fetch succeeds. | Kaz: if it says 402, top up ZenRows. Otherwise tech team (Gumtree blocking, profile URL changed). |
| `[ALERT] Gumtree listings dropped — <dealer>` | The 03:00/08:00 Gumtree check returned 0 listings, or under 50% of the previous run, for that dealer. A failed fetch is **not** counted as 0. | Kaz → dealer first (did they remove ads, change profile URL?). If the dealer changed nothing, tech team (Gumtree blocking). |
| `[ALERT] Spike in publish / delete / Facebook form-fill failures` | Failures in the last hour are far above the 7-day normal. Several dealers = Facebook changed something; one dealer = that account. | Tech team if several dealers; Kaz → dealer if one. |
| `[Action Required] Client has duplicate FB listings` | The extension found duplicate Marketplace listings for a dealer. | Kaz → dealer. |
| `[Action Required] Client FB account needs verification` | The extension detected Facebook asking the dealer to verify. | Kaz → dealer. |
| `[Action Required] Client has listings older than 7 days` | Listings were not relisted on schedule. | Kaz → dealer (is the extension open?); tech team if it persists. |

## KAZ_AND_TEAM — platform and server issues

| Email subject starts with | What it means | Who acts |
|---|---|---|
| `[ALERT] Relister website is DOWN` / `Relister api is DOWN` | The public site or API failed 2 consecutive checks from outside the server. | Tech team, immediately. |
| `[ALERT] Relister database is DOWN` / `Relister redis is DOWN` | The backend cannot reach PostgreSQL or Redis (2 consecutive checks). Publishing, syncs and logins fail. | Tech team, immediately. |
| `[WARNING] Relister server disk 8x% full` | Disk at 80%+. | Tech team: free space soon. |
| `[CRITICAL] Relister server disk 9x% full` | Disk at 90%+; the server can stop writing at 100%. | Tech team, immediately. |
| `[ALERT] Relister server memory low` | Under 300 MB free for 10 minutes. | Tech team: check containers, restart if needed. |
| `[ALERT] Flashpost email error 403: sending suspended` | Flashpost has suspended sending. Dealer emails, invoices and most alerts are not going out. This email arrives via Amazon SES, not Flashpost. | Tech team / Kaz: check the Flashpost account. |
| `[ALERT] Flashpost email error 422: recipient suppressed` | Flashpost refuses one address (bounced or unsubscribed); the email in the body was not delivered. Sent via Amazon SES. | Kaz → dealer if it is a dealer address (confirm their email); tech team if it is ours. |
| `[ALERT] RDS backups behind for sydney / melbourne database` | The database's latest restorable time is over an hour old; point-in-time recovery would lose everything after it. | Tech team: check RDS automated backups and cross-region replication. |
| `[ALERT] RDS backup check failed for <db> database` | The monitor could not read the backup status at all (credentials, identifier or region wrong). | Tech team: fix `RDS_MONITOR_*` in `.env` (see docs/RDS_MONITORING.md). |
| `[ALERT] S3 image upload failing` | Vehicle photos cannot be uploaded to Amazon S3, so listings publish without hosted images or fail. | Tech team: AWS credentials, bucket policy, S3 status. |
| `[ALERT] Relister scheduled task did not run: <task>` | A nightly job (Gumtree check, trial expiry, invoices, overage report, log cleanup…) missed its slot by more than 2 hours. | Tech team: check Beat and the worker. |
| `[WARNING] / [CRITICAL] ZenRows credits at 80% / 95%` | Scraping credits nearly used for this billing period. | Tech team / Kaz: top up or upgrade ZenRows. |
| `[WARNING] ZenRows credits projected to run out <date>` | At the current rate credits run out before renewal. | Tech team / Kaz: top up or reduce scraping. |
| `[CRITICAL] ZenRows 402 — out of credits` | ZenRows refused a scraper request. Gumtree, custom-domain and image scraping are failing now. | Tech team / Kaz: top up ZenRows immediately. |
| `[TEST] AutoRelister alert system is working` | Weekly proof that alert emails deliver. | Nobody. If it stops arriving on Mondays, tell the tech team. |
| `[STILL FAILING] …` | The issue above has been open for 24 h+ without a fix. | Same as the original alert. |
| `[RESOLVED] …` | The issue cleared on its own or was fixed. | Nobody. |
