"""
RDS backup monitoring — is point-in-time recovery current for the Sydney
database and its Melbourne copy?

Beat task "RDS_Backup_Monitor" (every RDS_MONITOR_INTERVAL_MINUTES). For each
entry in RDS_MONITOR_DATABASES ("name=region=db-instance-identifier", comma
separated) it reads the LATEST RESTORABLE TIME from AWS:
    1. describe_db_instances      → DBInstance.LatestRestorableTime
       (the live instance; also a cross-region read replica)
    2. if the identifier is not an instance in that region,
       describe_db_instance_automated_backups → RestoreWindow.LatestTime
       (a cross-region automated-backup replica, e.g. the Melbourne copy)
If that time is more than RDS_MONITOR_MAX_LAG_MINUTES (60) behind now, the
alert 'rds:backup:<name>' is raised to KAZ_AND_TEAM through
team_alerts.alerting (once, reminder after ALERT_REMIND_HOURS, [RESOLVED] when
it catches up). A database that cannot be read at all raises
'rds:check:<name>' — an unverifiable backup is reported, never ignored.

Credentials: a dedicated READ-ONLY IAM user with only rds:Describe* —
RDS_MONITOR_ACCESS_KEY_ID / RDS_MONITOR_SECRET_ACCESS_KEY in .env. The S3
application key is not used. See docs/RDS_MONITORING.md for the IAM policy.
"""
import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .alerting import raise_alert, resolve_alert

logger = logging.getLogger('relister_views')


def _setting(name, default):
    return getattr(settings, name, default)


def parse_databases(raw):
    """'sydney=ap-southeast-2=relister-prod, melbourne=ap-southeast-4=relister-prod'
    → [{'name','region','identifier'}]. Bad entries are logged and skipped."""
    if isinstance(raw, str):
        raw = raw.split(',')
    out = []
    for item in raw or []:
        parts = [p.strip() for p in str(item).split('=')]
        if len(parts) != 3 or not all(parts):
            if str(item).strip():
                logger.error(f"rds_monitor: ignoring malformed RDS_MONITOR_DATABASES entry {item!r} (want name=region=identifier)")
            continue
        out.append({'name': parts[0], 'region': parts[1], 'identifier': parts[2]})
    return out


def _client(region):
    import boto3
    key = _setting('RDS_MONITOR_ACCESS_KEY_ID', '')
    secret = _setting('RDS_MONITOR_SECRET_ACCESS_KEY', '')
    if not key or not secret:
        raise RuntimeError('RDS_MONITOR_ACCESS_KEY_ID / RDS_MONITOR_SECRET_ACCESS_KEY are not configured')
    return boto3.client('rds', region_name=region, aws_access_key_id=key, aws_secret_access_key=secret)


def latest_restorable_time(client, identifier):
    """(datetime, source) for the instance or its replicated automated backup.
    Raises when neither can be read."""
    from botocore.exceptions import ClientError
    try:
        resp = client.describe_db_instances(DBInstanceIdentifier=identifier)
        inst = resp['DBInstances'][0]
        value = inst.get('LatestRestorableTime')
        if value is None:
            raise RuntimeError(f"instance {identifier} has no LatestRestorableTime (status {inst.get('DBInstanceStatus')}, backups may be disabled)")
        return value, f"instance ({inst.get('DBInstanceStatus')})"
    except ClientError as exc:
        if exc.response.get('Error', {}).get('Code') != 'DBInstanceNotFound':
            raise
    # Not an instance here → look for a cross-region automated-backup replica.
    resp = client.describe_db_instance_automated_backups(DBInstanceIdentifier=identifier)
    backups = [b for b in resp.get('DBInstanceAutomatedBackups', []) if (b.get('RestoreWindow') or {}).get('LatestTime')]
    if not backups:
        raise RuntimeError(f"no instance and no automated backup replica named {identifier} in this region")
    backup = max(backups, key=lambda b: b['RestoreWindow']['LatestTime'])
    return backup['RestoreWindow']['LatestTime'], f"automated backup replica ({backup.get('Status')})"


@shared_task(queue='scheduling_queue')
def rds_backup_monitor():
    databases = parse_databases(_setting('RDS_MONITOR_DATABASES', []))
    max_lag = timedelta(minutes=float(_setting('RDS_MONITOR_MAX_LAG_MINUTES', 60)))
    now = timezone.now()
    summary = {}
    if not databases:
        logger.error("rds_monitor: RDS_MONITOR_DATABASES is empty — nothing monitored.")
        return summary

    for db in databases:
        name, region, identifier = db['name'], db['region'], db['identifier']
        try:
            restorable, source = latest_restorable_time(_client(region), identifier)
        except Exception as exc:
            summary[name] = f'check failed: {exc}'
            logger.error(f"rds_monitor: could not read latest restorable time for {name} ({identifier}, {region}): {type(exc).__name__}: {exc}")
            raise_alert(
                f'rds:check:{name}', 'team',
                f"[ALERT] RDS backup check failed for {name} database",
                '\n'.join([
                    f"The monitor could not read the latest restorable time of the {name} database, so its backups cannot be verified.",
                    '',
                    f"Instance: {identifier}  (region {region})",
                    f"Error: {type(exc).__name__}: {exc}",
                    '',
                    'Check the RDS_MONITOR_* credentials (read-only IAM user with rds:Describe*), the identifier and the region.',
                ]) + '\n',
            )
            continue
        resolve_alert(f'rds:check:{name}', note=f"{name} database readable again ({source}).")

        lag = now - restorable
        lag_minutes = int(lag.total_seconds() // 60)
        key = f'rds:backup:{name}'
        if lag <= max_lag:
            summary[name] = f'ok ({lag_minutes}m behind)'
            logger.info(f"rds_monitor: {name} ({identifier}, {region}) latest restorable time {restorable:%Y-%m-%d %H:%M} UTC, {lag_minutes}m behind — ok ({source}).")
            resolve_alert(key, note=f"{name} database latest restorable time is {lag_minutes} minutes behind now ({source}).")
            continue

        summary[name] = f'BEHIND {lag_minutes}m'
        logger.error(f"rds_monitor: {name} ({identifier}, {region}) latest restorable time is {lag_minutes}m behind now (limit {int(max_lag.total_seconds() // 60)}m).")
        raise_alert(
            key, 'team',
            f"[ALERT] RDS backups behind for {name} database — latest restorable time {lag_minutes} minutes ago",
            '\n'.join([
                f"The {name} database's latest restorable time is {lag_minutes} minutes behind the current time (limit {int(max_lag.total_seconds() // 60)} minutes).",
                'Point-in-time recovery would lose everything after that moment.',
                '',
                f"Instance: {identifier}  (region {region}, {source})",
                f"Latest restorable time: {restorable:%Y-%m-%d %H:%M:%S} UTC",
                f"Checked at: {now:%Y-%m-%d %H:%M:%S} UTC",
                '',
                'Check RDS automated backups / transaction log upload for the instance, and cross-region backup replication for the copy.',
            ]) + '\n',
        )
    logger.info(f"rds_monitor: {summary}")
    return summary
