---
sidebar_position: 2
title: Automated Generation
---

# Automated Generation

Run `immich-memories auto run` once a day. It retries one pending delivery, generates one
eligible memory, or skips the run. The same cooldown, history and variety rules apply to host
schedules, the UI's daily timer and HTTP triggers.

## Check the decision first

```bash
immich-memories auto suggest
immich-memories auto run --dry-run
immich-memories auto run
```

`auto suggest` lists candidates and rejection reasons. `auto run --dry-run` checks the decision
without rendering. See [auto](../cli/auto.md) for detectors, cooldowns and result fields.

## Schedule it on the host

```bash
immich-memories auto install --hour 9
```

This writes a launcher and schedule files. **Run the `Activate` command it prints**, then
check `immich-memories auto status`. On Linux, activation is:

```bash
systemctl --user enable --now immich-memories-auto.timer
```

macOS prints a `launchctl` command. Other platforms print a crontab entry to install.
The job runs as your user and does not inherit your interactive shell; put its credentials in
the config file. If using a custom file, pass `--config /path/to/config.yaml` before `auto install`.

To remove it, run the printed `Deactivate` command first, then `auto install --uninstall`.

## Docker and the web UI: built-in daily timer

The UI process can run the same decision daily, without a host scheduler:

```yaml
advanced:
  automation:
    enabled: true
    daily_at: "09:00"
```

The time is local to the process. In Docker, set `TZ` for the intended timezone. The equivalent
environment variables are `IMMICH_MEMORIES_AUTOMATION__ENABLED=true` and
`IMMICH_MEMORIES_AUTOMATION__DAILY_AT=09:00`. Restart the UI after changing its config.

- If the container was down at the scheduled time, it catches up when it starts. A decision
  already recorded that day, including a manual `auto run`, prevents another timer run.
- An active UI or CLI generation holds the shared lock; the timer records a skip.
- `/health/ready` reports `in_process_scheduler`, including the next run and last outcome.
- Uploads and notifications use the [automation configuration](../cli/auto.md#configuration).

For an on-demand HTTP call, see [Trigger from Immich or Anything Else](./trigger-endpoint.md).

## Kubernetes

`deploy/kubernetes/base/job.yaml` contains a one-off generation Job, a monthly CronJob and an
`auto run` CronJob. Edit their example parameters and choose which schedules to enable before
applying the file. They use the `immich-memories-secrets` Secret and the deployment's PVCs:

```bash
kubectl apply -f deploy/kubernetes/base/job.yaml
```

Output goes to `/app/output`. See the [Kubernetes deployment](../../deploy/installation/kubernetes.md)
for storage and setup. Avoid running several daily schedulers for the same installation.
