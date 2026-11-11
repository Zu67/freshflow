// Optional frontend regression checks: node --test tests/test_frontend.cjs
// Runs the actual inline script with a minimal DOM; no packages required.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const nodes = new Map();
const context = vm.createContext({
  document: {querySelector: s => {
    if (!nodes.has(s)) nodes.set(s, {});
    return nodes.get(s);
  }},
  fetch: () => new Promise(() => {}),
});
const html = fs.readFileSync(path.join(__dirname, '../static/index.html'), 'utf8');
vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1], context);

test('formatting follows the measure, including zero and rates above one', () => {
  for (const [value, unit, expected] of [
    [0.5, 'units', '0.5'], [0.5, 'dollars', '$0.5'],
    [0.5, 'percent', '50.0%'], [1, 'percent', '100.0%'],
    [1.5, 'percent', '150.0%'], [0, 'percent', '0.0%'],
    [null, 'percent', '—'],
  ]) assert.equal(context.fmtCell(value, unit), expected);
});

function card(unit, delta) {
  return {answerable: true, answer: 'Rows available.', restatement: '',
    backend: 'test', elapsed_s: 0, unverified_figures: [], sensitivity: null,
    assumptions: [], definitions: [], caveats: [],
    scope_note: {fresh_total: 0, all_total: 0, delta_pct: delta,
      departments_excluded: ['Grocery'], unit},
    rows: [{units_sold: 0.5}], compare_rows: [{units_sold: 0.25}],
    column_units: {units_sold: 'units'}, sql: 'SELECT', compare_sql: '', spec: {}};
}

test('zero scope totals render without a fabricated percentage', () => {
  context.render(card('units', null));
  const out = nodes.get('#out').innerHTML;
  assert.match(out, /Fresh only/);
  assert.doesNotMatch(out, /null|NaN|0\.0%/);
});

test('unit scope and both tables never display units as dollars or percentages', () => {
  context.render(card('units', 0));
  const out = nodes.get('#out').innerHTML;
  assert.doesNotMatch(out, /\$|50\.0%|25\.0%/);
  assert.match(out, />0\.5</);
  assert.match(out, />0\.25</);
});

test('dollar scope keeps its currency label', () => {
  context.render(card('dollars', 0));
  assert.match(nodes.get('#out').innerHTML, /\$0/);
});

test('planning failures are distinguished from missing extract data', () => {
  context.render({answerable: false, error: true, backend: 'test', answer: 'Bad plan', caveats: []});
  assert.match(nodes.get('#out').innerHTML, /Unable to answer/);
  assert.doesNotMatch(nodes.get('#out').innerHTML, /Not answerable from this extract/);
});

test('weekly report names item ids and does not draw a missing prior week as zero', () => {
  context.renderReport({
    team: 'finance', unit: 'dollars', comparable: false,
    week: {label: 'Apr 1–5, 2026'},
    prior_week: {available: false, label: 'outside the extract'},
    counted: ['Items: 201 item ids. Inclusion is by item_id.'],
    notes: ['There is no prior-week column and no increase ranking, because that week is not in the extract.'],
    departments_table: [{dept: 'Grocery', current: 12.5, prior: null, change: null}],
    department_total: {current: 12.5, prior: null, change: null},
    top_items: [],
    top_items_note: 'Increase is not ranked.',
    stores: [],
    included_items: [
      {item_id: 10008, description: 'Honeycrisp Apples', dept: 'Produce'},
      {item_id: 10009, description: 'Gala Apples', dept: 'Produce'},
    ],
    sql: {departments: 'SELECT 1', top_items: null},
  });
  const out = nodes.get('#report-out').innerHTML;
  assert.match(out, /Grocery/);
  assert.match(out, /10008 — Honeycrisp Apples/);
  assert.match(out, /10009 — Gala Apples/);
  assert.match(out, /no prior-week column/);
  assert.match(out, /Increase is not ranked/);
  assert.doesNotMatch(out, /\$0/);
});

