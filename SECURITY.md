# Security Policy

## Supported Versions

Only the latest release receives security fixes. The project follows semantic
versioning and ships from `main`; upgrade to the newest tag on
[GitHub Releases](https://github.com/sam-dumont/immich-video-memory-generator/releases)
before reporting.

| Version | Supported          |
| ------- | ------------------ |
| [latest release](https://github.com/sam-dumont/immich-video-memory-generator/releases) | :white_check_mark: |
| older   | :x:                |

## Reporting a Vulnerability

If you discover a security vulnerability in Immich Memories, please report it responsibly:

1. **Do NOT** open a public GitHub issue for security vulnerabilities
2. Use [GitHub's private vulnerability reporting](https://github.com/sam-dumont/immich-video-memory-generator/security/advisories/new) (preferred), or email the maintainer at the address on the [GitHub profile](https://github.com/sam-dumont)
3. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

I'm a solo maintainer on a hobby project, so there is no SLA. Security reports go to the front
of the queue, but "the front of the queue" realistically means a few days, sometimes longer. If a
week passes with no reply, open a public issue asking me to check my inbox, without describing
the vulnerability.

## Deployment posture (short version)

- Authentication is **off by default**; the container listens on `0.0.0.0:8080`. Enable
  [authentication](https://sam-dumont.github.io/immich-video-memory-generator/docs/deploy/configuration/authentication)
  before exposing the port beyond localhost, and put a TLS reverse proxy in front.
- The Immich API key only needs read access plus, optionally, upload/album scopes; the app
  never deletes or edits existing assets.
- What leaves your network (geocoding, map tiles, LLM, music, notifications) is listed on the
  [network & privacy page](https://sam-dumont.github.io/immich-video-memory-generator/docs/deploy/configuration/network-and-privacy).
- CI runs five security scans on every change: Bandit, Semgrep, pip-audit, Gitleaks and Hadolint.
  OpenSSF Scorecard runs on its own schedule and on pushes to `main`. The Docker image is
  digest-pinned and runs as a non-root user.

## Security Considerations

### API Keys

- Never commit API keys to the repository
- Use environment variables or the config file (which should be in `.gitignore`)
- The config file is stored in `~/.immich-memories/config.yaml`

### Network Security

- All communication with Immich should be over HTTPS
- Verify your Immich server's SSL certificate is valid

### Local Storage

- Downloaded videos are cached locally
- Cache directory: `~/.immich-memories/cache/`
- Clear cache periodically if disk space is a concern

## Dependencies

The pip-audit gate blocks any PR that introduces a dependency with a known CVE. To pick up the
patches, upgrade:

```bash
uv tool upgrade immich-memories        # or: pip install --upgrade immich-memories
docker compose pull                    # Docker installs
```
