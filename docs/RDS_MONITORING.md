# RDS backup monitoring

Beat task `RDS_Backup_Monitor` (`team_alerts/rds_monitor.py`) runs every 30
minutes and reads the **latest restorable time** of the Sydney database and
the Melbourne copy. If either is more than 60 minutes behind now, or cannot be
read at all, `KAZ_AND_TEAM` gets an alert through the central alert system
(one email, a reminder after 24 h, `[RESOLVED]` when it catches up).

## 1. Create the read-only IAM user (once, in the AWS console or CLI)

The monitor must **not** use the S3 application key. Create a separate user
that can only describe RDS resources.

Policy `RelisterRdsMonitorReadOnly`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "rds:Describe*",
      "Resource": "*"
    }
  ]
}
```

CLI:

```bash
aws iam create-policy --policy-name RelisterRdsMonitorReadOnly --policy-document file://rds-monitor-policy.json
aws iam create-user --user-name relister-rds-monitor
aws iam attach-user-policy --user-name relister-rds-monitor --policy-arn arn:aws:iam::<ACCOUNT_ID>:policy/RelisterRdsMonitorReadOnly
aws iam create-access-key --user-name relister-rds-monitor
```

Copy the `AccessKeyId` and `SecretAccessKey` from the last command into
`src/.env` on the server. Do not give this user any other permission.

## 2. Configure `.env`

```
RDS_MONITOR_ACCESS_KEY_ID=<AccessKeyId>
RDS_MONITOR_SECRET_ACCESS_KEY=<SecretAccessKey>
RDS_MONITOR_DATABASES=sydney=ap-southeast-2=<sydney-db-identifier>,melbourne=ap-southeast-4=<melbourne-copy-identifier>
RDS_MONITOR_MAX_LAG_MINUTES=60
RDS_MONITOR_INTERVAL_MINUTES=30
```

The identifier is the **DB instance identifier** shown in the RDS console
(the first label of the endpoint hostname). For the Melbourne copy use the
identifier shown under *Automated backups → Replicated* (cross-region backup
replication) or the read replica's own identifier; the monitor handles both.

## 3. Deploy

`python manage.py migrate` (creates the Beat row), then restart `beat-scheduler`
and `celery-worker-scheduling`.

## 4. Verify

```bash
docker exec -it celery-worker-scheduling python /app/src/manage.py shell -c "from team_alerts.rds_monitor import rds_backup_monitor; print(rds_backup_monitor())"
```

Expected: `{'sydney': 'ok (3m behind)', 'melbourne': 'ok (12m behind)'}`.
A `check failed` entry means the credentials, identifier or region are wrong,
and an alert has already been sent for it.

## Tests

```bash
python src/manage.py test team_alerts.tests_rds_monitor
```