test('store operations report renders one section per store in units', () => {
  context.renderReport({
    team: 'ops', unit: 'units', comparable: true,
    week: {label: 'Jun 22–28, 2026'},
    prior_week: {available: true, label: 'Jun 15–21, 2026'},
    counted: ['Departments: Produce, Dairy, Meat, Bakery, Deli.'],
    notes: [],
    departments_table: [], department_total: null, top_items: [], top_items_note: '',
    stores: [{
      store_id: 101, banner: 'Meridian Foods', region: 'Northeast',
      departments: [{dept: 'Produce', current: 4, prior: 1, change: 3}],
      department_total: {current: 4, prior: 1, change: 3},
      top_items: [{item_id: 10008, description: 'Honeycrisp Apples', dept: 'Produce', current: 4, prior: 1, change: 3}],
      top_items_note: '1 item increased versus the prior week, so fewer than ten are listed.',
    }],
    included_items: [{item_id: 10008, description: 'Honeycrisp Apples', dept: 'Produce'}],
    sql: {departments: 'SELECT store', top_items: 'SELECT item'},
  });
  const out = nodes.get('#report-out').innerHTML;
  assert.match(out, /Store 101 · Meridian Foods · Northeast/);
  assert.match(out, /Honeycrisp Apples/);
  assert.match(out, />4</);
  assert.doesNotMatch(out, /\$|Grocery/);
});

test('an answer is a sentence, with numbered sources at the bottom', () => {
  context.render({
    answerable: true,
    answer: 'In June, shrink cost $12, up from $9 in May.',
    restatement: 'shrink last month',
    headline: {
      parts: [
        {text: 'In June, shrink cost ', tone: ''},
        {text: '$12', tone: '', fig: true},
        {text: ', ', tone: ''},
        {text: 'up from $9 in May.', tone: 'up'},
      ],
      table: {
        headers: ['Department', 'June', 'May', 'Change'],
        rows: [[
          {text: 'Produce'},
          {value: 12, unit: 'dollars', tone: ''},
          {value: 9, unit: 'dollars', tone: ''},
          {value: 3, unit: 'dollars', tone: 'up'},
        ]],
      },
      sources: [
        {n: 1, label: 'Counted', text: 'June, 2026-06-01 through 2026-06-30.'},
        {n: 2, label: 'Formula', text: 'shipped minus sold.'},
      ],
    },
    backend: 'test', elapsed_s: 0, unverified_figures: [], sensitivity: null,
    assumptions: [], definitions: [], caveats: [],
    scope_note: null,
    rows: [{dept: 'Produce', shrink_cost: 12}], compare_rows: [],
    column_units: {shrink_cost: 'dollars'},
    column_labels: {},
    sql: 'SELECT 1', compare_sql: '', spec: {dimensions: ['dept']},
  });
  const out = nodes.get('#out').innerHTML;
  assert.match(out, /In June, shrink cost /);
  assert.match(out, /class="fig">\$12</);
  assert.match(out, /class="up">up from \$9/);
  assert.doesNotMatch(out, /<sup/);
  assert.match(out, />Produce</);
  const answerAt = out.indexOf('In June, shrink cost');
  const tableAt = out.indexOf('>Produce<');
  const sourcesAt = out.indexOf('>Sources<');
  assert.ok(answerAt !== -1 && tableAt > answerAt && sourcesAt > tableAt);
  assert.ok(out.indexOf('Counted') > sourcesAt);
  assert.ok(out.indexOf('We read the question as') > sourcesAt);
});

test('grocery is called out when its change dominates the finance total', () => {
  context.renderReport({
    team: 'finance', unit: 'dollars', comparable: true,
    week: {label: 'Jun 22–28, 2026'},
    prior_week: {available: true, label: 'Jun 15–21, 2026'},
    counted: ['Items: 201 item ids.'],
    notes: [],
    departments_table: [
      {dept: 'Produce', current: 17037.49, prior: 5915.82, change: 11121.67},
      {dept: 'Grocery', current: -25365.38, prior: 21885.56, change: -47250.94},
    ],
    department_total: {current: 11655.57, prior: 54892.21, change: -43236.64},
    top_items: [], top_items_note: '',
    stores: [], included_items: [],
    sql: {departments: 'SELECT 1', top_items: null},
  });
  assert.match(nodes.get('#report-out').innerHTML, /Grocery accounts for most of this change/);
});

