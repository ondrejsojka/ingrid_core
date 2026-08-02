# A persistent fillability oracle for ingrid_core

Status: a want, not a design. Written by the agent that spent an afternoon working
around its absence, so treat the opinions as load-bearing and the details as negotiable.

## The story

I am not filling a crossword. I am *choosing a template*, and there are millions of them.

The loop I actually run looks like this: place a word, ask "is this grid still fillable?",
keep it or undo it, repeat. On a 15×15 švédská a single constructor restart considers on
the order of 10⁴ candidate placements. Somewhere between 20 and 60 of them get accepted,
and the rest need to be rejected fast.

`ingrid_core` can answer that question *perfectly*. `Unfillable grid` is decided at
initial arc consistency and comes back before the search even starts — it is a proof, not
a heuristic, and it accounts for the dupe index and the shared-substring constraint,
which nothing I can write in Python does.

I cannot use it, because **every call costs about 4.5 seconds and essentially all of that
is loading the dictionary.** 160k entries, parsed from scratch, per question. The answer
itself is instant. So the economics are inverted: the oracle is free and the handshake is
ruinous.

What I did instead was write a bitset unary screen — for each slot, does at least one
word still match the induced pattern. It is necessary and it is not sufficient, and the
gap between those two words cost me most of a day:

- I pinned 22 theme entries into a template where every slot passed the screen. The real
  solver said `Unfillable grid` on all of them.
- I invented a stronger proxy (`--min-domain N`: every slot must keep ≥ N candidates) and
  swept it. Floor 1 → 14 pinned, unfillable. Floor 200 → 5 pinned, fills. A dial that
  trades away the thing I am maximising in exchange for a *probability* of feasibility.
- When I finally did call the real solver, I got the default 12-second timeout wrong for
  the grid size, every candidate came back "reject", and the tool printed
  **"nothing placeable, stopping"** — which reads exactly like a proof and is not one.

Every one of those is the same bug wearing a different hat: I had to guess at something
the engine already knows.

## What I want

A way to ask ingrid the fillability question thousands of times without paying for the
dictionary each time. That is the whole feature. Load once, ask many.

I do not care much what shape it takes. In descending order of how much I would enjoy it:

1. **A library entry point.** `GridConfig` already exists and the crate is already a lib.
   Something I can hold across calls and hand a template to. With a thin `pyo3` binding
   this collapses my constructor's inner loop from "serialise a grid, spawn a process,
   reparse 160k words, read stdout" to a function call, and I would restructure the whole
   search around it.
2. **A subprocess I keep open.** `ingrid_core --serve`, templates in on stdin, one verdict
   per line on stdout. Dumber, uglier, portable to any language, and it captures ~95 % of
   the value. If someone wants to ship this in an hour, ship this.

## What the answer should say

Three states, distinguished, because collapsing them is how I got burned:

- **unfillable** — a proof from arc consistency. I can prune with confidence.
- **fillable** — a fill was found. Ideally give me the fill; often I only need the bit.
- **unknown** — the budget ran out. This is *not* a rejection, and every tool that
  conflates it with one will eventually report a perfectly good grid as saturated.

The third one is the opinionated part. A boolean API here is a trap.

## What I would build on top, immediately

- **Real-oracle search.** Replace the unary screen in the constructor's accept step. Not
  a speedup — a different algorithm, because I can afford to be wrong and back out.
- **Honest saturation.** "No more theme entries fit in this template" would become a
  claim I can actually make, instead of "no more fit within a budget I guessed at".
- **Backtracking over pinned entries.** `pin_long.py` is greedy first-fit and stops at 4
  because reconsidering an earlier pin costs 4.5 s per probe. At sub-millisecond it is
  just a search.

## Things I do not want

- **Don't make it clever.** No incremental grid diffing, no caching of partial AC state
  across calls, no "warm" template handles. Reload the template every time; it is 225
  cells. The dictionary is the only expensive thing here.
- **Don't make me configure it per call.** Wordlists, min-score, `--max-shared-substring`,
  `--dupe-exempt-preferred` are fixed for a whole campaign. Set them at startup. If a
  campaign needs two policies, run two oracles.
- **Don't bundle the search.** I do not want "find me the best template". I want a fast
  yes/no. The search is mine and I want to keep it in a language I can iterate in.

## The other half

A fast oracle that answers `yes`/`no` still leaves the outer search doing rejection
sampling. The companion want is **`template-diagnostics-spec.md`**: the post-AC domain
profile and the `dom/wdeg` crossing weights, so a rejected geometry says *where* it is
tight instead of merely that it is. Throughput without signal just lets you sample the
same wall faster.

## Open questions I don't have answers to

- Is initial AC alone a good enough oracle, or does it pass too many templates that then
  fail deep in the search? My evidence says it is decisive for over-constrained grids —
  which is the case I care about — but I have not measured the false-accept rate.
- Should there be a `--probe-time` giving the caller a knob between "AC only" and "try a
  bit"? Probably, defaulting to AC only.
- Does the score column influence candidate *selection* at all, or only `--min-score`
  filtering? I designed a tier around the assumption that it does and never verified it.
  This matters more than it sounds: if scores steer selection, the tier build becomes the
  control surface for filler quality, and several other wishes collapse into it.

## Related, and now more urgent than I thought

I had "make clue availability a first-class dictionary property" as the next feature after
this one, on the grounds that a filler word with no ready clue is expensive. I was told,
correctly, that I am perfectly capable of writing clues. So scratch that: **corpus
attestation, not clue availability, is the quality signal for filler**, and it already
lives in `build_tier.py --gated --attest`. One less engine feature to want.

What survives of that line of thinking is the **score-weighted objective**
(`Σ score` instead of `count_preferred_words`), because what I actually want to express is
"maximise theme entries, but prefer attested filler, and penalise crosswordese" — three
terms, and the current objective can express none of them. That one is still worth doing,
but it is worth nothing until the question above about score semantics is answered.
