#!/bin/zsh
# axe setup -- double-click this file once after cloning (or run ./Setup.command).
# Everything stays inside this folder. Safe to run again: it never overwrites your list.

REPO="${0:A:h}"   # this folder, wherever axe was cloned
LIST="$REPO/grocery_list.txt"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

ok()    { print -P "  %F{green}✓%f $1"; }
pause() { [[ -t 0 ]] && read -k 1 "?Press any key to close."; }   # only when run in a Terminal window
fail()  { print -P "\n  %F{red}✗ $1%f\n"; pause; exit 1; }
step()  { print -P "\n%B$1%b"; }

print -P "%B🛒 axe setup%b  ($REPO)"
cd "$REPO" || fail "can't open $REPO"

step "1/5  Checking your Mac"
[[ "$(uname)" == "Darwin" ]] || fail "axe supports macOS only for now."
ok "macOS $(sw_vers -productVersion)"

PY=""
for c in python3.13 python3.12 python3; do
  if command -v $c >/dev/null && $c -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
    PY=$(command -v $c); break
  fi
done
[[ -n "$PY" ]] || fail "Python 3.12 or newer is needed.
    Install it with Homebrew:  brew install python@3.12
    or from https://www.python.org/downloads/macos/  -- then run Setup again."
ok "Python $($PY -c 'import platform; print(platform.python_version())')"

step "2/5  Checking Claude Code (it chooses the products)"
command -v claude >/dev/null || fail "Claude Code isn't installed.
    Install it from https://claude.com/claude-code , run  claude  once in Terminal to sign in,
    then run Setup again."
ok "Claude Code $(claude --version 2>/dev/null | head -1)"
if (cd /tmp && claude -p "Reply with OK" --tools "" --no-session-persistence --output-format json 2>/dev/null) \
    | grep -q '"is_error":false'; then
  ok "signed in"
else
  fail "Claude Code is installed but not signed in.
    Open Terminal, run  claude  and sign in, then run Setup again."
fi

step "3/5  Installing axe (inside this folder only)"
[[ -x .venv/bin/python ]] || "$PY" -m venv .venv || fail "couldn't create the Python environment"
.venv/bin/python -m pip install -q --upgrade pip >/dev/null && .venv/bin/python -m pip install -q -e . \
  || fail "installing dependencies failed (are you online?)"
chmod +x "Grocery Compare.command"
ok "installed"

step "4/5  Your grocery list"
if [[ -f "$LIST" ]]; then
  ok "kept your existing list: grocery_list.txt"
else
  cp examples/grocery_list.txt "$LIST" && ok "created grocery_list.txt from the sample"
fi

step "5/5  Logging in to Zepto and Swiggy Instamart"
print "  A browser tab opens for each app (phone number + OTP). Already logged in? Nothing opens."
.venv/bin/python -m grocer login zepto     || fail "Zepto login didn't finish -- run Setup again to retry."
.venv/bin/python -m grocer login instamart || fail "Instamart login didn't finish -- run Setup again to retry."
ok "both apps connected"

print -P "\n%F{green}%B✓ All set!%b%f  In this folder ($REPO):
  1. Edit  grocery_list.txt  (it opens now) and save it.
  2. Double-click  Grocery Compare.command .\n"
[[ -t 0 ]] && open -e "$LIST"
pause
