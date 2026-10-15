"""
Verbal opt-out detection ("remove me", "stop calling") on live calls.

Plan §09: internal opt-out is honoured immediately. This runs in the gateway
on every final caller transcript and writes DNC + revokes call consent —
it does not depend on the LLM noticing. False positives are deliberately
tolerated: wrongly adding a number to the DNC list is recoverable, dialling
someone who asked us to stop is not.
"""

import re

_PATTERNS = [
    r"\bremove (me|my (phone )?number)\b",
    r"\btake (me|my (phone )?number) off\b",
    r"\b(do not|don't|dont|never) (call|ring|phone) (me|this number|us)\b",
    r"\bstop (calling|ringing|phoning)\b",
    r"\b(put|add) (me|my (phone )?number) (on|to) (your |the )?(do not|don't) call\b",
    r"\bunsubscribe\b",
    r"\bno more (calls|phone calls)\b",
]
_OPT_OUT = re.compile("|".join(_PATTERNS), re.IGNORECASE)


def is_opt_out(transcript: str) -> bool:
    return bool(transcript and _OPT_OUT.search(transcript.replace("’", "'")))
