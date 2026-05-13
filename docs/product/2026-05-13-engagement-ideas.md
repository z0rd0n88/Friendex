# StockXchange Engagement Brainstorm

**Date:** 2026-05-13
**Author:** product strategy pass
**Status:** ideation only — no code changes proposed

---

## Executive Summary

StockXchange already has the rarest thing in a Discord economy bot: **a price engine wired to real human behavior** (messages, voice time, reactions, VC pings, timeouts). The engagement opportunity is not to add features — it is to *expose* the signal the engine already produces back to players as **social leverage**: things they can talk about, bet on, conspire over, and react to in real time. The bot's premise turns every member into a public ticker; today most members never see anyone else's ticker move unless they `$price` it.

Optimizing for three principles:

1. **Surface the existing signal.** The price engine is the show. Push moves into chat, not into commands users have to type.
2. **Reward observation, not grind.** Mechanics that pay for *paying attention to other members* beat mechanics that pay for typing more messages.
3. **Drama before depth.** A weekly rivalry that ends in a public liquidation is worth ten new portfolio analytics screens.

The top-3 shortlist is built around those principles: a live ticker channel, prediction markets on milestones, and a weekly "Most Shorted" callout that turns the existing `short_liquidation_check` into a spectator event.

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Design Principles](#design-principles)
3. [Idea Table (by theme)](#idea-table-by-theme)
   - [Social mechanics](#social-mechanics)
   - [Progression](#progression)
   - [Scarcity events](#scarcity-events)
   - [Prediction markets](#prediction-markets)
   - [Seasonal cycles](#seasonal-cycles)
   - [Information asymmetry](#information-asymmetry)
   - [Spectator hooks](#spectator-hooks)
   - [Cooperative play](#cooperative-play)
4. [Top-3 Shortlist](#top-3-shortlist)
5. [Non-Goals (Explicit Rejections)](#non-goals-explicit-rejections)

---

## Design Principles

| # | Principle | What it rules in | What it rules out |
|---|-----------|------------------|-------------------|
| 1 | Surface existing signal | Push notifications, live ticker, public callouts | Hidden stats that require a command |
| 2 | Reward observation | Prediction markets, gifting, alliance trading | More grindable activity multipliers |
| 3 | Drama before depth | Public liquidations, rivalry weeks, leak events | Yet another analytics command |
| 4 | Premise integrity | Server-bounded gameplay using Discord-native UX | Web dashboards, federation, off-Discord clients |
| 5 | Layered-architecture friendly | Ideas that map onto existing repos/services in `docs/02-target-architecture.md` | Anything requiring a new persistence engine or multi-guild model |

---

## Idea Table (by theme)

Effort: **S** (≤2 days), **M** (3–10 days), **L** (>10 days)
Impact: Low / Med / High (gut-judged engagement lift for a 50–200-member active server)

### Social mechanics

| Idea | One-line description | Effort | Impact |
|------|----------------------|--------|--------|
| **Public rivalry weeks** | Two members opt in (`$rival @x`); 7-day side-bet pool where bystanders bet on whose price ends higher; winner takes pot minus 10% events-wallet cut | M | **High** |
| **Gifting (`$gift @user 5 shares`)** | Transfer owned shares to another user with no price impact; creates social currency for thank-yous, alliances, and stunt gifts | S | Med |
| **Alliance / co-ownership flag** | Two members who hold ≥10 shares of each other get a visible "alliance" tag in `$pf`; small mutual reaction-bonus | M | Med |
| **Bounties on a target's price** | `$bounty @x $200 above_120` — pays out to whoever first triggers a `$buy` after the target's price crosses $120 | M | Med |
| **Insider quotas** | Each user can post one "quote of the day" tied to their stock; reactions on that message count 2x toward their price tick | S | Low |

### Progression

| Idea | One-line description | Effort | Impact |
|------|----------------------|--------|--------|
| **Investor titles (tiered roles)** | Auto-grant Discord roles at portfolio milestones (Penny Trader → Whale → Tycoon); visible name color | M | Med |
| **Achievements with permanent perks** | First-to-short, first-100k-net-worth, 30-day streak — each unlocks one small perk (e.g., +1 daily reward, 5% lower trade fee) | M | Med |
| **Stock prestige reset** | At $1M net worth, opt to "prestige" — reset cash to $10k but keep a permanent 5% activity-tick multiplier and a gold ticker icon | M | Med |
| **Daily streak as compounding curve** | Replace flat 7-day bonus with `streak_bonus = $100 * streak` (capped at 30 days); makes streak loss painful | S | Med |
| **Per-stock analyst rating** | After a user has held a position 30+ days they can publish a one-line "rating" of that stock that appears in `$ticker` views | S | Low |

### Scarcity events

| Idea | One-line description | Effort | Impact |
|------|----------------------|--------|--------|
| **IPO window for new members** | First 48h after `$optin`, the new member's stock can only be bought by the first 10 traders; trades after 48h flow normally | M | Med |
| **Halving event** | Once per quarter, the activity-tick coefficient `K` is doubled for a single 24-hour window announced in advance — "Volatility Day" | S | **High** |
| **Limited share float per stock** | Cap the total outstanding long+short shares per user at a soft float (e.g., 500 × current_price/100); attempts beyond charge a premium | L | Med |
| **Buyback events** | Server admin can sponsor a buyback: events-wallet purchases up to N shares of any stock at +10% of market; creates artificial demand spikes | M | Low |

### Prediction markets

| Idea | One-line description | Effort | Impact |
|------|----------------------|--------|--------|
| **Milestone markets** | `$predict @x net_worth_50k by Friday` — yes/no pool; resolves automatically from `users_data["net_worth"]` snapshot at deadline | M | **High** |
| **Voice-time markets** | "Will @x spend ≥3 hours in VC this week?" — resolves from `activity.week.voice_minutes` | S | Med |
| **Daily price-direction pool** | Each morning, a free $50 bet on each stock — up or down by close. Cheap, viral, low stakes | S | Med |
| **Event prediction** | Manually-resolved pools: "Will @host go live on stream this weekend?" — admins resolve | M | Low |

### Seasonal cycles

| Idea | One-line description | Effort | Impact |
|------|----------------------|--------|--------|
| **Monthly tournament** | Top 5 net-worth gain over a calendar month wins from events wallet; resets month_start_net_worth comparison surface | M | **High** |
| **Themed weeks** | "Short Week" (cooldowns halved), "Voice Week" (VC minutes pay 2x), "Photo Friday" (media bonus doubled) | S | Med |
| **Holiday events** | Christmas: random gift-share airdrops. Halloween: a "haunted" stock each day gets a 10% spook discount/drop. Configurable via settings | M | Med |
| **End-of-year awards ceremony** | Auto-generated Discord post: biggest gainer, biggest loser, most-shorted, best fund manager, longest streak | S | Med |

### Information asymmetry

| Idea | One-line description | Effort | Impact |
|------|----------------------|--------|--------|
| **Research reports (`$research @x`)** | Costs $100; reveals 30-day price history graph + activity breakdown for one stock. Free public data is just current price | M | Med |
| **Rumor system** | Any user can `$rumor @x` for $50 — bot posts an anonymized "📰 Rumor: $x may be about to spike" message. False rumors hurt the *rumorer's* stock after 24h if price didn't actually move | M | **High** |
| **Insider leaks** | Once/day, a randomly chosen user gets a private DM revealing tomorrow's biggest predicted mover (based on current-week activity score). Optional opt-in for "Analyst" role | M | Med |
| **Earnings calls** | Once/week each user can opt to host a 1-paragraph "earnings statement"; readers who react boost the user's price 0.5% per reaction up to a cap | S | Low |

### Spectator hooks

| Idea | One-line description | Effort | Impact |
|------|----------------------|--------|--------|
| **Live ticker channel** | Dedicated read-only channel where the bot posts every 5%+ price move, every short opened, every liquidation. Auto-prunes old messages | S | **High** |
| **Liquidation drama posts** | When `short_liquidation_check` fires (currently stub), post a public message naming the liquidated short-holder, the target, the loss amount | S | **High** |
| **Big-mover end-of-day recap** | Daily auto-post at market close: top 3 gainers, top 3 losers, "stock to watch" (highest activity score) | S | Med |
| **Real-time `$buy` announcements (opt-in)** | Whale buys (>$1k single trade) auto-post to the ticker channel with the buyer's name | S | Med |
| **Live "now in VC" board** | A pinned auto-updating embed in the ticker channel showing who is currently in VC and their session-so-far minutes | M | Low |

### Cooperative play

| Idea | One-line description | Effort | Impact |
|------|----------------------|--------|--------|
| **Index funds** | A fund that auto-holds shares of every member in a role (e.g., "Mods Index"); investors get fractional exposure | L | Med |
| **Team challenges** | Roles compete weekly: aggregate net-worth gain of "Team Red" vs "Team Blue"; winning team gets a one-time activity multiplier | M | Med |
| **Co-managed hedge funds** | Allow two managers per fund; both can withdraw, both subject to penalty; needed for the spec's unimplemented multi-investor case | M | Low |
| **Pooled shorts** | Multiple users can pool collateral into a single short position to bypass individual cash limits; P&L splits proportionally | L | Low |

---

## Top-3 Shortlist

### 1. Live Ticker Channel + Liquidation Drama Posts (Spectator hooks)

**Mechanism:** A bot-owned read-only channel where the bot posts public messages whenever a "newsworthy" event fires. Newsworthy events all already exist as state transitions in `bot.py`:

- A price tick from `activity_price_step` exceeds ±5% in a single tick → post.
- `apply_trade_price_impact` triggered by `$buy`/`$short` above a threshold notional → post.
- The currently-stub `short_liquidation_check` fires → post a **big** message naming names.
- A 17% `DISCIPLINE_PENALTY` fires on timeout/ban → post (this is already comedic gold).
- An inactivity-decay event (4% drop after 4 hours quiet) → optional post on the *first* daily occurrence per user only, to avoid spam.

**Why it's high-leverage:** The price engine is doing the work already — `prices_data`, `apply_trade_price_impact`, and the activity tick loop all produce signal that nobody currently sees unless they manually `$ticker`. One small adapter (a new `TickerService` in the application layer, a new `TickerListener` cog, no domain changes) takes that signal and broadcasts it. Maps cleanly onto the layered architecture in `docs/02-target-architecture.md` — it is purely a new consumer of `PriceTickService` and `TradingService` events. Zero new game mechanics, maximum new visibility.

**Engine variables touched:** `prices_data` (read), `apply_trade_price_impact` (hook on exit), `short_liquidation_check` (hook on liquidation — also forces the team to finally implement this stub, killing the highest-risk item in the Phase 1 risk register).

**Command surface extension:** Adds `$ticker_channel set #channel` (admin) and `$ticker_mute` (per-user opt-out of being named in posts about their stock — but they still appear in price-move posts). No new player verbs.

---

### 2. Milestone Prediction Markets (Prediction markets)

**Mechanism:** Yes/no betting pools on objective, machine-readable outcomes about other members. `$predict @target net_worth_50k by 2026-05-20` opens a pool. Anyone can `$bet predict_id yes $200` or `$bet predict_id no $500`. At deadline, the bot reads the resolved metric directly from `users_data[target_id]` and pays out the winning side proportionally from the pool (minus a 5% events-wallet cut).

**Supported metrics** (all already tracked, no new instrumentation):
- `net_worth_<amount>` — reads `calculate_net_worth(target_id)`
- `price_<amount>` — reads `prices_data[target_id]["current"]`
- `voice_minutes_week_<amount>` — reads `users_data[target_id]["activity"]["week"]["voice_minutes"]`
- `streak_<n>` — reads `users_data[target_id]["daily"]["streak"]`
- `shorted_by_<n>_users` — counted across all `portfolio.short` entries

**Why it's high-leverage:** It directly monetizes **paying attention to other people**, which is exactly Principle 2. It re-uses every existing tracked metric in `users_data` and `prices_data` without changing the price engine. It also creates passive engagement — a prediction running on @alice keeps everyone watching @alice for a week. The target user gets a free attention boost; bettors get a reason to encourage or sabotage them. It pairs viciously well with the live ticker.

**Engine variables touched:** all of `users_data` (read-only for resolution), `funds_data["events_wallet"]` (5% cut destination). Adds one new persistence concept: an `open_predictions` table/dict with deadline, metric_type, threshold, yes-pool, no-pool, bettors. This fits cleanly as a new `PredictionRepository` and `PredictionService` in the target architecture.

**Command surface extension:** `$predict`, `$bet`, `$predictions` (list open), `$predictions_history` (my bets). Five new commands; one new repo.

---

### 3. Weekly "Most Shorted" Rivalry Cycle (Social mechanics + Seasonal cycles)

**Mechanism:** Every Monday 00:00 UTC the bot computes the top-3 most-shorted members (by aggregate shares-shorted across all `portfolio.short` entries). It announces them publicly in the ticker channel as "This Week's Targets." For the next 7 days:

- Each Target gets a **+10% activity-tick multiplier** (an incentive to actually be active and squeeze the shorts).
- Anyone who **closes a profitable short** against a Target during the week earns a "Bear of the Week" badge — a Discord role granted for one week.
- Anyone who **takes losses on a short** against a Target who finishes the week net-positive gets a "Burned" footnote in their next `$portfolio` view (cosmetic shame, not punitive).
- At Sunday 23:59 UTC the bot posts the final tally: which Targets squeezed their shorts, which shorts won, biggest single P&L of the week.

**Why it's high-leverage:** It turns the existing short mechanic into a recurring, named, public weekly event. The short flow today is invisible — opened privately, frozen at 30 min, liquidated by a stub task. Wrapping it in a named cycle creates **stakes, identity, and a deadline**. The +10% multiplier for Targets is a tiny tweak to the `compute_activity_return` function (multiply by a per-user weekly boost factor pulled from a new `weekly_target` field). Maps cleanly onto a new `WeeklyRivalryService` driven by the existing `WeeklyResetTask` in `docs/02-target-architecture.md`.

**Engine variables touched:** `users_data[*]["portfolio"]["short"]` (read, aggregated), `compute_activity_return` (adds a multiplier lookup), `prices_data` (no direct change — multiplier flows through existing tick), Discord roles (one transient role grant per week). The unimplemented `short_liquidation_check` benefits from this cycle: liquidations during a rivalry week become headline moments rather than silent state changes.

**Command surface extension:** `$rivalry` (current week's status), `$bears` (history of Bear of the Week winners). Two new read commands; no new write verbs from the player side — the cycle runs itself.

---

## Non-Goals (Explicit Rejections)

These ideas were considered and **rejected** with reasoning. The shortlist is sharper because these are off the table.

| Rejected idea | Why rejected |
|---------------|--------------|
| **Web dashboard / external trading UI** | Breaks the premise. The whole appeal is "the game is in your server's chat." A web app duplicates Discord's UX and fragments attention. The target architecture is explicitly single-process, Discord-only. |
| **Multi-guild federation / cross-server arbitrage** | Out of scope per `docs/02-target-architecture.md` Open-Question 12 resolution: "single-guild only for this refactor." Would require a `guild_id` column on every table and a re-architecture of `users_data` keying. |
| **ML-driven price predictions / sentiment analysis on messages** | Exceeds reasonable scope. Requires a model server, training data pipeline, and ongoing tuning. The activity-driven price engine already produces emergent behavior; adding ML obscures rather than reveals the mechanism. |
| **Premium currency / pay-to-win cash shops** | Anti-engagement. Turns a social game into a transaction. The $10k starting cash is intentionally a leveler — premium tiers would destroy that and create a two-tier server. |
| **Public leaderboard of "worst" members / mockery mechanics** | Anti-engagement (toxic). The 17% timeout/ban discipline penalty is already on the edge; explicitly amplifying shame as a feature (e.g., a "Loser of the Week" role) crosses into bullying and will drive opt-outs. The "Burned" footnote in #3 is intentionally a small cosmetic note in the offender's own portfolio view — visible only to them — not a public broadcast. |
| **Auto-trading bots / algorithmic strategies for users** | Conflicts with the layered architecture in two ways: it would require users to inject code into the application layer, and it would defeat Principle 2 by automating the "paying attention" that the game is supposed to reward. Power users will build them off-bot anyway via the Discord API; the bot shouldn't endorse it. |
| **Insider-trading mechanics that exploit Discord moderation knowledge** | Anti-engagement and creates perverse incentives. E.g., a moderator who can see queued bans could short the soon-to-be-banned user. Even with opt-in, this normalizes weaponizing private info. |
| **NFT / crypto integration of any kind** | Breaks the premise and adds compliance surface. The $ in this bot is a play currency in a single server. |
| **Voice chat sentiment scoring via audio capture** | Privacy nightmare. The bot already has voice-time tracking via state events; adding audio processing requires user consent flows the bot is not built for, and Discord ToS issues. Out of scope. |
| **A "marriage" mechanic where two users merge portfolios** | Considered as a social mechanic. Rejected because portfolio merging is irreversible at the data layer and creates ugly edge cases on breakup (who keeps the shorts? what about locked collateral?). The lighter-weight Alliance flag in the social mechanics table captures the engagement upside without the data-model damage. |
| **Replacing the `$` prefix with slash commands as a prerequisite for engagement features** | Out of scope for ideation. Migration to slash commands is a separate architectural decision; engagement ideas should work with whichever command surface ships. Both interfaces can route to the same application services. |
