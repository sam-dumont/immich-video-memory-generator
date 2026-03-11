# Disclaimer

## AI-Assisted Development

**This software was developed with significant AI assistance (Claude by Anthropic), guided and directed by a human developer.**

### What This Means

1. **AI-Assisted, Human-Directed**: AI helped generate and refine code, but a human developer provided requirements, architectural decisions, code review, and quality oversight throughout the process.

2. **Rigorously Tested and Validated**: This project maintains strict quality standards enforced through automated CI/CD:
   - **400+ tests** covering core functionality, edge cases, and integrations
   - **Linting and formatting** via Ruff (enforced on every PR)
   - **Static type checking** via mypy
   - **Cyclomatic complexity gates** — no function exceeds grade C (Xenon)
   - **File length limits** — no source file exceeds 500 lines
   - **Dead code detection** via Vulture
   - **Security scanning** via Bandit (SAST), Semgrep, and Gitleaks (secret detection)
   - **Dependency vulnerability auditing** via pip-audit
   - **Dockerfile linting** via Hadolint
   - **Conventional commit enforcement** via Commitizen
   - **Pre-commit hooks** running all checks locally before code reaches CI

3. **Standard Open-Source Caveats**: Like any software, this project:
   - May have undiscovered bugs despite extensive testing
   - May behave unexpectedly with unusual inputs or environments
   - Is provided without warranty (see below)

### Your Responsibilities

By using this software, you acknowledge:

1. **Backup Your Data**: Always maintain backups of any data this software accesses (your Immich library, generated videos, etc.)

2. **Secure Your Credentials**: Keep your Immich API key and any other credentials safe

3. **Report Issues**: If you discover bugs or security vulnerabilities, please report them through GitHub Issues or the security reporting process

4. **Contribute Improvements**: The community is encouraged to review, improve, and fix issues in this codebase

### No Warranty

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.

IN NO EVENT SHALL THE AUTHORS, COPYRIGHT HOLDERS, OR AI SYSTEMS USED IN DEVELOPMENT BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

### Usage Considerations

This software:

- **Accesses your Immich server** via API — ensure your API key is kept secure
- **Processes your personal videos** — be aware of privacy implications
- **Downloads videos temporarily** — ensure adequate disk space
- **Uses significant CPU/GPU resources** — may affect system performance
- **May use external AI services** (Ollama, OpenAI) — review their privacy policies if enabled
- **Can fetch or generate music** from external sources — review licensing for your use case

### Questions?

If you have questions about this software, please open a GitHub Discussion.

---

*This disclaimer was last updated: March 2026*
