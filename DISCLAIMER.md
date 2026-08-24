# Disclaimer

## Built With AI, On Purpose

This entire codebase was written with AI (Claude by Anthropic). That's the experiment: how far AI-assisted development goes when every change has to survive a full verification stack before it lands.

The human half is architecture decisions, code review, and spotting when the AI is confidently wrong. The gates below catch the rest.

### The quality bar

The AI writes code. I make sure it's good. Every line goes through:

- 5,600+ tests (5,000 unit, 600+ integration/E2E, plus benchmarks)
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
- 20 gates on every PR: 15 static checks in the quality job, 5 security scans in the security job. They are tiered, so the cheap ones fail first and the tests, Docker builds and launch check only run after
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
- Can optionally use external AI services (Ollama, OpenAI) for content analysis: review their privacy policies if you enable this
- Music generation/fetching may involve external sources: check licensing for your use case

### Questions?

Open a GitHub Discussion.

---

*Last updated: 2026-08-24*
