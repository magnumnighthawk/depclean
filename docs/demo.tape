# Demo GIF for the README / Reddit post.  Render with:  vhs docs/demo.tape
# Runs against a scratch copy (plain cp -R, not an APFS clone, so the freed-space line is real) so the real project is untouched; DEMO_DIR is a fake $HOME holding code/agents/<project>.

Output docs/demo.gif

Set Shell "bash"
Set FontSize 16
Set Width 1100
Set Height 520
Set Padding 18
Set Theme "Catppuccin Mocha"
Set TypingSpeed 60ms
Set WindowBar Colorful

Hide
Type `export HOME="$DEMO_DIR" PS1='\[\e[1;36m\]\w\[\e[0m\] $ ' && cd ~/code && clear`
Enter
Sleep 300ms
Show

Type "depclean agents"
Sleep 500ms
Enter
Sleep 3s

# expand the project to show its folders
Enter
Sleep 2.5s
Down 3
Sleep 600ms
Down 3
Sleep 1s
Up 6
Sleep 400ms
Left
Sleep 800ms

# select the whole project and delete
Space
Sleep 1.2s
Type "d"
Sleep 3s
Type "y"
Sleep 4s

Enter
Sleep 800ms
Type "q"
Sleep 1s
