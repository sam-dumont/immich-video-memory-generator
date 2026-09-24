# Documentation

The manual is on the [docs site](https://sam-dumont.github.io/immich-video-memory-generator/),
versioned with the code:
[self-hosting](https://sam-dumont.github.io/immich-video-memory-generator/docs/being-rewritten/self-hosting),
[your first memory](https://sam-dumont.github.io/immich-video-memory-generator/docs/get-started/first-film),
[config reference](https://sam-dumont.github.io/immich-video-memory-generator/docs/reference/config-reference),
[CLI reference](https://sam-dumont.github.io/immich-video-memory-generator/docs/reference/cli-reference),
[troubleshooting](https://sam-dumont.github.io/immich-video-memory-generator/docs/reference/troubleshooting).

Authentication is off by default. If the UI binds beyond loopback, anyone who can reach the port can
use it, so turn auth on before exposing it. The UI is single-user and single-replica: run one
instance.

What lives in the repo:

| Document | What it is |
|----------|------------|
| [Main README](../README.md) | Installation, quick start, configuration |
| [Architecture](../ARCHITECTURE.md) | Package map, key classes, data flow, composition pattern |
| [Contributing](../CONTRIBUTING.md) | How to contribute |
| [Changelog](../CHANGELOG.md) | Version history |
| [Disclaimer](../DISCLAIMER.md) | AI-generated software notice and warranty |
| [Security](../SECURITY.md) | Security policy and how to report a vulnerability |
| [Kubernetes](../deploy/kubernetes/README.md) | Deploying on Kubernetes |
| [Terraform](../deploy/terraform/README.md) | Deploying with Terraform |
