# FreshFlow Analytics — design

FreshFlow answers questions over a fixed Meridian Markets extract. The model selects measures, filters, dates, and groups. Python builds SQL from the approved catalog and adds checks to the answer card. [README.md](README.md) covers setup and model access.

## Request flow

```text
CSV files → in-memory DuckDB
question → planner → validated QuerySpec → compiled SQL
                                             ↓
                                 current + comparison rows
                                             ↓
                                  sensitivity + caveats
                                             ↓
                                         narrator
                                             ↓
                                   numeric flags → card
```

[`warehouse.py`](src/freshflow/warehouse.py) loads items, daily sales, and shipments when the engine starts. It joins sales and shipments at store-item-day grain with a full outer join. Missing sales or receipt values become zero. The compiler joins the resulting fact table to item attributes.

[`planner.py`](src/freshflow/planner.py) sends the question and semantic catalog to a model. The reply contains either a refusal or a `QuerySpec`, the structured query parameters. Pydantic checks its shape. Catalog validation checks measure and dimension names, date ordering, and the selected sort field. Defaults come from the prompt and spec fields.

[`compiler.py`](src/freshflow/compiler.py) builds SQL using catalog expressions and quoted filter values. The engine runs it for the requested period and, when supplied, the comparison period. [`engine.py`](src/freshflow/engine.py) builds period, scope, filter, grouping, and ranking assumptions from the spec. Planner notes stay with the query disclosure. They are not mixed into those assumptions.

## Definitions and checks

[`semantic/meridian.yml`](semantic/meridian.yml) defines each measure's formula and SQL. Shipments are converted from cases to units. Shrink is shipped minus sold, in units or at item cost; it isn't a measured waste quantity. Rates divide shrink by shipments on the same basis.

The planner defaults to cost-based shrink and fresh scope: Produce, Dairy, Meat, Bakery, and Deli. Grocery or explicit department requests change scope through the plan. Relative dates use the extract window. These policies are represented in both the catalog and planner prompt.

[`checks.py`](src/freshflow/checks.py) compares the first paired shrink measure with its other basis. Comparisons check changes; grouped results can check ranking overlap. Fresh-scope level measures also get a total with fresh scope removed, retaining other filters. Rates skip that scope check. Caveats flag short shrink windows, daily shrink grouping, negative store-item balances, empty results, and periods beyond the extract.

[`narrator.py`](src/freshflow/narrator.py) writes the answer from the returned rows. It does not call a model. A single total is one sentence. A breakdown names the first rows and carries a table of every row. A numeric matcher then flags unmatched figures in that sentence using returned values, selected derived values, and numeric filter labels. It allows rounding and scaling, and skips years and small bare counts. Flags stay on the card; they don't block the answer or verify its meaning. The weekly report does not use this path.

## Limits

Catalog validation doesn't establish that the planner understood the question. Item filters match a whole word from the question, so "apples" does not include Apple Juice or Apple Pie. “Why” requests produce grouped comparisons, and the answer says the numbers do not show a cause. The tests cover selected deterministic behavior; there is no automated live-model evaluation suite. The app uses in-memory DuckDB and synchronous model calls, with no authentication, conversation history, caching, or streaming.
