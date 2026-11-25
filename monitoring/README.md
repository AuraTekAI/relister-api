# External uptime monitoring

`uptime_check.py` checks the public website and API from **outside** the
production server and emails `KAZ_AND_TEAM` after 2 consecutive failed checks.
It uses only the Python 3 standard library, so it needs no Django, no Docker
and nothing from the server it monitors.

| Target  | URL (default)                                   | Counts as up            |
|---------|--------------------------------------------------|-------------------------|
| website | `https://autorelister.com.au/`                   | HTTP 200 within 15 s    |
| api     | `https://api.autorelister.com.au/api/login/`     | any HTTP status < 500   |

Behaviour per run: each target's consecutive-failure count is kept in
`uptime_state.json`. At 2 failures in a row one `[ALERT]` email is sent; nothing
more is sent while it stays down; a `[RECOVERED]` email is sent when it comes
back. A failed email send is retried on the next run.

## Set up (on the testing server or any other machine, NOT production)

```bash
cd ~/relister-api/monitoring
cp uptime.env.sample uptime.env        # fill in FLASHPOST_API_URL / FLASHPOST_API_KEY (same as backend .env)
python3 uptime_check.py                # one manual run — prints "website OK ... api OK ..."
crontab -e
```

Add (every 3 minutes):

```
*/3 * * * * /usr/bin/python3 /home/ubuntu/relister-api/monitoring/uptime_check.py >> /home/ubuntu/relister-uptime.log 2>&1
```

To test the alert path without an outage, point `UPTIME_WEBSITE_URL` at a
non-existent host in `uptime.env`, run the script twice, then restore it and run
once more to see the recovery email.

## Tests

```bash
cd relister-api/monitoring && python3 -m unittest test_uptime_check -v
```
