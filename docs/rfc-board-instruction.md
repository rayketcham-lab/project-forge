# Build instruction — `/rfc`, the standards-gap board

> An implementation brief for Claude Code working in this repository.
> Read it end to end before writing code. Follow the repo's TDD rule:
> tests first, then implementation, then the full suite green.

## The idea, stated precisely

RFC 3161 exists, so timestamping has a standard. Plenty of equally load-bearing
things have **no** standard, and the absence is invisible until something
breaks. The example that started this: the post-quantum transition has
standardised signatures (ML-DSA, SLH-DSA) and a Merkle-tree story for some
constructions, but **revocation and enrolment have no PQ-native answer** — CRL
and CSR profiles that survive PQ signature sizes do not exist. That is a gap
with a clock on it.

The board's unit of value is therefore **not** "an RFC we wrote". It is a
**gap dossier**:

> a named missing standard, the evidence that it is genuinely missing,
> who is blocked by its absence today, what a solution would have to
> specify, and a skeleton Internet-Draft a human could take to a working
> group

That distinction is the whole design. An autonomously generated RFC is
standards spam. An autonomously assembled, well-evidenced gap dossier is the
thing a working-group participant cannot buy and does not have time to make.

## Why this fits here

This repo already runs an admission-gated, grounded, adversarially-reviewed
generation pipeline twice: `/pki` (v0.23) and `/money-bots` (v0.24). Both have
the same skeleton, and this board is the third instance of it:

```
grounded probe → pick ONE gap → generate a dossier → prior-art gate →
adversarial panel → admission gate → store or DROP
```

Reuse the skeleton. Do not invent a fourth architecture.

## Non-negotiables

1. **Nothing is ever submitted anywhere.** No IETF Datatracker submission, no
   mailing-list post, no GitHub issue filed by a cadence. The board produces
   artefacts for a human to carry. This is the repo's standing governance rule
   for autonomous cadences and it is not negotiable for a board that touches a
   standards community.
2. **A dossier with no evidence of absence is refused.** "There is no RFC for
   X" is a claim, and the gate must see the search that supports it: the RFC
   index, active and expired drafts, and the relevant working group's charter.
   An expired draft that already proposed this is the single most likely way
   to look foolish, and it must be found before the item is stored, not after.
3. **Never claim IETF status.** No generated text may imply it is an adopted
   draft, a WG item, or endorsed by anyone. Skeleton drafts render with an
   explicit "not submitted, not affiliated" header.
4. **Keyless-safe.** Like every other engine here, it must degrade to
   deterministic heuristics with no API key and no backend.

## Data model

Add to `models.py`:

```python
class RfcGapKind(StrEnum):
    MISSING_PROFILE   = "missing-profile"     # the primitive exists, no profile for this use
    MISSING_PROTOCOL  = "missing-protocol"    # no wire protocol at all
    MISSING_FORMAT    = "missing-format"      # no interoperable encoding
    MISSING_PRACTICE  = "missing-practice"    # no BCP where everyone improvises
    STALE_STANDARD    = "stale-standard"      # an RFC exists and is now wrong

class RfcGap(BaseModel):
    title: str                    # "PQ-sized CRL profile"
    kind: RfcGapKind
    area: str                     # IETF area/WG: LAMPS, TLS, ACME, PQUIP, …
    # The absence, evidenced. Each entry is a place searched and what was found.
    absence_evidence: list[str]   # >= 2 required by the gate
    # Existing standards this would sit next to — cited by number.
    adjacent_rfcs: list[str]      # >= 1 required
    # Who is blocked, concretely, today.
    blocked_parties: str
    # What a solution has to specify to be useful.
    required_semantics: list[str]
    interop_risk: str             # what breaks if two vendors guess differently
    # The strongest objection the panel raised that survived revision.
    surviving_objection: str | None = None
```

Add to `Idea`: `rfc_gap_score: float | None`, `rfc_gap: RfcGap | None` (JSON
column, tolerant loader — copy `_parse_bot_spec` in `storage/db.py`).

New categories, as their own board grouping (`RFC_CATEGORIES`), disjoint from
every existing board:

```
rfc-security-profile, rfc-pq-transition, rfc-operational-practice,
rfc-interop-format, rfc-protocol-gap
```

## The axis: `rfc_gap_score`

New module `engine/rfc_gap.py`, modelled on `engine/pki.py` and
`engine/bot_edge.py` (heuristic + LLM verify in a borderline band, plus
`admits()`).

The question the axis answers: **would filling this gap change what implementers
actually do?**

Score signals (heuristic, cheap, deterministic):

- an explicit clock (a deprecation, a compliance date, a forced migration)
- blast radius (how many implementations are affected)
- evidence of improvisation today (vendors shipping incompatible extensions)
- cited adjacent RFCs (the gap is positioned, not floating)
- a concrete interop failure mode

Penalties:

