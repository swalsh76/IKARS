# Kalshi's native order options, and what IKARS must build (PLAN D14)

*Read from Kalshi's API reference on 2026-10-26 (Create Order V2, Create Order Group), for slice M1.2. IKARS sends
no order before M7; this page is the input to D14's custom order types and to M7's proposal.*

## What Kalshi offers natively

Orders are created with `POST /portfolio/events/orders` (V2). Every order is a **priced limit order on the YES
book**:

| Field | Values | Notes |
|---|---|---|
| `side` | `bid` (buy YES) or `ask` (sell YES) | The NO side is expressed on the YES book: buying NO at `p` is an `ask` at `1 - p`. |
| `price` | dollars, 2 to 4 decimals | Valid steps depend on the market's price-level structure. |
| `count` | contracts, up to 2 decimals | Fractional contracts down to 0.01. |
| `time_in_force` | `good_till_canceled`, `immediate_or_cancel`, `fill_or_kill` | Required. There is no "good till time" value. |
| `expiration_time` | Unix seconds | Only with `good_till_canceled`: a resting order that lapses at a set time. |
| `post_only` | boolean | Maker-only: the order is refused rather than take liquidity. |
| `reduce_only` | boolean | Only with `immediate_or_cancel`. Caps the count at the current position. |
| `self_trade_prevention_type` | `taker_at_cross` or `maker` | Required. |
| `cancel_order_on_pause` | boolean | A resting order is cancelled if the exchange pauses trading. |
| `order_group_id` | text | Puts the order in an order group (below). |
| `client_order_id`, `subaccount`, `exchange_index` | | Identity and routing. |

**Order groups** (`POST /portfolio/order_groups/create`): a group has a **contracts limit per rolling 15-second
window**. When the limit is reached, Kalshi **cancels every order in the group** and blocks new ones until the group
is reset. There can be up to 100,000 groups.

**Not native:** a market order, stop and stop-limit, take-profit, trailing stop, OCO or bracket, conditional or
if-touched orders, time-triggered entries, iceberg, pegged orders, and pair or hedge orders.

## How each D14 custom type maps

| Custom type | Built from | Kalshi's part |
|---|---|---|
| Market-equivalent exit | IOC at a protective bound | `immediate_or_cancel` with a worst acceptable `price`, plus `reduce_only` for an exit. |
| Stop / stop-limit | The engine watches the trigger, then sends the exit | IOC (stop) or a GTC limit (stop-limit). **Nothing rests on Kalshi until the trigger fires**, so it protects nothing while the engine is down. |
| Take-profit | Can **rest natively** as a GTC limit at the target | `good_till_canceled` and `post_only`. This one does work while the engine is down. |
| Bracket / OCO | One leg rests (the take-profit) and the engine watches the other (the stop); a fill on one cancels the other | Order groups cap the *quantity* filled across the group within 15 s. That is close to "one cancels the others" for a cap equal to the position, but it is not a true OCO; the engine still does the cancel. |
| Trailing stop | The engine tracks the high and sends the exit | IOC at a bound. |
| If-touched / conditional | The engine's trigger (another market, a source item, spot, a book pattern) | Any order type. |
| Time-based | The engine's clock for entries; **native for expiry** | `expiration_time` lapses a resting order at a set time. |
| Scale-in / ladder | Several limits | Several GTC orders, optionally in one order group, which also caps the total filled. |
| Iceberg | The engine re-posts a small visible size after each fill | GTC and `post_only`. **To be cleared by M7's compliance review** (G17). |
| Peg | The engine re-prices a resting limit when the best bid or mid moves | Amend or cancel-and-replace. **To be cleared by M7's compliance review**, which covers fast cancel and replace. |
| Pair / hedge | The engine places the second leg after the first fills | Any order type; a perp hedge after M1.10's research. |

## Native safety features to use in M7

- **Order groups as a circuit breaker:** a contracts limit per 15 s on every group of IKARS's orders, so a runaway
  rule is cut off by Kalshi itself, not only by IKARS's own risk layer.
- **`cancel_order_on_pause`** on every resting order.
- **`expiration_time`** on every resting order, so nothing outlives the reason it was placed. This is SPARQ's rule
  that "every resting quote carries an expiry".
- **`post_only`** on every order meant to be a maker.
- **`reduce_only`** (with IOC) on every exit, so an exit can never open a position the other way.

## Open points for M7

- Amending (price and size) under V2: the endpoint and whether an amend loses queue position.
- Order-group reset and trigger semantics (their pages were not read for this note).
- Kalshi's rulebook on rapid cancel and replace, and on hidden size, for iceberg and peg (G17).
