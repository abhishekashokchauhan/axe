#!/bin/zsh
# axe setup -- double-click this file once after cloning (or run ./Setup.command).
# Everything stays inside this folder. Safe to run again: it never overwrites your list.

REPO="${0:A:h}"   # this folder, wherever axe was cloned
LIST="$REPO/grocery_list.txt"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

TTY=0; [[ -t 1 ]] && TTY=1
doing() { (( TTY )) && print -n -- "… $1\r"; }                    # shown while a step runs
clear_line() { (( TTY )) && print -n -- "\r\e[K"; }
ok()    { clear_line; print -P "%F{green}✓%f $1"; }
pause() { [[ -t 0 ]] && read -k 1 "?Press any key to close."; }   # only when run in a Terminal window
fail()  { clear_line; print -P "%F{red}✗ $1%f"; pause; exit 1; }

print -P "%B🛒 axe setup%b"
cd "$REPO" || fail "can't open $REPO"

doing "Checking your Mac"
[[ "$(uname)" == "Darwin" ]] || fail "axe supports macOS only for now."
PY=""
for c in python3.13 python3.12 python3; do
  if command -v $c >/dev/null && $c -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
    PY=$(command -v $c); break
  fi
done
[[ -n "$PY" ]] || fail "Python 3.12 or newer is needed.
  Install it with Homebrew:  brew install python@3.12
  or from https://www.python.org/downloads/macos/  -- then run Setup again."
ok "macOS $(sw_vers -productVersion) · Python $($PY -c 'import platform; print(platform.python_version())')"

doing "Checking Claude Code"
command -v claude >/dev/null || fail "Claude Code isn't installed (it chooses the products).
  Install it from https://claude.com/claude-code , run  claude  once in Terminal to sign in,
  then run Setup again."
if ! (cd /tmp && claude -p "Reply with OK" --tools "" --no-session-persistence --output-format json 2>/dev/null) \
    | grep -q '"is_error":false'; then
  fail "Claude Code is installed but not signed in.
  Open Terminal, run  claude  and sign in, then run Setup again."
fi
ok "Claude Code $(claude --version 2>/dev/null | awk '{print $1}') · signed in"

doing "Installing axe"
[[ -x .venv/bin/python ]] || "$PY" -m venv .venv || fail "couldn't create the Python environment"
.venv/bin/python -m pip install -q --upgrade pip >/dev/null && .venv/bin/python -m pip install -q -e . >/dev/null \
  || fail "installing dependencies failed (are you online?)"
chmod +x "Grocery Compare.command"
ok "Installed in this folder"

if [[ -f "$LIST" ]]; then
  ok "Kept your grocery_list.txt"
else
  cp examples/grocery_list.txt "$LIST" && ok "grocery_list.txt created from the sample"
fi

doing "Connecting Zepto and Swiggy Instamart"
# A browser tab opens only if an app still needs a login (phone number + OTP).
.venv/bin/python -m grocer login zepto --quiet     || fail "Zepto login didn't finish -- run Setup again to retry."
.venv/bin/python -m grocer login instamart --quiet || fail "Instamart login didn't finish -- run Setup again to retry."
ok "Zepto and Swiggy Instamart connected"

print -P "\n%F{green}%B✓ All set!%b%f Edit grocery_list.txt (opening now), save it, then run Grocery Compare.command"
[[ -t 0 ]] && open -e "$LIST"
pause