test('both shows dollars and units side by side, ranked by the team measure', () => {
  context.renderReport({
    team: 'finance', unit: 'dollars', comparable: true, basis: 'both', ranked_by: 'shrink_cost',
    week: {label: 'Jun 22–28, 2026'},
    prior_week: {available: true, label: 'Jun 15–21, 2026'},
    counted: ['Items: 201 item ids.'],
    notes: [],
    departments_table: [
      {dept: 'Produce', current: 100, prior: 80, change: 20,
        also: {measure: 'shrink_units', unit: 'units', current: 500, prior: 400, change: 100}},
    ],
    department_total: {current: 100, prior: 80, change: 20,
      also: {measure: 'shrink_units', unit: 'units', current: 500, prior: 400, change: 100}},
    top_items: [
      {item_id: 10008, description: 'Honeycrisp Apples', dept: 'Produce', current: 50, prior: 10, change: 40,
        also: {measure: 'shrink_units', unit: 'units', current: 25, prior: 5, change: 20}},
    ],
    top_items_note: 'The ten largest increases.',
    stores: [], included_items: [],
    sql: {departments: 'SELECT 1', top_items: 'SELECT 2'},
  });
  const out = nodes.get('#report-out').innerHTML;
  assert.match(out, /Ranked by shrink in dollars/);
  assert.match(out, /Shrink in units/);
  assert.match(out, /Jun 22–28, 2026 · \$/);
  assert.match(out, /Jun 22–28, 2026 · units/);
  assert.match(out, />500</);
  assert.match(out, />\$100</);
});

test('store operations lists every store until one store or region is chosen', () => {
  const body = {
    team: 'ops', unit: 'units', comparable: true,
    week: {label: 'Jun 22–28, 2026'},
    prior_week: {available: true, label: 'Jun 15–21, 2026'},
    counted: ['Departments: Produce.'],
    notes: [],
    departments_table: [], department_total: null, top_items: [], top_items_note: '',
    stores: [
      {store_id: 101, banner: 'Meridian Foods', region: 'Northeast',
        departments: [], department_total: {current: 4, prior: 1, change: 3},
        top_items: [], top_items_note: ''},
      {store_id: 105, banner: 'Meridian Foods', region: 'West',
        departments: [], department_total: {current: 2, prior: 2, change: 0},
        top_items: [], top_items_note: ''},
    ],
    included_items: [],
    sql: {departments: 'SELECT store', top_items: null},
  };
  nodes.get('#store-filter').value = '';
  nodes.get('#region-filter').value = '';
  context.renderReport(body);
  let out = nodes.get('#report-out').innerHTML;
  assert.match(out, /Store 101/);
  assert.match(out, /Store 105/);
  assert.doesNotMatch(out, /\$|Grocery/);
  nodes.get('#store-filter').value = '105';
  context.renderReport(body);
  out = nodes.get('#report-out').innerHTML;
  assert.match(out, /Store 105/);
  assert.doesNotMatch(out, /Store 101/);
  nodes.get('#region-filter').value = 'Northeast';
  nodes.get('#store-filter').value = '';
  context.renderReport(body);
  out = nodes.get('#report-out').innerHTML;
  assert.match(out, /Store 101/);
  assert.doesNotMatch(out, /Store 105/);
  nodes.get('#store-filter').value = '';
  nodes.get('#region-filter').value = '';
});

test('debug stays available outside the customer header', () => {
  const header = html.slice(html.indexOf('<header>'), html.indexOf('</header>'));
  assert.doesNotMatch(header, /debug-toggle/);
  assert.match(html, /id="debug-toggle"/);
  assert.match(html, /id="debug"/);
});

test('the wait state names the current step and previews the answer shape', () => {
  const html = context.workingMarkup(
    ['Planning the query', 'Writing the answer'], 0,
    'The answer will lead with the number, then the comparison.');
  assert.match(html, /role="status"/);
  assert.match(html, /Planning the query/);
  assert.match(html, /class="skel lead"/);
  assert.match(html, /aria-hidden="true"/);
  assert.doesNotMatch(html, /%|percent-done/);
});
