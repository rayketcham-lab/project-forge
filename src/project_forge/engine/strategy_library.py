"""The library of edges that already work — grounding for the money board.

Generation from a blank page reinvents "AI-powered trading bot" forever.
This module is the corpus of strategy PRIMITIVES that are publicly
documented and mechanically real: quoting for a rebate, capturing a venue's
published liquidity budget, holding a funding carry, closing a price
difference between two books, sweeping idle cash to the best rate.

Three consumers:

  1. Generation. A probed venue program plus one primitive from here is the
     seed: "this venue just published these mechanics; that mechanism is
     known to pay; what is the specific bot?" — a composition, not a guess.
  2. The board. The Playbook section on /money-bots renders this directly,
     so the operator can read the mechanism inventory without generating
     anything.
  3. The gate. `engine.bot_edge` treats a mechanism that matches a known
     primitive as evidence the yield source is real.

Deliberately NO deep links. Venue reward formulas, fee tiers, and program
terms change on their own schedule, and a citation that 404s reads as
authority while being worse than nothing. Every primitive instead carries
`verify_by`: the exact thing to go confirm on the venue's live docs before
a dollar moves. The live URLs come from `feeds.venue_probe`, which fetches
them at generation time.

Honesty is a hard constraint here, enforced by tests: every primitive must
state how its edge DECAYS and at least two ways it goes wrong. Nothing in
this file may describe a return as guaranteed or risk-free, because none of
them are — each one is a payment for taking a specific, nameable risk.
"""

from __future__ import annotations

import random

from pydantic import BaseModel, Field

from project_forge.models import BotVenueFamily, IdeaCategory


class StrategyPrimitive(BaseModel):
    """One known-working way a bot turns capital into income."""

    key: str
    name: str
    family: BotVenueFamily
    category: IdeaCategory
    # Where the money comes from, in one sentence a skeptic can attack.
    mechanism: str
    # The honest shape of the return — never a number pretending to be a
    # promise. "Small and frequent, capped by X" beats "12% APR".
    yield_shape: str
    capital_floor_usd: float = Field(ge=0.0)
    api_primitives: list[str] = Field(default_factory=list)
    # Why this stops paying. Every edge here has one.
    decay: str
    known_risks: list[str] = Field(default_factory=list)
    legality_note: str
    # What to go re-read on the venue's live docs before deploying capital.
    verify_by: str


# --------------------------------------------------------------------------- #
# The corpus                                                                  #
# --------------------------------------------------------------------------- #

