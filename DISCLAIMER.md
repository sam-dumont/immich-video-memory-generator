# Disclaimer

## Built With AI, On Purpose

Claude (Anthropic's model) writes most of this codebase: the code, the tests, most of the docs, and the PRs. That's the experiment: how far AI-assisted development goes when every change has to survive a full verification stack before it lands.

### Who does what

- **Claude** writes the change, runs the gates below, opens the PR, and fixes whatever comes back red.
- **I (the maintainer)** set the direction and rule on the product calls: what a good film is, what may be shown to whom, what runs by default. I review the results, meaning the films, the contact sheets and the measurements on a real library, and I read the diffs of the PRs I pick, usually the ones that touch those calls.
- **A merge train** squash-merges green PRs to `main` without asking me each time. Claude runs it under a standing permission I gave. A red PR does not merge, and a check that fails twice comes back as work, not as a retry.

Said plainly: not every PR diff is read by a human. What stands between a change and `main` is the gate list below, plus my review of what the change does to real films. When a film comes out wrong, the fix is another PR through the same gates.

### The quality bar

Every change goes through:

- A unit suite and an integration suite; `uv run pytest tests/ --collect-only -q` prints the current split. No count is written down here, because it moves every week and a written one is wrong within days
- Ruff linting and formatting on every PR
- mypy static type checking
- Cyclomatic complexity gates (Xenon grade C max, cognitive complexity checks)
- 800-line file length limits
- Dead code detection (Vulture)
- Code duplication detection
- Refurb modernization checks
- Security scanning: Bandit, Semgrep, Gitleaks
- Dependency vulnerability auditing (pip-audit)
- Dockerfile linting (Hadolint)
- CLI and config reference drift checks (the docs cannot describe a flag that no longer exists)
- Architecture layer enforcement
- Conventional commit enforcement
- OpenSSF Scorecard monitoring
- 21 gates on every PR: 16 static checks in the quality job, 5 security scans in the security job. They are tiered, so the cheap ones fail first and the tests, Docker builds and launch check only run after
- Pre-commit hooks running all of the above locally

That list is the claim, and you can check it yourself: the gates live in the `Makefile`, the pipeline in `.github/workflows/ci.yml`. The build stays red until they pass.

### Standard open-source stuff

Like any software, this project may have undiscovered bugs, may behave unexpectedly with unusual inputs, and is provided without warranty.

### Your responsibilities

- Keep backups of anything this software accesses (your Immich library, generated videos)
- Keep your Immich API key secure
- Report bugs and security issues through GitHub Issues
- If you find something broken, PRs are welcome

### No warranty

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.

IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

### Things to know

- Accesses your Immich server via API: keep your API key secure
- Downloads videos temporarily: make sure you have disk space
- Uses significant CPU/GPU resources during processing
- Cuts with no model at all on `reader: rules` and `tier: metadata_only`. Two optional endpoints, a vision reader and a caption server, make it a better cut; both speak the OpenAI-compatible API, both are meant to be yours, and pointing either at a third party sends your pictures there
- Music generation/fetching may involve external sources: check licensing for your use case

### Questions?

Open a GitHub Discussion.

---

*Last updated: 2026-09-24*
