---
name: powerbi-analyst
description: "Answer questions from GB Corp's Power BI datasets: find the dataset, read its data dictionary, resolve names, write and run DAX, fix errors, report from the rows."
version: 0.1.0
author: GB Corp
license: Proprietary
metadata:
  hermes:
    tags: [power-bi, dax, data, analytics, kpi, gb-corp]
    category: data
    related_skills: [company-knowledge, microsoft-office]
---

# Power BI analyst

Use this skill whenever an employee asks for a number, trend, ranking or comparison that lives in
a GB Corp Power BI dataset (sales, branches, customers, targets, KPIs), in English or Arabic.

The tools come from the GB Power BI connection (`mcp__gb_powerbi__*`). Each one checks the
employee's access. Never ask for or handle credentials: there are none to handle.

## The flow (follow it in order)

1. **Pick the dataset.** Call `list_datasets`. If more than one could answer, pick by name; if it is
   still unclear, ask the employee which one. If the list is empty, say they have no Power BI
   access in GB Chat and stop.
2. **Read the overview.** Call `describe_dataset(dataset_id)`. Read the table routing, key measures,
   relationships, DAX rules and key facts. The DAX rules are mandatory.
3. **Read the detail you need.** Call `describe_dataset(dataset_id, tables=[...], measures=[...])`
   with only the tables and measures the question needs. This returns exact column names, measure
   definitions, exact string values for filters, query patterns and fix rules.
4. **Resolve names.** If the question names a customer, branch, region, product or any other
   entity, call `resolve_entities(dataset_id, <the question>)`. Use the returned exact values (and
   codes, when the correction says to filter by code) verbatim in filters. Never guess a spelling.
5. **Write one DAX query.**
   - Use only tables, columns and measures that `describe_dataset` returned. Never invent one.
   - Prefer existing measures (`[Total Sales]`) over re-aggregating columns.
   - Start with `EVALUATE`. Use `DEFINE` only for query-scoped measures or variables.
   - Wrap table names that contain spaces in single quotes: `'Dim Date'[Year]`.
   - Group with `SUMMARIZECOLUMNS`; rank with `TOPN`; a single number is `EVALUATE ROW("Label", [Measure])`.
   - Follow the matching query pattern when one exists.
6. **Run it.** Call `run_dax(dataset_id, dax)`.
7. **On an error, fix and retry, up to 5 runs in total.** Read Power BI's message, check the fix
   rules and the exact column names, change only what the error points at, and run again. After 5
   failed runs, stop: tell the employee what you tried and that the question could not be answered
   from this dataset. If `ask_powerbi` is available, you may offer it as a second opinion.
8. **On zero rows,** the filters matched nothing. Re-check names with `resolve_entities` and the
   string values before concluding that the answer is zero.
9. **Answer from the rows only.**
   - Lead with the answer and the number, with units and the period it covers.
   - Never state a figure that is not in the returned rows, and never extrapolate.
   - Say when rows were truncated (`truncated: true`), and give `row_count`.
   - Reply in the employee's language. Keep names exactly as the data spells them.
   - Offer the DAX you ran when the employee may want to check it.
10. **Make a file when asked.** For a chart, table or deck, build it from the same rows with the
    document skills (`microsoft-office`, `pdf`), and say which dataset and period it covers.

## Rules

- One question can take several queries (for example a total and a breakdown). Run each one.
- Ask before a query that would return thousands of rows; summarise instead.
- Data from Power BI is internal to GB Corp. Do not send it to web tools or outside services.
- If a tool says access is denied, tell the employee plainly. Do not try another dataset to get
  around it.
