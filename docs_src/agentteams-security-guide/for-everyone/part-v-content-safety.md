# Part V — Content safety

## The inspector reading everything that comes in {#S15}

An automatic inspector reads each incoming line looking for two kinds of trouble:
sneaky trick-text trying to give the helpers orders, and sensitive material like
personal file paths, passwords, keys, tokens, and random-looking secrets. Before
it reads, it cleans the text up to defeat disguises such as invisible or look-alike
characters. Anything serious triggers a stop; lesser finds trigger a
pass-with-conditions. The honest ceiling, in plain words: the inspector can still
be fooled by cleverly disguised text — it judges by shape and patterns, and it has
known gaps (for instance, it does not catch certain spreadsheet-formula tricks;
the guard covers that class by judgment instead). Fittingly, this very guide once
tripped the inspector with a harmless quoted example — a live reminder that
anything read is just data, not a command.

## Blanking out the live news before saving {#S16}

When the system compares one build to another, it first blanks out the live
security-news sections and their timestamps, so the comparison stays steady and no
live news text ever gets frozen in as if it were permanent. Incoming news from
outside sources is also cleaned before it is placed anywhere — spacing is
collapsed, risky markers are defanged, and length is capped — because text from
outside is untrusted until it has been tidied up.
