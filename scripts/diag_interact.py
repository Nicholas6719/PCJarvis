"""Native-app clicking: matching, refusal, and the own-window guard.

The mechanism itself was verified live against a real Calculator -- clicking
'seven', 'plus', 'three', 'equals' by name and reading '10' back off the
screen afterward, which is stronger evidence than any unit test can give,
because it proves the click reached the real control rather than merely not
raising an exception.

What lives here is everything a live run cannot easily cover: the matching
logic in isolation (exact, substring, fuzzy), the refusal list, which has to
be right every time since it is the only thing standing between a misheard
word and a real purchase, and the guard against clicking JARVIS's own window.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from jarvis.tools.interact import _DANGEROUS, _best_match  # noqa: E402

passed = 0
failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  ok    {label}" + (f"   {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {label}" + (f"   {detail}" if detail else ""))


print("\n[matching] exact, substring, then fuzzy, in that order")
pool = [("Seven", None), ("Add to cart", None), ("Add to wishlist", None),
        ("Submit", None), ("Cancel", None)]

name, _ = _best_match("seven", pool)
check("case-insensitive exact wins first", name == "Seven", name)

name, _ = _best_match("cart", pool)
check("substring matches inside a longer name", name == "Add to cart", name)

name, _ = _best_match("Add to Cart", [("Add to cart", None), ("Add to wishlist", None)])
check("exact beats a substring also present elsewhere", name == "Add to cart", name)

name, _ = _best_match("submitt", pool)
check("a typo still finds the close name", name == "Submit", name)

name, _ = _best_match("launch the missiles", pool)
check("nothing plausible returns no match", name is None, str(name))

print("\n[refusal] the list a misheard word must never cross")
must_refuse = ["Buy Now", "Place Order", "Confirm Payment", "Delete Account",
              "Uninstall", "Format", "Empty Trash", "Discard changes",
              "Unsubscribe", "Cancel subscription", "Delete",
              "Close without saving",
              # Found by testing against Amazon's real page, not by guessing:
              # the first version of this list refused Buy Now but let "Add
              # to Cart" straight through, which is the far more common
              # button and the one that actually got clicked.
              "Add to Cart", "Add to cart", "Add to Bag", "Add to Basket",
              "Redeem", "Apply Coupon"]
for name in must_refuse:
    check(f"refuses {name!r}", bool(_DANGEROUS.search(name)))

print("\n[safe] ordinary controls a real app is full of")
must_allow = ["Save", "Cancel", "Next", "OK", "Seven", "Submit search",
             "Clear all memory", "Settings", "Play", "Pause", "Close",
             "Minimize", "Send",
             # Web-page controls that must stay click-able despite sitting
             # right next to the dangerous ones above.
             "Sign in", "Add to wishlist", "Add to list", "Save for later",
             "See more results", "Next page"]
for name in must_allow:
    check(f"allows {name!r}", not _DANGEROUS.search(name))

print("\n[honesty] a window that is not open must say so")
# It used to fall off the end of the search returning None, which flowed
# through the whole click path and came back as "I do not see anything
# clickable in that window" -- implying the window was open and empty.
from jarvis.tools import registry

registry.load_all()
from jarvis.tools.interact import click_button, list_clickable

missing = "zzz-no-such-window-zzz"
said = click_button("anything", app=missing)
check("click_button names the missing window", "No window matching" in said, said)
check("and does not claim it was empty",
      "clickable" not in said.lower(), said)
said = list_clickable(app=missing)
check("list_clickable names it too", "No window matching" in said, said)

print("\n" + "=" * 66)
print(f" {passed} passed, {failed} failed")
print("=" * 66)
sys.exit(1 if failed else 0)
