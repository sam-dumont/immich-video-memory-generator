# Disclaimer

This project uses AI assistance to write code. The maintainer chooses the behaviour and reviews
results. Automated checks catch some mistakes; they do not establish that every setup works or
that every generated memory is suitable to share.

The app is beta. Review the first output before scheduling unattended runs. Keep your Immich
API key private and allow room for downloaded media, models, temporary renders and exports.

Rules selection can run without a language model. Image preparation is a separate choice:
`metadata_only` uses no model producers, `no_captions` uses image classifiers, and `full` also
needs a caption server. Remote model endpoints receive the pictures and text sent to them.
See [Network & Privacy](https://sam-dumont.github.io/immich-video-memory-generator/docs/deploy/configuration/network-and-privacy).

Checks and test commands are documented in [CONTRIBUTING.md](CONTRIBUTING.md) and the
[Makefile](Makefile). Report bugs through GitHub Issues and security concerns through
[SECURITY.md](SECURITY.md).

### No warranty

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.

IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
