"""What a click must never be allowed to hit, wherever it happens.

One list, because there are now two places that click something by its name --
a native control through Windows UI Automation, a web element through the
DevTools protocol -- and a wrong click in either one is the same category of
mistake: not "the wrong song," but a purchase, a deletion, an account gone.
Keeping two copies invites them to quietly drift apart, so both import this
one instead.

The first version of this list was written by guessing at plausible button
text, and it missed something real: tested against Amazon's actual page,
"Add to Cart" was not in it, and got clicked. Cart and bag actions are not a
completed purchase, but they are the first step of one and a real change to
his account, and a list built from imagination rather than a real page is
exactly how that gap stayed invisible until something was actually clicked.
"""
from __future__ import annotations

import re

DANGEROUS = re.compile(
    r"\b(buy|purchase|order now|place order|pay|checkout|confirm payment|"
    r"add to (cart|bag|basket)|redeem|apply coupon|"
    r"subscribe|delete|remove account|deactivate|uninstall|format|erase|"
    r"empty trash|discard|unsubscribe|cancel subscription|"
    r"close without saving|don't save)\b", re.I)
