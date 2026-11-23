# Recorded by `just demo` in the container of docs/demo/Dockerfile.
Output new.gif

Set Shell bash
Set Width 1200
Set Height 700
Set FontFamily "JetBrainsMono Nerd Font Mono"
Set FontSize 23
Set LineHeight 1.2
Set Padding 28
Set Framerate 15
Set CursorBlink false
Set Theme "Catppuccin Mocha"

Hide
Type "PS1= && clear && uv run -q demo.py play"
Enter
Wait+Screen@60s /invented documents/
Show
Sleep 26s