STRATEGY_LIBRARY: tuple[StrategyPrimitive, ...] = (
    # --- incentive capture ------------------------------------------------ #
    StrategyPrimitive(
        key="liquidity-reward-minutes",
        name="Liquidity reward minutes",
        family=BotVenueFamily.PREDICTION_MARKETS,
        category=IdeaCategory.INCENTIVE_CAPTURE,
        mechanism=(
            "The venue pays a published reward budget to accounts holding resting two-sided "
            "quotes within a maximum spread of the midpoint, scored per minute and split "
            "pro-rata among qualifying makers. Income is the budget share, not the fill P&L."
        ),
        yield_shape=(
            "Steady while you qualify, proportional to your share of qualifying size — so it "
            "falls as other makers arrive, regardless of how well you trade."
        ),
        capital_floor_usd=250.0,
        api_primitives=[
            "order-book websocket for the midpoint",
            "post-only limit order placement",
            "bulk cancel/replace",
            "reward or earnings endpoint for reconciliation",
        ],
        decay=(
            "The budget is fixed per market and split pro-rata, so yield per dollar drops as "
            "capital arrives; venues also re-tune formulas and rotate which markets qualify."
        ),
        known_risks=[
            "fills are real trades — an informed counterparty can pick off the quote for more than the reward pays",
            "the midpoint moves and quotes silently stop qualifying unless actively re-centred",
            "capital is locked in resting orders and cannot be withdrawn instantly",
            "resolution risk on event markets held through settlement",
        ],
        legality_note=(
            "Participation in a venue's published, open-to-all liquidity program under its "
            "documented terms. No manipulation, no wash trading, no non-public information."
        ),
        verify_by=(
            "Read the venue's current rewards documentation: the qualifying spread, the "
            "per-market budget, the scoring interval, and any minimum size."
        ),
    ),
    StrategyPrimitive(
        key="maker-rebate-capture",
        name="Maker rebate capture",
        family=BotVenueFamily.CRYPTO_DEFI,
        category=IdeaCategory.INCENTIVE_CAPTURE,
        mechanism=(
            "On venues with a negative maker fee, every passive fill pays the account a rebate. "
            "A post-only bot earns the rebate on volume it would be flat on otherwise."
        ),
        yield_shape=(
            "Basis points per unit of volume — meaningful only at volume, and only where the "
            "rebate exceeds the adverse-selection cost of being filled."
        ),
        capital_floor_usd=1000.0,
        api_primitives=[
            "post-only order flag",
            "fee-tier or account-status endpoint",
            "fill stream for realised rebate accounting",
        ],
        decay=(
            "Fee schedules are revised, rebate tiers get harder to reach, and competing makers "
            "compress the spread that made passive fills survivable."
        ),
        known_risks=[
            "adverse selection can exceed the rebate on fast-moving books",
            "inventory accumulates on the wrong side and must be hedged or flattened at a cost",
            "rate limits and cancel ratios can push the account out of maker status",
        ],
        legality_note=(
            "Standard passive market participation under a published fee schedule available to "
            "every account at the same tier thresholds."
        ),
        verify_by="Confirm the venue's current maker fee is actually negative at your volume tier.",
    ),
    StrategyPrimitive(
        key="lp-incentive-farming",
        name="LP incentive farming",
        family=BotVenueFamily.CRYPTO_DEFI,
        category=IdeaCategory.INCENTIVE_CAPTURE,
        mechanism=(
            "A protocol pays emissions to liquidity providers on top of pool trading fees. The "
            "bot supplies liquidity only where emissions plus fees exceed expected divergence loss."
        ),
        yield_shape=(
            "Emissions-driven and front-loaded: highest at program launch, declining on a "
            "published or governance-set schedule."
        ),
        capital_floor_usd=500.0,
        api_primitives=[
            "pool state and reserves read",
            "add/remove liquidity transactions",
            "reward claim call",
            "on-chain price feed for divergence tracking",
        ],
        decay=(
            "Emissions schedules taper and end; TVL inflows dilute per-dollar rewards within days "
            "of a program being noticed."
        ),
        known_risks=[
            "divergence (impermanent) loss can exceed the incentives collected",
            "smart-contract failure or an upgrade that changes reward accounting",
            "reward tokens can be illiquid, so the quoted yield is not the realised yield",
            "gas and claim costs can consume small positions",
        ],
        legality_note=(
            "Supplying liquidity to a public protocol under published terms; no privileged access "
            "and no dependence on any counterparty's non-public information."
        ),
        verify_by="Check the live emissions rate, the program end date, and current pool TVL.",
    ),
    StrategyPrimitive(
        key="fee-tier-optimization",
        name="Fee-tier threshold holding",
        family=BotVenueFamily.BROKERAGE,
        category=IdeaCategory.INCENTIVE_CAPTURE,
        mechanism=(
            "Venues price fees by rolling volume or balance tier. Holding the cheapest tier that "
            "pays for itself converts an operating cost into a durable saving on every later trade."
        ),
        yield_shape="A cost reduction, not a return — worth exactly the fee delta times future volume.",
        capital_floor_usd=0.0,
        api_primitives=[
            "account tier / fee schedule endpoint",
            "rolling volume query",
            "order placement for scheduled qualifying flow",
        ],
        decay="Tier thresholds are raised and schedules are restructured, usually without notice periods.",
        known_risks=[
            "trading purely to reach a tier can cost more in spread than the tier saves",
            "tier resets on a rolling window can be missed by an unattended bot",
        ],
        legality_note=(
            "Using a venue's published fee schedule as written. Manufacturing volume through "
            "self-matching would be wash trading and is explicitly out of scope."
        ),
        verify_by="Pull the current tier table and compute the breakeven volume before routing anything.",
    ),
    StrategyPrimitive(
        key="staking-yield-routing",
        name="Staking and validator yield routing",
        family=BotVenueFamily.CRYPTO_DEFI,
        category=IdeaCategory.INCENTIVE_CAPTURE,
        mechanism=(
            "Protocols pay stakers for securing the network. A bot routes stake to the best net "
            "rate after commission and tracks unbonding periods so capital is never trapped."
        ),
        yield_shape="Protocol-set and relatively stable, but quoted before commission, slashing risk, and lockup.",
        capital_floor_usd=100.0,
        api_primitives=[
            "validator set and commission query",
            "delegate / undelegate transactions",
            "rewards withdrawal call",
        ],
        decay="Issuance schedules decline and commissions get repriced as more stake arrives.",
        known_risks=[
            "slashing for validator downtime or misbehaviour",
            "unbonding periods mean the position cannot be exited during a drawdown",
            "the reward asset's own price risk dominates the yield",
        ],
        legality_note=(
            "Ordinary participation in a public protocol's consensus rewards. Tax and, in some "
            "jurisdictions, securities treatment vary and belong in the operator's own review."
        ),
        verify_by="Confirm the current net commission, unbonding period, and slashing conditions.",
    ),
    # --- market making ---------------------------------------------------- #
    StrategyPrimitive(
        key="thin-book-quoting",
        name="Thin-book two-sided quoting",
        family=BotVenueFamily.PREDICTION_MARKETS,
        category=IdeaCategory.MARKET_MAKING,
        mechanism=(
            "On books with wide spreads and mostly uninformed flow, resting quotes on both sides "
            "earns the spread when both sides eventually fill."
        ),
        yield_shape="Small per round trip, dependent on fill frequency; long idle stretches are normal.",
        capital_floor_usd=500.0,
        api_primitives=[
            "level-2 book snapshot and updates",
            "limit order placement and cancellation",
            "position and balance query",
        ],
        decay="Other makers arrive and compress the spread that made the round trip worth taking.",
        known_risks=[
            "one-sided fills leave directional inventory in a market that then resolves against it",
            "informed flow around news picks off stale quotes",
            "thin books can gap through the quote entirely",
        ],
        legality_note=(
            "Providing genuine two-sided liquidity with real capital at risk — the ordinary "
            "function of a market maker, with no spoofing and no orders intended not to trade."
        ),
        verify_by="Measure realised spread and fill asymmetry on the target book before scaling size.",
    ),
    StrategyPrimitive(
        key="delta-neutral-quoting",
        name="Hedged quoting",
        family=BotVenueFamily.CRYPTO_DEFI,
        category=IdeaCategory.MARKET_MAKING,
        mechanism=(
            "Quote on the venue that pays the spread or rebate, then immediately hedge each fill "
            "on a deeper venue so the earnings are the spread, not a directional bet."
        ),
        yield_shape="Spread minus hedge cost minus fees on both legs — thin, and dependent on hedge slippage.",
        capital_floor_usd=2000.0,
        api_primitives=[
            "maker order placement on the quoting venue",
            "taker execution on the hedge venue",
            "cross-venue position reconciliation",
        ],
        decay=(
            "Hedge venue fees and slippage rise with size; the quoted spread narrows as competitors hedge the same way."
        ),
        known_risks=[
            "hedge leg fails or fills at a worse price, converting a flat book into a directional one",
            "capital must sit on two venues at once, doubling counterparty exposure",
            "transfers between venues are slow exactly when rebalancing is most needed",
        ],
        legality_note=(
            "Ordinary hedged market making across two venues where the operator holds accounts "
            "in good standing under each venue's terms."
        ),
        verify_by="Measure the true hedge cost, including slippage at working size, before quoting.",
    ),
    StrategyPrimitive(
        key="reward-window-quoting",
        name="Reward-window scheduled quoting",
        family=BotVenueFamily.PREDICTION_MARKETS,
        category=IdeaCategory.MARKET_MAKING,
        mechanism=(
            "Quote only during the venue's published reward or rebate windows, so exposure to "
            "adverse selection is taken only while the venue is subsidising it."
        ),
        yield_shape="Concentrated in the qualifying window; nothing outside it, by design.",
        capital_floor_usd=250.0,
        api_primitives=[
            "venue clock / program schedule endpoint",
            "scheduled order placement and cancellation",
            "reward accrual endpoint",
        ],
        decay="Windows get shorter, or the program moves to continuous scoring that removes the timing edge.",
        known_risks=[
            "concentrating quoting into known windows also concentrates competition into them",
            "a position opened in-window may need to be carried out of it",
        ],
        legality_note="Trading inside a venue's published program hours under its published rules.",
        verify_by="Confirm the current program schedule and whether scoring is windowed or continuous.",
    ),
    # --- cross-venue arbitrage -------------------------------------------- #
    StrategyPrimitive(
        key="outcome-set-underpricing",
        name="Outcome-set underpricing",
        family=BotVenueFamily.PREDICTION_MARKETS,
        category=IdeaCategory.CROSS_VENUE_ARBITRAGE,
        mechanism=(
            "When the prices of a complete, mutually exclusive outcome set sum to less than "
            "certainty after fees, buying every leg locks the difference at resolution."
        ),
        yield_shape="A fixed amount per set, realised at resolution — capital is tied up until then.",
        capital_floor_usd=500.0,
        api_primitives=[
            "multi-market price query for the full outcome set",
            "atomic or near-simultaneous multi-leg order placement",
            "settlement / resolution feed",
        ],
        decay="Other participants close the same sum; venues also tighten fees that made it visible.",
        known_risks=[
            "one leg fills and the others move, leaving directional exposure instead of a locked set",
            "the outcome set may not be genuinely exhaustive or mutually exclusive",
            "resolution disputes or ambiguous settlement criteria can break the assumed identity",
            "capital is locked until resolution, so the annualised return depends on duration",
        ],
        legality_note=(
            "Taking publicly displayed prices on a venue where the operator is eligible to trade. "
            "No manipulation and no reliance on non-public resolution information."
        ),
        verify_by="Read both markets' resolution criteria word for word and confirm they are identical.",
    ),
    StrategyPrimitive(
        key="cross-venue-event-gap",
        name="Cross-venue event price gap",
        family=BotVenueFamily.PREDICTION_MARKETS,
        category=IdeaCategory.CROSS_VENUE_ARBITRAGE,
        mechanism=(
            "The same event trades on two venues with independent books. Buying the cheaper side "
            "and selling the richer one captures the gap when both settle on the same criteria."
        ),
        yield_shape="Per-opportunity and episodic; total return depends on how often gaps exceed total cost.",
        capital_floor_usd=1000.0,
        api_primitives=[
            "price feeds on both venues",
            "order placement on both venues",
            "balance monitoring to keep both legs funded",
        ],
        decay="Venue fragmentation is the edge; as the same participants trade both books, the gap closes.",
        known_risks=[
            "settlement criteria differ subtly, so the legs are not actually the same bet",
            "capital stranded on two venues, each with its own withdrawal and solvency risk",
            "one venue may restrict or close the account, freezing one leg",
        ],
        legality_note=(
            "Legal only where the operator is eligible on BOTH venues under their terms and local "
            "law. Venue eligibility must be checked before any capital is committed."
        ),
        verify_by="Confirm account eligibility, withdrawal record, and identical resolution sources on both venues.",
    ),
    StrategyPrimitive(
        key="sportsbook-middling",
        name="Cross-book middling",
        family=BotVenueFamily.SPORTSBOOK,
        category=IdeaCategory.CROSS_VENUE_ARBITRAGE,
        mechanism=(
            "Two books post different lines on the same event. Taking both sides at the extremes "
            "produces a range where both tickets win, and a small known loss otherwise."
        ),
        yield_shape="Frequent small losses punctuated by a large payoff when the result lands in the middle.",
        capital_floor_usd=1000.0,
        api_primitives=[
            "odds feed from each book",
            "programmatic bet placement where the book's terms permit it",
            "settlement and balance reconciliation",
        ],
        decay="Books tighten lines, limit accounts that only take stale prices, or refuse the flow entirely.",
        known_risks=[
            "account limiting or closure is the normal response to consistent line-taking",
            "line moves between the two placements leave an unhedged position",
            "automated placement violates some books' terms of service outright",
            "variance is high — the middle is rare and the small losses are constant",
        ],
        legality_note=(
            "Only viable on venues whose terms permit programmatic placement, and only where "
            "sports wagering is legal for the operator. Terms of service must be read first: "
            "automating against a book that forbids it is a contract violation, not an edge."
        ),
        verify_by="Read each book's API terms for whether automated placement is permitted at all.",
    ),
    StrategyPrimitive(
        key="cex-dex-spread",
        name="Centralized-to-decentralized spread",
        family=BotVenueFamily.CRYPTO_DEFI,
        category=IdeaCategory.CROSS_VENUE_ARBITRAGE,
        mechanism=(
            "The same asset prices differently on an exchange book and an on-chain pool. Trading "
            "the gap captures the difference once gas and both venues' fees are covered."
        ),
        yield_shape="Episodic and highly competitive; the durable version is in long-tail pairs, not majors.",
        capital_floor_usd=2000.0,
        api_primitives=[
            "exchange order book feed and order placement",
            "on-chain pool quote and swap call",
            "gas estimation",
            "pre-positioned balances on both sides",
        ],
        decay="Majors are already contested by faster infrastructure; only underserved pairs stay open.",
        known_risks=[
            "transaction inclusion is not guaranteed, so the on-chain leg can miss",
            "gas spikes can turn a profitable gap into a loss after commitment",
            "bridge or transfer latency strands capital on the wrong side",
        ],
        legality_note=(
            "Trading public prices on public venues with the operator's own capital. No privileged "
            "ordering, no exploitation of contract bugs — an exploit is theft, not arbitrage."
        ),
        verify_by="Measure realised inclusion rate and true gas cost at working size on a testnet or small live size.",
    ),
    # --- basis / carry ----------------------------------------------------- #
    StrategyPrimitive(
        key="perp-funding-carry",
        name="Perpetual funding carry",
        family=BotVenueFamily.CRYPTO_DEFI,
        category=IdeaCategory.BASIS_CARRY,
        mechanism=(
            "When a perpetual trades above spot, longs pay shorts a periodic funding payment. "
            "Holding spot and shorting the perp collects that payment with the price exposure hedged."
        ),
        yield_shape="Accrues per funding interval while the rate is positive; can invert without warning.",
        capital_floor_usd=2000.0,
        api_primitives=[
            "funding rate history and next-funding endpoint",
            "spot purchase and custody",
            "perpetual short with margin management",
            "liquidation price monitoring",
        ],
        decay="Crowding compresses funding toward zero; regime changes flip it negative for long stretches.",
        known_risks=[
            "funding inverts and the carry becomes a cost",
            "the short leg can be liquidated on a spike even though the combined position is flat",
            "venue solvency and withdrawal risk on the leg that holds the margin",
            "spot and perp can be on different venues, adding transfer risk exactly when margin is needed",
        ],
        legality_note=(
            "A hedged position in publicly traded instruments on venues where the operator is "
            "eligible. Leverage and product eligibility differ sharply by jurisdiction."
        ),
        verify_by="Check the venue's current funding history, the margin formula, and the liquidation engine's rules.",
    ),
    StrategyPrimitive(
        key="cash-and-carry-basis",
        name="Cash-and-carry basis",
        family=BotVenueFamily.BROKERAGE,
        category=IdeaCategory.BASIS_CARRY,
        mechanism=(
            "A dated future trading above spot can be sold against a long spot position; holding to "
            "expiry captures the convergence as a known amount rather than a forecast."
        ),
        yield_shape="A known spread earned over a known holding period, quoted as an annualised rate.",
        capital_floor_usd=5000.0,
        api_primitives=[
            "futures term structure query",
            "spot and futures order placement",
            "margin requirement endpoint",
            "expiry and settlement calendar",
        ],
        decay="Basis compresses as rates and demand normalise; crowded windows close quickly.",
        known_risks=[
            "margin calls on the short leg before convergence",
            "carrying costs and financing rates can exceed the captured basis",
            "early assignment or contract specification differences on some products",
        ],
        legality_note=(
            "A standard hedged position in regulated listed products, executed through a broker "
            "the operator is onboarded with."
        ),
        verify_by="Compute the annualised basis net of financing, margin, and both legs' commissions.",
    ),
    StrategyPrimitive(
        key="lending-rate-spread",
        name="Lending rate spread",
        family=BotVenueFamily.CRYPTO_DEFI,
        category=IdeaCategory.BASIS_CARRY,
        mechanism=(
            "Supply rates differ across lending venues for the same asset. A bot moves capital to "
            "the best net supply rate and, where safe, borrows cheaply against it to lend richer."
        ),
        yield_shape="Modest and rate-driven; the leveraged version multiplies both the yield and the liquidation risk.",
        capital_floor_usd=1000.0,
        api_primitives=[
            "per-market supply and borrow rate query",
            "supply / withdraw calls",
            "health-factor or collateral-ratio monitoring",
        ],
        decay="Rates equalise as capital chases them; utilisation spikes make the quoted rate unrepresentative.",
        known_risks=[
            "smart-contract risk on every venue capital touches",
            "utilisation can hit the point where withdrawal is temporarily impossible",
            "leveraged loops liquidate on a collateral price move",
        ],
        legality_note=(
            "Supplying assets to public lending markets under published terms with no privileged "
            "access. Tax treatment of interest income is the operator's responsibility."
        ),
        verify_by="Check current utilisation, the interest-rate curve, and whether withdrawals are currently free.",
    ),
    StrategyPrimitive(
        key="securities-lending",
        name="Securities lending income",
        family=BotVenueFamily.BROKERAGE,
        category=IdeaCategory.BASIS_CARRY,
        mechanism=(
            "A broker's lending program pays the account a share of the borrow fee when its "
            "long inventory is lent out, which is largest in hard-to-borrow names."
        ),
        yield_shape="A rate on inventory already held — additive to a position, never a reason to open one.",
        capital_floor_usd=5000.0,
        api_primitives=[
            "lending program enrolment status",
            "borrow-rate query by symbol",
            "position and accrual reporting",
        ],
        decay="Borrow demand is transient; a hot rate collapses when the squeeze that caused it resolves.",
        known_risks=[
            "lent shares may lose certain rights while on loan",
            "the rate is set by the broker and can change daily",
            "the underlying position's own price risk dwarfs the lending income",
        ],
        legality_note=(
            "An opt-in program offered by regulated brokers under a written agreement; the terms "
            "and the revenue split are disclosed up front."
        ),
        verify_by="Read the broker's lending agreement for the revenue split and what happens to lent shares.",
    ),
    StrategyPrimitive(
        key="covered-call-income",
        name="Mechanical covered-call writing",
        family=BotVenueFamily.BROKERAGE,
        category=IdeaCategory.BASIS_CARRY,
        mechanism=(
            "Selling calls against stock already owned collects premium in exchange for capping the "
            "upside — a rules-based bot removes the discretionary drift that ruins the strategy."
        ),
        yield_shape="Regular premium income with a truncated upside; not a hedge against a real decline.",
        capital_floor_usd=10000.0,
        api_primitives=[
            "options chain query",
            "multi-leg order placement",
            "assignment and expiry notifications",
            "position-level greeks or delta reporting",
        ],
        decay="Premium shrinks in low-volatility regimes until it no longer pays for the capped upside.",
        known_risks=[
            "the underlying can fall far more than the premium collected",
            "assignment forces the sale of the underlying at an inopportune time",
            "tax treatment of assignment and rolls can be materially adverse",
        ],
        legality_note=(
            "A standard options strategy in a regulated brokerage account with the appropriate options approval level."
        ),
        verify_by="Confirm options approval level, assignment handling, and per-contract commissions.",
    ),
    # --- capital automation ------------------------------------------------ #
    StrategyPrimitive(
        key="treasury-sweep",
        name="Idle-cash sweep",
        family=BotVenueFamily.BROKERAGE,
        category=IdeaCategory.CAPITAL_AUTOMATION,
        mechanism=(
            "Idle balances earn nothing by default. A bot sweeps cash into the best available "
            "short-duration instrument and back out before it is needed."
        ),
        yield_shape="The prevailing short rate on capital that was earning zero — small, dependable, and boring.",
        capital_floor_usd=1000.0,
        api_primitives=[
            "balance and settlement query",
            "money-market or short-duration purchase and redemption",
            "scheduled execution",
        ],
        decay="The edge is operational, not market-based: it shrinks only if rates fall to zero.",
        known_risks=[
            "settlement timing can leave cash unavailable when it is actually needed",
            "redemption windows and cut-off times are easy to model wrong",
            "moving cash between institutions introduces transfer failure risk",
        ],
        legality_note=(
            "Ordinary cash management inside accounts the operator owns, using instruments the broker already offers."
        ),
        verify_by="Confirm settlement cut-offs, redemption timing, and any minimum holding period.",
    ),
    StrategyPrimitive(
        key="rules-based-rebalance",
        name="Band-triggered rebalancing",
        family=BotVenueFamily.BROKERAGE,
        category=IdeaCategory.CAPITAL_AUTOMATION,
        mechanism=(
            "Rebalancing only when an allocation drifts outside a band captures the mechanical "
            "sell-high/buy-low of the band without the turnover of calendar rebalancing."
        ),
        yield_shape="A small structural improvement over drift, realised over years — not a income stream.",
        capital_floor_usd=5000.0,
        api_primitives=[
            "position and valuation query",
            "order placement with tax-lot selection where supported",
            "scheduled evaluation",
        ],
        decay="Nothing decays here, but the benefit is small enough that costs and taxes can erase it.",
        known_risks=[
            "taxable realisation can exceed the rebalancing benefit",
            "bands set too tight generate turnover that costs more than the drift",
        ],
        legality_note="Automating the operator's own allocation policy in accounts they own.",
        verify_by="Model the after-tax, after-commission benefit of the chosen band before automating.",
    ),
    StrategyPrimitive(
        key="auto-compounding",
        name="Cost-aware auto-compounding",
        family=BotVenueFamily.CRYPTO_DEFI,
        category=IdeaCategory.CAPITAL_AUTOMATION,
        mechanism=(
            "Yield that sits unclaimed does not compound. A bot claims and reinvests at the "
            "interval where the compounding gain exceeds the transaction cost."
        ),
        yield_shape="An improvement on an existing position's yield, bounded by how often it is worth paying to claim.",
        capital_floor_usd=500.0,
        api_primitives=[
            "pending reward query",
            "claim transaction",
            "reinvest / deposit call",
            "gas estimation",
        ],
        decay="Purely operational — it stops mattering when fees rise or the underlying yield falls.",
        known_risks=[
            "claiming too often burns more in fees than it compounds",
            "each claim is another contract interaction and another chance to fail",
            "reward tokens may need to be swapped, adding slippage",
        ],
        legality_note="Automating claims on the operator's own positions under public protocol terms.",
        verify_by="Solve for the optimal claim interval using live fee levels, not assumed ones.",
    ),
    StrategyPrimitive(
        key="collateral-efficiency",
        name="Collateral efficiency automation",
        family=BotVenueFamily.CRYPTO_DEFI,
        category=IdeaCategory.CAPITAL_AUTOMATION,
        mechanism=(
            "Margin systems accept several assets at different haircuts. Posting the cheapest "
            "acceptable collateral frees the rest of the balance to keep earning."
        ),
        yield_shape="Recovered yield on capital that was posted and idle — a saving, not a new edge.",
        capital_floor_usd=2000.0,
        api_primitives=[
            "margin requirement and haircut schedule",
            "collateral deposit / withdrawal",
            "risk-level monitoring",
        ],
        decay="Haircut schedules change; a collateral asset can be repriced or de-listed outright.",
        known_risks=[
            "optimising collateral thins the buffer before a margin call",
            "the freed capital's new use may be illiquid exactly when margin is needed",
        ],
        legality_note="Using a venue's published margin rules inside the operator's own account.",
        verify_by="Re-read the venue's haircut table and margin-call mechanics before freeing any buffer.",
    ),
)


