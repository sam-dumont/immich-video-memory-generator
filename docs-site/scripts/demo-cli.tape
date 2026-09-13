# CLI demo recording. The only consumer is the Remotion demo's CliScene
# (docs-site/remotion/src/scenes/CliScene.tsx), which plays it sped up.
#
# Nothing is simulated: scripts/demo-cli-hermetic.py runs the real CLI against
# the hermetic fake Immich and the scripted editorial route from tests/e2e,
# over the CC0 fixture library: generate, then runs story, then runs why, in and out.
#
# Requires: brew install vhs

Output "docs-site/remotion/public/cli-demo.mp4"

Set FontSize 16
Set Width 1400
Set Height 760
Set Theme "GitHub Dark"
Set Padding 20
Set TypingSpeed 40ms

Type "uv run python scripts/demo-cli-hermetic.py"
Enter
Sleep 66s
