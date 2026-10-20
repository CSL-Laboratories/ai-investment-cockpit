#!/usr/bin/env bash
# PreToolUse guard for the Bash tool. Allow rules in .claude/settings.json match
# command text, and Claude Code does not document how they treat command or
# process substitution inside an allowed command's arguments. Any Bash call
# whose input contains $(, a backtick, <( or >( therefore gets a permission prompt.
# A $, < or > followed by a backslash also asks: a line continuation there
# ("$\<newline>(cmd)") still runs the substitution.
# The whole hook input is scanned, so a match in another field only adds a
# prompt. Prints nothing otherwise and always exits 0: a failing hook is ignored.
input=$(cat)
case $input in
  *'$('* | *'`'* | *'<('* | *'>('* | *'$\\'* | *'<\\'* | *'>\\'* | *'\u0060'* | *'\u003c('* | *'\u003C('* | *'\u003e('* | *'\u003E('*)
    printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"Command or process substitution needs approval (.claude/hooks/guard-substitution.sh)."}}'
    ;;
esac
exit 0
