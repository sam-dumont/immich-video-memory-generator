---
title: On a NAS
---

# On a NAS

Reader: newcomer.

The NAS that runs Immich runs this too, with no model and no second box. The install is the
[Docker Compose](./docker.md) one; this page is what is different on a Synology, QNAP, TrueNAS or
Unraid box. Tested on a Synology DS423+ (Celeron J4125, four cores).

## Install

Synology Container Manager, QNAP Container Station, TrueNAS SCALE Apps and the Unraid Docker
Compose Manager plugin all take the compose file as a project. Put `docker-compose.yml` and your
`.env` (from `example.env`) in the project folder, create an `output` folder beside them, and
start the project. Then, from an SSH session in that folder:

```bash
sudo docker compose exec immich-memories immich-memories models fetch
sudo docker compose exec immich-memories immich-memories preflight
```

The NAS tier is `no_captions`, which the compose file already pins: the eight context heads and
the two detectors run on the NAS CPU, and nothing leaves the box. That is the whole setup.

Set the home base in `.env` before the first cut
(`IMMICH_MEMORIES_TRIPS__HOMEBASE_LATITUDE` and `..._LONGITUDE`). Without it no day counts as
away from home, so a three-week holiday arrives as three weekly stories instead of one trip. Then
confirm who's who once: [Teach it your family](../get-started/who-is-who.md).

### The output folder

The container runs as UID 1000. On Synology the folder a DSM user creates belongs to that user,
usually not 1000, and the first cut refuses with `Output directory is not writable`. Either
`sudo chown -R 1000:1000 output` over SSH, or set `user: "<your uid>:<your gid>"` on the service
(`id` prints them) and chown the config volume to match. If neither suits, drop the `./output`
mount and turn on upload-back: the film goes to Immich instead.

### Do not use `cpus:` on a Synology

`cpus:` is a CFS quota, and DSM runs a cgroup v1 kernel built without the CFS bandwidth
controller. A DS423+ answers `docker compose up` with `NanoCPUs can not be set, as your kernel
does not support CPU CFS scheduler or the cgroup is not mounted` and starts nothing. The shipped
file sets no CPU limit for that reason. To keep cores free for Immich, pin them instead:

```yaml
    cpuset: "0-2"      # three of four cores; works without CFS
```

Memory limits work on every NAS tested.

### Reaching the UI

The UI is on the NAS's loopback only. From your desktop:

```bash
ssh -L 8080:localhost:8080 you@your-nas
```

then open `http://localhost:8080`. To put it on the LAN instead, turn on
[authentication](./authentication.mdx) first, then change the mapping to `"8080:8080"`. UniFi and
other NAS apps often hold 8080 already: change the left side (`127.0.0.1:8081:8080`) and tunnel
to that port.

## What to expect

The first cut of a month reads every picture it can reach once, on the NAS CPU, and banks the
answers. Run it in the evening. The second cut of the same month reads nothing again, and what is
left is mostly the render. `docker compose logs immich-memories | grep "preparation tier"` shows
what each producer cost this box. Numbers per host are on [Measured](../better/measured.md).

Start with one month, not a year: preparation grows with the pictures in the window, not with the
length of the film. To read a bigger window ahead of time, run
`immich-memories prepare --year 2025` overnight; later cuts inside it start warm.

## Encoding

The default codec is H.264, which Intel Quick Sync encodes in hardware. Pass the render node
through to use it; the compose file carries the block commented out:

```yaml
    devices:
      - /dev/dri:/dev/dri
    group_add:
      - "937"          # the GID that owns /dev/dri/renderD128 on DSM
```

`stat -c '%g' /dev/dri/renderD128` on the host prints the GID. Without `group_add` the device is
there and the container cannot open it. More on [Hardware encoding](./hardware.md#intel-quick-sync-and-amd-vaapi).

If you switch to `output.codec: h265`, know that Gemini Lake (the J4125 class) has no HEVC encode:
that part goes to software, and the log says `vaapi cannot encode h265 on this device`. ARM NAS
models have no hardware encoder here at all.

The J4125 has no AVX either, so titles are drawn by the CPU fallback instead of the animated
kernels: [CPUs without AVX](./hardware.md#cpus-without-avx).

## Long films

A long album on two cores is a long encode, and after the music is mixed in the app decodes the
whole film once to prove it plays. That check can take as long again as a 1080p encode, logs
`Checking the finished film: ... decoded` once a minute, and a film that fails it stays on disk:
[Troubleshooting](../reference/troubleshooting.md#a-long-render-ends-with-ffprobe-failed-to-inspect-output-artifact).
Keep 4K for a box with more cores, or send the render to a
[GPU box](../better/gpu-render.md).

## Memory and disk

The 4 GB limit in the compose file is what the tested runs used. The render blends one clip at a
time, so its memory does not grow with the number of clips.

Size `cache.thumbnail_cache_max_size_mb` against your library: too small and the next overlapping
memory downloads every preview again, which on a NAS is the slow part. The budget per picture is in
the [config reference](../reference/config-reference.md#size-the-thumbnail-cache-by-your-library).

## What a NAS can't do

- Hold a 30B reader. A model is optional; if you want one, it goes on a Mac, a GPU box or a hosted
  API: [Add a reader](../better/reader.md).
- Captions at a useful speed. A caption per picture on four Celeron cores is about 30 seconds, so
  `full` on a NAS alone is days for a year. The compose file has a captioner profile if you are
  patient: [Add captions](../better/captions.md).
- Generate music: MusicGen and ACE-Step want a GPU. The bundled tracks and your own uploads work.

## Every night

Uncomment `IMMICH_MEMORIES_AUTOMATION__ENABLED` and `IMMICH_MEMORIES_AUTOMATION__DAILY_AT` in the
compose file and set `TZ` in `.env`. The UI process makes one memory a day by itself; there is no
cron to install. [Daily automation](./docker.md#daily-automation).
