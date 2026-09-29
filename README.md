# 🛒 axe — cheapest groceries across Zepto & Instamart

Write your grocery list in a text file, double-click once, and axe tells you **what to buy
on which app** so the total — **fees included** — is as low as possible.

- 🔎 Searches **Zepto** and **Swiggy Instamart** through their **official** MCP servers
- 🧠 **Claude** picks the right product for each line ("Tata Tuver dal" → Tata Sampann Toor Dal)
- 📦 Buys **your exact pack size**; if it isn't sold, identical packs that add up to it
  (2 × 500 g for 1 kg), else the best-value pack
- 💸 Reads **live bills** — delivery, handling, small-cart, surge and rain fees — and finds the
  cheapest split between the two apps
- 🛡️ **Never places an order** and leaves your carts as it found them

> **macOS only** for now.

## Why axe?

Existing price-comparison sites don't give a reliable answer for a real address. A live
audit on 28 Sep 2026 in Gota, Ahmedabad checked Smartprix, Quick Compare and Comparify
against what Blinkit, Zepto and Instamart actually showed for 10 everyday items:

- **Missing listings:** Quick Compare didn't list Zepto for 6 of the 8 items Zepto sells there;
  Comparify treated Instamart as unavailable and missed all 6 of its in-stock items.
- **The cheapest option gets hidden:** Tata groundnut oil 5 L was ₹1,549 on Zepto vs ₹1,789
  elsewhere, and two of the three sites didn't list the Zepto offer.
- **Wrong stock status:** two sites showed Amul Gold 500 ml out of stock on Instamart while it
  was in stock at ₹35.
- **No site splits your list** across apps, and none includes the fees you'd actually pay.

Full evidence, item by item: [`price-audit.html`](price-audit.html) (download or clone the
repo and open it in a browser).

---

## What you get

A real run for an 11-item list (30 Sep 2026, Gota, Ahmedabad). In the Terminal the savings
are in orange and the grand total sits on an orange band:

```
🛒 axe  ·  11 items from grocery_list.txt
✓ Searched 11 items on Zepto and Swiggy Instamart  11.2s
✓ Claude chose the product for each line  12.1s
✓ Live bills read · fees included  6.4s

╭───────────────────────────────────────────────┬──────────────────────────────────────────────╮
│ Swiggy Instamart  ·  7 items                  │ Zepto  ·  3 items                            │
├───────────────────────────────────────────────┼──────────────────────────────────────────────┤
│ Dettol Skincare Handwash Refill          ₹158 │ Tata Sampann Unpolished Toor Dal        ₹103 │
│   1.35 L · you asked 900 ml                   │   500 g                                      │
│ Amul Processed Cheese Block              ₹126 │ Tata Simply Better Groundnut Oil      ₹1,549 │
│   200 g                                       │   5 L                                        │
│ 2 × NOICE Idli Dosa Batter                ₹78 │ Amul Salted Butter                      ₹310 │
│   500 g each · = your 1 kg                    │   500 g                                      │
│ NOICE Idli Dosa Batter                    ₹39 │                                              │
│   500 g                                       │                                              │
│ 2 × Tender Coconut                       ₹170 │                                              │
│   1 pc each · = your 2 pcs                    │                                              │
│ Tata Salt Iodised                         ₹28 │                                              │
│   1 kg                                        │                                              │
│ Amul Fresh Paneer                         ₹92 │                                              │
│   200 g                                       │                                              │
├───────────────────────────────────────────────┼──────────────────────────────────────────────┤
│ Items                                    ₹691 │ Items                                 ₹1,962 │
│ Handling fee                              ₹12 │ Delivery fee                            FREE │
│ Delivery partner fee                     FREE │                                              │
│ Late night fee                             ₹9 │                                              │
│ GST and charges                         ₹1.62 │                                              │
├───────────────────────────────────────────────┼──────────────────────────────────────────────┤
│ To pay                                   ₹714 │ To pay                                ₹1,962 │
╰───────────────────────────────────────────────┴──────────────────────────────────────────────╯
                                       Grand total · 10 of 11 items   ₹2,676                                       

How this split compares
This split               ████████████████████▏       ₹2,676           
All on Zepto¹            ████████████████████▍       ₹2,703 ₹27 saved 
All on Swiggy Instamart² ██████████████████████▌     ₹2,984 ₹308 saved
At MRP (items only)      ██████████████████████████  ₹3,453 ₹800 saved
¹ NOICE Idli Dosa Batter from Swiggy Instamart
² Amul Salted Butter from Zepto

✓ 10 of 11 found · 7 exact size · 2 as several packs · 1 nearest size
✗ Amul Gold milk pouch: sold out right now
↓ Tata Simply Better Groundnut Oil is ₹303 cheaper on Zepto (₹1,549 vs ₹1,852 on Swiggy Instamart)

→ Add these in your Zepto and Swiggy Instamart apps. Nothing was ordered.
Prices and fees live at 30 Sep, 12:00 AM · full details: grocery_plan.txt
```

*Your prices depend on your address and the moment you run it.*

---

## Before you start

