# User-reported message regression analysis (2026-11)

Analysis was performed against the actual `compose_message -> _search_query ->
_search_line_candidate` path, `extract_title`, `find_candidates -> _relate_candidates
-> _extract_build_pools`, and `process_source_event -> dedup_before_send`.
Telegram was not contacted; message behavior is tested locally.

## Per-example disposition

The `❓` shown in these samples represents the existing generated header where
provided; it is not source text in every case. The branch's current runtime is
reproduced by the focused regression tests in `tests/test_user_campaign_examples.py`.

| Example | Expected header / price | Actual root cause and safe disposition |
|---|---|---|
| Amazon mobile, 1,000 TL spend / 100 TL off | Campaign sentence incl. threshold and discount; no item price | `_search_line_candidate` deliberately strips money/discount words for product-search identity. Candidate scoring therefore loses campaign meaning and short teaser wins. New coupon-only path retains full sentence, treats spend/discount as campaign identity, and leaves `extract_price` returning none. |
| Boyner 2,000 / 500 code | “Boyner 2.000 TL'ye 500 TL İndirim Kodu”; no item price | Search-line cleaning removes the amount and discount phrase, leaving provider footer/merchant fragments eligible. The narrow coupon-only selector prioritizes the complete amount+discount sentence. |
| MODADA250 / 1,250 spend / 250 discount | “1250 TL VE ÜZERİ 250 TL INDIRIM”; code is not product name; no item price | Code-first heading can win generic title scoring. The campaign-only selector normalizes Turkish uppercase `INDIRIM` (dotless-I) and chooses the complete condition/discount line rather than the coupon code. |
| Amazon 4,000 / 1,000 | Amazon campaign sentence; no item price | Threshold classifier assigns the 4,000 as `threshold` and discount amount as `discount_amount`, correctly not an item price. Header loses these tokens in generic search cleanup. New path preserves campaign sentence and adjacent explicit Amazon merchant. |
| Suit with 2,727, 2,477 code, 2,527 coupon | Suit name/model retained; price depends on offer semantics | `extract_price` roles distinguish normal, `discounted`, basket/product coupon, threshold and discount amount. Existing logic avoids arithmetic/supposition; supplied only-incomplete semantics remain ambiguous. Regression verifies model/catalog ID survives title selection. |
| Dyson “Toplu ürün” + code | No product name and no price | `extract_title`/`_search_query` lack catalog identity; “Toplu ürün” is generic, seller is not product. Correct safe output is no fabricated product identity. No guessed title introduced. |
| Boyner repeated case | Same as above | Duplicate report; same root cause and coverage. |
| Trendyol 4,000 spend / 300 off | Full campaign sentence; no product price | Threshold/discount not sale price. Campaign-only route retains complete condition; code-only line is not chosen. |
| Amazon 4,000 spend / 1,000 off | Full Amazon campaign sentence; no product price | Same as Amazon condition case; marketing label plus condition sentence. |
| PUMA depot shoe, 3,516.75 → 1,352.79 | Full PUMA name; 1,352.79 TL | Price scan had both currency candidates as equally strong `normal`; `_extract_build_pools` then picked first candidate. Explicit arrow relation now marks only right-hand candidate as `discounted`, and normal title path keeps complete product name. |
| SONGMICS depot title, 1,499 → 971.10 | Full long title incl. RFB126B01; 971.10 TL | Same arrow price-role issue. Long title handling and catalog/model preservation already exist; no wide title truncation or metadata removal was added. |

## Cross-channel dedup / message IDs

Source-event suppression is keyed by `(chat_id, message_id)` in live/poll
in-flight and seen caches. This prevents the *same Telegram source event* from
being processed twice; it is not a cross-chat identity. Cross-chat consolidation
is an explicit product-level feature: `dedup_offer_key` is normalized title,
`dedup_before_send` may use strong model/title matching, and the configurable
cache/history window is 1–72 hours (12h default). Thus chat ID is intentionally
not in the offer key so independently posted copies of the same item can merge.
Price/condition/coupon `offer_terms` are compared after title identity; invalid
or missing event IDs do not establish source-event equality, and transport
failure paths are fail-open. The one user report supplies no two raw source
messages, chat IDs, message IDs, timestamps, or their observed outputs; therefore
there is no evidence to safely change cross-channel matching criteria from this
sample. Aggressively adding chat ID would disable desired cross-group dedup;
relaxing title match would increase false merges. Existing integration coverage
for same-product cross-channel merge, different model/price, explicit condition,
source counts and same-source poll/live replay remains green.

## Safe changes and limits

1. Coupon-only message path preserves a complete campaign sentence only when
   explicit coupon/code language and amount+discount text occur, and suppresses
   it when a model/unit-bearing product title exists. It cannot invent products.
2. Explicit old-price arrow current-price relationship makes the right side the
   final price. No general first/last-price preference or discount arithmetic.
3. No dedup algorithm changes: evidence in the supplied text does not establish
   an erroneous cross-group merge, and current repository already has extensive
   cross-channel regression tests. To diagnose the specific merging incident,
   retain anonymized raw bodies, source chat/message IDs, message times and
   generated notification IDs.

Known limitation: campaign title detection is conservative and relies on
explicit coupon/code plus amount/discount markers. Code-first posts without a
complete explanatory sentence and arbitrary campaign language may remain
unknown; this is preferable to turning a coupon identifier into a product name.
The source examples do not establish every claimed old generated result, so the
report distinguishes verified current behavior from expected corrections.
