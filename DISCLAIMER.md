# Disclaimer

## Built With AI, On Purpose

This entire codebase was written with AI (Claude by Anthropic). Not by accident, not as a shortcut: this is a deliberate experiment in how far you can push AI-assisted development while maintaining a clean, production-quality codebase.

The question I'm trying to answer: can you build something genuinely complex with AI and have it NOT be a mess? So far the answer is yes, but it takes real engineering discipline on the human side: architecture decisions, code review, quality gates, and knowing when the AI is confidently wrong.

### The quality bar

The AI writes code. I make sure it's good. Every line goes through:

- 1,100+ tests (unit, integration, benchmarks)
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
- Docstring coverage enforcement
- Architecture layer enforcement
- Conventional commit enforcement
- OpenSSF Scorecard monitoring
- 17 CI quality gates (tiered: cheap gates first, tests and Docker after)
- Pre-commit hooks running all of the above locally

If a human wrote this code, nobody would bat an eye at the quality. The AI part is the interesting experiment, not a caveat.

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

*Last updated: March 2026*
