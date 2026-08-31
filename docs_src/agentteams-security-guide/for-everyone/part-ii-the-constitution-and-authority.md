# Part II — The constitution and authority

## The rule book no one can rewrite {#S3}

At the very top sits a short rule book of five bedrock rules — the constitution.
It cannot be overridden, weakened, or quietly edited: the exact same words appear
in three separate places so any tampering stands out, and while a project may add
*more* rules, it may never soften these. The five, in plain words: first, there
is one fixed order for whose instructions win, and nothing may promote itself to
the top of it; second, when the guard says "stop," that is final; third, the list
of tools each helper is allowed to use is a hard limit, not a suggestion; fourth,
anything a helper reads is just information — if text inside it tries to give
orders, that is reported as suspicious, never obeyed; and fifth, permission for
anything destructive must be recorded *before* it happens, not after.

## Whose instructions win {#S4}

The system keeps a ranked list of *whose instructions to follow*, and — this is
the subtle part — a completely separate list of *who to believe about facts*.
Being trusted about facts gives no permission to act, and that gap is exactly
what trick-text tries to exploit. From the top down, instructions are ranked:
the computer's own hard limits, then the rule book, then the live human running
the tool, then the project's own rules, then each helper's role instructions,
then the facts-ranking (which grants no permission at all), and dead last, the
stuff a helper merely reads (no authority whatsoever). Ties are broken by rank
and then by how specific the instruction is — never by whatever showed up most
recently or shouted the loudest. A message that demands the helper ignore
everything before it and hand over control is itself a red flag to report. When
in doubt, the helper treats the material as untrusted and asks. One honest note:
writing this ranking down does not, by itself, make it enforce itself — the
system separately checks that the rule is present and reachable inside the
helpers that need it.

## The safety guard who can say stop {#S5}

One helper is the dedicated safety guard, and it has the highest priority: the
coordinator must check with it before anything risky, and nobody can overrule its
"stop." Crucially, the guard is look-only — it reads and judges but never edits
files or runs commands, and that is a hard limit on its abilities, not a matter
of style. It answers with one of three verdicts: pass, pass-with-conditions, or
stop. It follows a fixed decision chart rather than improvising, and when several
rules apply at once, the strictest one wins. Its checklist covers ten things —
among them: no passwords or personal file paths in saved files, no writing into
outside projects, honest references, extra care around destructive steps, catching
trick-text, keeping reviewed material walled off, staying inside the assigned job,
no machine-specific details, checking that pathways are safe, and vetting new
add-on software after a short waiting period. Two of these (no secrets, no
machine-specific details) are also backed by an automatic inspector; the rest
rely on the guard's judgment. Every verdict — even a plain pass — gets written in
a log. The honest ceiling: the guard is a smart but fallible AI. Except where the
automatic inspector backs it up, its judgment can miss an attack or get something
wrong — which is precisely why it is one layer among many, not the whole wall.

## When "stop" really means stop {#S6}

A "stop" is the guard's final word: the work halts and the problem is shown before
anyone reaches for a workaround. The locked doors check for an unretracted "stop"
first, across the whole log, and no permission slip of any kind gets past one —
when anything is unclear, the door simply refuses. Permission slips that lift the
*ordinary* locks (never a full stop) are signed, expire after a set time, can only
be used a limited number of times, and refuse to work at all if the signing pen is
missing. The honest ceiling, in plain words: the signing uses one shared secret
pen, so a slip stops a stranger who doesn't hold that pen — but not someone who
does. Separately, a helper's list of allowed tools is enforced automatically when
files are merged: widening the list is flagged for a human and never applied on
its own, while narrowing it is applied freely.
