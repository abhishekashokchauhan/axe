# axe

Price a weekly grocery list on **Zepto** and **Swiggy Instamart** through their official
MCP servers, let Claude choose the right product for each line, and get the cheapest
split between the two apps -- with live fees.

## How it works

1. **Search** every list item on both apps (read-only).
2. **Choose products** -- Claude (via your local Claude Code login, `claude -p`) picks the
   product that fits each line; code picks the pack: your exact size, else identical packs
   that add up to it (2 x 500 g for 1 kg), else the best-value pack between 0.75x and 2x.
3. **Live bills** -- fees (delivery, handling, small-cart, surge, rain) are read from real
   cart quotes: the basket is put in the app's server-side cart, the bill is read, and the
   cart is put back as it was. Zepto's bill comes from its order *preview*
   (`confirmOrder: false`, enforced in code). Nothing is ever ordered.
4. **Optimise** -- every split is evaluated; baskets in the winning split are quoted live
   until the winner is fully verified.
5. **Report** -- both carts side by side, totals, and insights (MRP savings, single-app
   alternatives, exact vs nearest sizes, items not found).

## Use

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m grocer login zepto        # browser login: phone + OTP
.venv/bin/python -m grocer login instamart
.venv/bin/python -m grocer compare            # reads list_file from grocery.yaml
```

The list is a plain text file (default `~/Desktop/grocery_list.txt`), one item per line:

```
Amul butter, 500g
Noice idly batter, 1kg, 2      # 2 = number of packs
```

Settings (list location, addresses, Claude model, fees) are in `grocery.yaml`.
Login tokens are stored in `~/.grocer/` (outside the repo).

## Notes

- The MCP servers' carts are not the carts shown in the phone apps, so the tool never
  leaves anything in them; add the items in the apps yourself (checkout from the tool is
  planned).
- Tests: `.venv/bin/pytest`
