#!/bin/zsh
# Grocery Compare -- double-click to price grocery_list.txt (in this folder) on Zepto +
# Swiggy Instamart and see what to buy where. It never changes your carts or places an
# order. The result is also saved to grocery_plan.txt in this folder.

REPO="${0:A:h}"   # this folder, wherever axe was cloned
LIST="$REPO/grocery_list.txt"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$REPO" || exit 1

if [[ ! -x .venv/bin/python ]]; then
  echo "axe isn't set up yet -- double-click Setup.command in this folder first."
  read -k 1 "?Press any key to close."; exit 1
fi
if [[ ! -f "$LIST" ]]; then
  cp examples/grocery_list.txt "$LIST"
  echo "Created grocery_list.txt from the sample -- edit it, save, then double-click Grocery Compare again."
  open -e "$LIST"; read -k 1 "?Press any key to close."; exit 0
fi

.venv/bin/python -m grocer compare 2>&1 | tee grocery_plan.txt
echo
read -k 1 "?Done -- also saved to grocery_plan.txt in the axe folder. Press any key to close."