# --------------------------------------------------------------------------- #
# Selectors                                                                   #
# --------------------------------------------------------------------------- #


def by_category(category: IdeaCategory) -> list[StrategyPrimitive]:
    """Every primitive filed under *category*."""
    return [p for p in STRATEGY_LIBRARY if p.category == category]


def by_family(family: BotVenueFamily) -> list[StrategyPrimitive]:
    """Every primitive that lives in *family*'s venue universe."""
    return [p for p in STRATEGY_LIBRARY if p.family is family]


def pick_primitive(
    *,
    rng: random.Random | None = None,
    category: IdeaCategory | None = None,
    family: BotVenueFamily | None = None,
) -> StrategyPrimitive:
    """Pick one primitive to ground a generation pass.

    Filters narrow the pool but never empty it: a category with no
    primitives (every non-bot category) falls back to the whole library
    rather than raising on the caller.
    """
    chooser = rng or random.Random()
    pool = list(STRATEGY_LIBRARY)
    if category is not None:
        narrowed = [p for p in pool if p.category == category]
        pool = narrowed or pool
    if family is not None:
        narrowed = [p for p in pool if p.family is family]
        pool = narrowed or pool
    return chooser.choice(pool)


def library_prompt_block(primitives: list[StrategyPrimitive]) -> str:
    """Render primitives as grounding text for a generation prompt.

    Carries the mechanism and the decay together on purpose — an idea
    generated from the mechanism alone comes back claiming a permanent
    edge, which is the failure mode this whole board exists to avoid.
    """
    if not primitives:
        return ""
    blocks: list[str] = []
    for prim in primitives:
        blocks.append(
            f"### {prim.name} ({prim.family.value})\n"
            f"- Mechanism: {prim.mechanism}\n"
            f"- Return shape: {prim.yield_shape}\n"
            f"- Capital floor: ${prim.capital_floor_usd:,.0f}\n"
            f"- API surface: {', '.join(prim.api_primitives)}\n"
            f"- How it decays: {prim.decay}\n"
            f"- Known risks: {'; '.join(prim.known_risks)}\n"
            f"- Why it is legitimate: {prim.legality_note}\n"
            f"- Verify before deploying: {prim.verify_by}"
        )
    return "\n\n".join(blocks)
