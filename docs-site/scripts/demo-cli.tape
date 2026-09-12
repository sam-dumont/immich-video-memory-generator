# CLI demo recording. The only consumer is the Remotion demo's CliScene
# (docs-site/remotion/src/scenes/CliScene.tsx), which plays this file at 5x.
# Runs the simulation script directly: Rich's LiveDisplay renders natively
# with proper cursor control (progress bar stays fixed).
#
# Requires: brew install vhs

Output "docs-site/remotion/public/cli-demo.mp4"

Set FontSize 16
Set Width 1200
Set Height 700
Set Theme "GitHub Dark"
Set Padding 20
Set TypingSpeed 40ms

Type "uv run python scripts/simulate-cli-demo.py"
Enter
Sleep 25s
