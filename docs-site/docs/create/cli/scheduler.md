---
sidebar_position: 4
title: scheduler (deprecated)
---

# scheduler

:::caution Deprecated
The `scheduler` command group is the advanced/legacy cron daemon. It still exists and still runs, but it is
not maintained: it needs `--foreground`, it knows nothing about the rotation rules, the failure
back-off or the upload retries that [`auto`](./auto.md) has, and it will be removed in a later
release.
:::

Use `auto` instead. One daily `auto run` picks the memory worth making that day; `auto install`
schedules it on the host, and in Docker `automation.enabled: true` with `automation.daily_at`
does the same inside the web UI process. See [Automated generation](../recipes/automated-generation.md).

If you still run a `schedules:` section, the three subcommands are `scheduler list`, `scheduler status`
and `scheduler start --foreground`. Nothing else in the app reads that section.