| You need | Why | How to get it |
|---|---|---|
| A **Mac** | the launcher and setup are macOS scripts | — |
| **Python 3.12+** | axe is a Python tool | `brew install python@3.12` or [python.org](https://www.python.org/downloads/macos/) |
| **Claude Code**, signed in | Claude chooses the products (uses your Claude login, no API key) | [claude.com/claude-code](https://claude.com/claude-code), then run `claude` once in Terminal to sign in |
| **Zepto** and **Swiggy** accounts with a **saved delivery address** | prices, stock and fees depend on your address | in the phone apps |

## Quick start — 3 steps

**1. Get the code**

```sh
git clone https://github.com/abhishekashokchauhan/axe.git
```

**2. Double-click `Setup.command`** in the `axe` folder (or run `./Setup.command` in Terminal).
It checks the prerequisites, installs axe inside its own folder, creates your list
**`grocery_list.txt`** from the sample (an existing list is never overwritten), and opens a
browser tab to log in to **Zepto** and **Swiggy** (phone number + OTP), once.

**3. Edit `grocery_list.txt`, then double-click `Grocery Compare.command`.** That's it.
Both live in the `axe` folder:

```
axe/
├── Setup.command             ← once, after cloning
├── grocery_list.txt          ← your list (created by Setup; not tracked by git)
├── Grocery Compare.command   ← double-click to run
└── grocery_plan.txt          ← the latest result (not tracked by git)
```

Each run takes about 30 seconds. The result, plus the full details (Claude's choice for every
line, every live bill), is saved to **`grocery_plan.txt`**.
Tip: drag `Grocery Compare.command` to the Dock or Finder sidebar for one-click access.

> If macOS says the file "can't be opened", right-click it → **Open** → **Open**.

---

## Writing your list

One item per line: **name, pack size[, number of packs]**, up to **50 items** per run.

```
Amul butter, 500g
Tata groundnut oil, 5L
Noice idli batter, 1kg, 2        # two 1 kg lots
Tender coconut, 2 pieces
# lines starting with # are ignored
```

| Tip | Example |
|---|---|
| Name the brand if it matters | `Amul butter, 500g` → only Amul |
| Leave the brand out if it doesn't | `Tender coconut, 2 pieces` → any brand |
| Be specific for variants you care about | `Dettol Original liquid refill, 900ml` |
| Units: `g`, `kg`, `ml`, `l`, `pcs` / `pieces` | `500ml`, `1 kg`, `1L`, `6 pcs` |
| Spelling slips are fine | `idly batter` → idli batter |

## How axe decides

1. **Search** — every item on both apps, for your saved address.
2. **Pick the product** — Claude decides which listing *is* your item (right brand, right
   kind — toor dal, not chana dal; right format — a milk *pouch* isn't a tetra pack) and, among
   those that fit, the best value per kg/L. Premium variants (organic, …) get no preference.
   If nothing fits because it's sold out, the report says so instead of substituting.
3. **Pick the pack** (done by code, so it's predictable):
   your exact size → identical packs that make it up exactly → the best-value pack between
   0.75× and 2× your size. Only packs in stock in the quantity you need are considered.
4. **Get live bills** — the only way to see today's fees is a real cart bill, so axe briefly
   puts a basket in each app's cart, reads the bill (Zepto: order *preview*), and puts the
   cart back. It quotes the full list on each app, then whichever split looks cheapest, until
   the winning split is confirmed by live bills.
5. **Report** — both carts side by side with every fee, the grand total, how this split
   compares with buying everything on one app (and with MRP), and what to do next.

Both apps are searched in parallel (at most 4 requests at a time per app), and Claude
decides the list in parallel chunks, so a run takes about 30 seconds.

## What axe touches (and what it doesn't)

- ✅ **Never places an order.** Checkout, payment and order-confirmation tools are blocked in
  code; Zepto's order tool is only allowed as a preview (`confirmOrder: false`).
- ✅ **Carts are restored** after every quote, and checked again at the end of the run.
- ⚠️ The apps' MCP carts are **not** the carts you see in the phone apps, so axe can't hand you a
  ready cart — you add the items yourself (the report lists exact names and sizes).
- ⚠️ Don't edit your cart in the apps **while** a run is going (a quote may briefly replace it).
- 🔐 Logins are stored in `~/.grocer/` on your Mac (delete the folder to log out). Your list and
  results stay in the axe folder; product choices are made by Claude through your Claude Code login.

## Limitations & roadmap

- macOS only; Zepto and Swiggy Instamart only. Blinkit, BigBasket, Flipkart Minutes, Amazon Now
  and DMart have **no official MCP** yet — they'll be added when they do.
- Up to 50 items per run.
- Placing the order from axe (with your explicit confirmation) is planned.

## Uninstall

Delete the `axe` folder and `~/.grocer/`.

## For developers

```sh
.venv/bin/pytest                                   # tests
.venv/bin/python -m grocer --help                  # all commands
.venv/bin/python -m grocer compare --details       # also show Claude's choices and every live bill
.venv/bin/python -m grocer compare --list FILE     # run another list without editing grocery.yaml
.venv/bin/python -m grocer quote-test zepto        # live bill for the first list item only
```

Code lives in `grocer/`: `platforms/` (Zepto, Instamart adapters), `advisor.py` (Claude +
pack choice), `quote.py` / `livefees.py` (live bills), `optimizer.py` (the split),
`pretty.py` (the result screen), `report.py` (the detailed report in `grocery_plan.txt`).

## License

Copyright 2026 Abhishek Chauhan. Licensed under the [Apache License 2.0](LICENSE).

You may use, modify and distribute axe, including commercially, provided you keep the
copyright notice and license, state any changes you make, and include the [`NOTICE`](NOTICE)
file (which credits the author) with your distribution.

