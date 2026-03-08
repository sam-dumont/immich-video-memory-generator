# Disclaimer

## AI-Generated Software Notice

**This software was developed primarily using artificial intelligence (AI) assistance, specifically Claude by Anthropic.**

### What This Means

1. **AI-Assisted Development**: The majority of the code in this repository was generated, reviewed, and refined through conversations with an AI language model. While the AI was guided by a human developer who provided requirements, feedback, and direction, the actual code implementation is largely AI-generated.

2. **Reduced Guarantees**: As AI-generated software, this project comes with even fewer guarantees than typical open-source software:
   - The AI may have made assumptions that don't match your use case
   - Edge cases may not be fully handled
   - Performance optimizations may be suboptimal
   - Security considerations may be incomplete
   - Documentation may not perfectly reflect implementation

3. **Not Exhaustively Tested**: While basic functionality has been verified, the software has not undergone the rigorous testing typically expected of production software. There may be:
   - Undiscovered bugs
   - Memory leaks or performance issues
   - Compatibility problems with certain systems
   - Unexpected behavior with unusual inputs

### Your Responsibilities

By using this software, you agree to:

1. **Backup Your Data**: Always maintain backups of any data this software accesses (your Immich library, generated videos, etc.)

2. **Review Before Production**: If deploying in any production or important environment, review the code to ensure it meets your requirements

3. **Report Issues**: If you discover bugs or security vulnerabilities, please report them through GitHub Issues or the security reporting process

4. **Contribute Improvements**: The community is encouraged to review, improve, and fix issues in this codebase

### No Warranty

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.

IN NO EVENT SHALL THE AUTHORS, COPYRIGHT HOLDERS, OR AI SYSTEMS USED IN DEVELOPMENT BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

### Specific Risks

This software:

- **Accesses your Immich server** via API - ensure your API key is kept secure
- **Processes your personal videos** - be aware of privacy implications
- **Downloads videos temporarily** - ensure adequate disk space
- **Uses significant CPU/GPU resources** - may affect system performance
- **May use external AI services** (Ollama, OpenAI) - review their privacy policies if enabled
- **Fetches music from external sources** - review licensing for your use case

### AI Model Limitations

The AI models used in development (and optionally in the LLM content analysis feature):

- May produce incorrect or nonsensical outputs
- Have knowledge cutoffs and may not reflect current best practices
- Cannot guarantee security or correctness of generated code
- May have biases in training data that affect outputs

### Recommendation

**For critical or production use cases, we strongly recommend:**

1. Thoroughly reviewing the source code
2. Running comprehensive tests in your environment
3. Having someone with software development experience audit the code
4. Starting with non-critical data to verify behavior
5. Monitoring resource usage during operation

### Questions?

If you have questions about this software or its AI-generated nature, please open a GitHub Discussion.

---

*This disclaimer was last updated: March 2026*