- vocabulary without substance ("a framework for holistic trust")
- the gap is a product idea wearing a standards costume
- a solution that needs no interoperability has no business being an RFC —
  this is the most common failure mode and must be penalised hard

`admits()` refuses: wrong board; fewer than two absence-evidence entries; no
adjacent RFC cited; below `RFC_ADMIT_THRESHOLD`; or prior art found.

## Grounding: `feeds/rfc_probe.py`

Keyless, best-effort, degrades to `[]`, same contract as `feeds/pki_probe.py`.

Sources to sweep:

- **RFC index / rfc-editor** for what exists and its status
- **IETF Datatracker** WG feeds — reuse the four already wired in
  `pki_probe.py` and add more areas
- **Expired and abandoned drafts** — the richest possible signal. A draft that
  expired without adoption is either a dead idea or an unfilled gap, and
  telling those apart is exactly the judgement this board should make
- **Errata** — a heavily-errata'd RFC is a stale-standard candidate
- **Implementation issue trackers** (already wired for PKI) where the phrase
  "there's no standard for this" appears in prose

Emit candidates with `{title, url, area, kind_hint, summary, gap_score}`.

## Prior art gate: `engine/rfc_prior_art.py`

Model it on `engine/pki_prior_art.py`. Cheap searches, fail **open** (a rate
limit must never masquerade as "this already exists"), but check:

- the RFC index for a matching title or abstract
- active drafts in the relevant WG
- expired drafts (search by keyword, then judge: "expired for lack of
  interest" versus "expired when the author changed jobs")

Store the near-misses on the dossier: they are the citations that make it
credible.

## Adversarial panel: `engine/rfc_depth.py`

Four lenses, copy the calibration approach from `engine/bot_depth.py`
(severity is about **fixability**, and one rewrite plus a re-check of the
hardest lens before anything is killed):

| Lens | Attacks |
|---|---|
| `exists` | It is already standardised, or an expired draft did it. Name it. |
| `unnecessary` | This needs no interoperability — one vendor could just ship it. If two independent implementations do not need to agree, it is not an RFC. |
| `unimplementable` | Wire-format, size, or state-machine reasons this cannot be built. Do the arithmetic on sizes and round trips. |
| `unadoptable` | Nobody would deploy it: no incentive, no migration path, no one to run it. |

## Cross-engine value — the part worth building carefully

The user's actual ask is collaboration between engines, and that is where this
earns its keep. Wire three edges, each behind a test:

1. **RFC gap → `/pki`.** A stored gap in `rfc-pq-transition` or
   `rfc-security-profile` becomes a seed for the PKI probe: "this standard is
   missing; what tooling can exist anyway, and what would it have to assume?"
   Tooling that anticipates a standard is exactly the PKI board's shape.
2. **RFC gap → `/sniper`.** An unfilled interop gap is a commercial wedge: the
   vendor who ships the reference implementation of a missing profile defines
   it in practice. Feed the gap in as an incumbent-free snipe seed.
3. **Existing ideas → RFC gaps.** Sweep stored ideas across every board for the
   sentence pattern "there is no standard/agreed format/common profile for X"
   — the corpus already contains these observations as asides. Promote them to
   candidate gaps. This is the cheapest source of real gaps in the system and
   it costs one SQL query plus a classifier pass.

Implement 3 first: it needs no new network source and it mines value already
sitting in the database.

## Surfaces

- `/rfc` board: dossier cards showing the gap, the absence evidence (as
  citations), adjacent RFCs, who is blocked, and the surviving objection.
  Follow `/money-bots` for layout: collapsible reference blocks, no wall of
  text, and the probe log visible so quiet cycles are legible.
- `GET /api/rfc/top` — same shape discipline as `/api/money-bots/top`: return
  the dossier, not just a title.
- `POST /api/rfc-draft/{idea_id}` — render a skeleton I-D (markdown, xml2rfc
  v3 optional) into `<db dir>/drafts/`. Local only. Never submitted. The
  header must state that it is unsubmitted and unaffiliated.
- Nav entry, and a row in the README boards table.

## Cadence

`_fire_rfc_gap` in `lifespan_scheduler.py`, every 4 hours, watermark on
`rfc_probes.probed_at`. **All blocking work goes through `asyncio.to_thread`**
— the v0.24 money-bot cadence froze the whole web app by calling a blocking
LLM backend inline, and the same mistake here would be worse because this
board's panel is larger.

## Definition of done

- [ ] Tests written first; full suite green; `ruff check` and
      `ruff format --check` clean
- [ ] Board renders with real generated dossiers, not fixtures
- [ ] A quiet cycle stores nothing and says why in the probe log
- [ ] Prior-art gate demonstrably kills a dossier for an existing RFC
- [ ] At least one cross-engine edge live, with a test proving a gap seeds
      another board
- [ ] No cadence touches GitHub, the IETF, or any mailing list
- [ ] README boards table and axis table updated; version bumped in
      `src/project_forge/__init__.py` (the SSOT) and the README badge
