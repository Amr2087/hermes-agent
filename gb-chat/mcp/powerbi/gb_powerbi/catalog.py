"""A dataset's data dictionary, rendered for the model.

Ported from Corp-ChatWithData `services/agent_powerbi/service/agent/infrastructure/schema_retriever.py`
(the same `schemas` and `rules` blocks tenancy stores per domain), so a domain configured for the
existing Power BI agent works here unchanged. The pipeline injected these fragments into its own
prompts; here the agent asks for them through describe_dataset.
"""

from __future__ import annotations

from typing import Iterable


class Catalog:
    def __init__(self, schemas: dict | None, rules: dict | None) -> None:
        schemas = schemas or {}
        self.index: dict = schemas.get("index") or {}
        self.tables: dict = schemas.get("tables") or {}
        self.measures: dict = schemas.get("measures") or {}
        self.reference: dict = rules or {}

    # ── overview ─────────────────────────────────────────────────────────────

    def index_context(self) -> str:
        idx, lines = self.index, []
        routing = idx.get("table_routing") or {}
        if routing:
            lines.append("TABLE ROUTING (pick the right table first):")
            width = max((len(k) for k in routing), default=0)
            lines += [f"  {use:<{width}} -> {table}" for use, table in routing.items()]
        summary = idx.get("tables_summary") or {}
        if summary:
            lines.append("\nTABLES OVERVIEW:")
            lines += [f"  {name}: {text}" for name, text in summary.items()]
        key_measures = idx.get("key_measures") or []
        if key_measures:
            lines += ["\nKEY MEASURES (frequently used):", "  " + ", ".join(key_measures)]
        for heading, values in (idx.get("key_columns") or {}).items():
            if values:
                lines += [f"\n{heading}:", "  " + ", ".join(values)]
        return "\n".join(lines)

    def default_tables(self) -> list[str]:
        declared = self.index.get("default_tables") or []
        if declared:
            return [t for t in declared if t in self.tables] or list(self.tables)[:2]
        return list(self.tables)[:2]

    def table_names(self) -> list[str]:
        return list(self.tables)

    def measure_names(self) -> list[str]:
        return list(self.measures)

    # ── detail ───────────────────────────────────────────────────────────────

    def tables_context(self, names: Iterable[str]) -> str:
        blocks = []
        for name in names:
            t = self.tables.get(name)
            if not t:
                continue
            quote = "'" if t.get("quote_name") else ""
            header = f"TABLE: {quote}{name}{quote}"
            if t.get("quote_name"):
                header += "   (ALWAYS wrap in single quotes — has spaces)"
            block = [header]
            if t.get("purpose"):
                block.append(f"Purpose: {t['purpose']}")
            block.append("Columns:")
            for col in t.get("columns", []):
                block.append(f"  {quote}{name}{quote}[{col['name']}]  -- {col.get('type', '?')}: {col.get('desc', '')}")
            if t.get("never_use"):
                block.append("NEVER USE (corrupted artifacts): " + " | ".join(t["never_use"]))
            blocks.append("\n".join(block))
        return "\n\n".join(blocks)

    def measures_context(self, names: Iterable[str]) -> str:
        blocks = []
        for name in names:
            m = self.measures.get(name)
            if not m:
                continue
            block = [f"[{name}]"]
            for key, label in (("what", "What"), ("source_table", "Source table"), ("dax", "DAX"),
                               ("grain", "Grain")):
                if m.get(key):
                    block.append(f"  {label}: {m[key]}")
            if m.get("requires_calendar_context") is not None:
                block.append(f"  Requires calendar context: {m['requires_calendar_context']}")
            if m.get("valid_group_by"):
                block.append("  Valid group by: " + ", ".join(m["valid_group_by"]))
            if m.get("invalid_group_by"):
                block.append("  Invalid group by: " + ", ".join(m["invalid_group_by"]))
            if m.get("notes"):
                block.append(f"  Note: {m['notes']}")
            blocks.append("\n".join(block))
        return "\n\n".join(blocks)

    # ── rules ────────────────────────────────────────────────────────────────

    def dax_rules(self) -> str:
        rules = self.reference.get("dax_rules") or []
        if not rules:
            return ""
        return "\n".join(["DAX RULES (follow strictly):", *[f"  {i}. {r}" for i, r in enumerate(rules, 1)]])

    def relationships(self) -> str:
        rels = self.reference.get("relationships") or []
        return "RELATIONSHIPS:\n  " + "\n  ".join(rels) if rels else ""

    def string_values(self, columns: Iterable[str] | None = None) -> str:
        sv = self.reference.get("string_values") or {}
        if columns is not None:
            wanted = set(columns)
            sv = {k: v for k, v in sv.items() if k in wanted or any(k.startswith(c) for c in wanted)}
        if not sv:
            return ""
        lines = ["IMPORTANT STRING VALUES (use exact text in filters):"]
        for col, values in sv.items():
            lines.append(f"  {col}:")
            lines += [f'    "{val}" = {meaning}' for val, meaning in values.items()]
        return "\n".join(lines)

    def key_facts(self) -> str:
        facts = self.reference.get("key_facts") or []
        return "KEY FACTS:\n  - " + "\n  - ".join(facts) if facts else ""

    def reference_blocks(self) -> str:
        rendered = []
        for block in self.reference.get("reference_blocks") or []:
            heading = (block.get("heading") or "").strip()
            lines = block.get("lines") or []
            if lines:
                body = "\n".join(f"  {line}" for line in lines)
                rendered.append(f"{heading}\n{body}" if heading else body)
        return "\n\n".join(rendered)

    def query_patterns(self) -> str:
        patterns = self.reference.get("query_patterns") or []
        return "\n\n".join(f"-- {p.get('name', 'pattern')}\n{p.get('dax', '')}" for p in patterns)

    def fix_rules(self) -> str:
        blocks = []
        for r in self.reference.get("fix_rules") or []:
            block = [f"{r.get('id', '?')}. {r.get('title', '')}"]
            if r.get("explanation"):
                block.append(r["explanation"])
            if "wrong" in r:
                block.append(f"WRONG:\n{r['wrong']}")
            if "correct" in r:
                block.append(f"CORRECT:\n{r['correct']}")
            block += [f'  "{bad}" -> {fix}' for bad, fix in (r.get("replacements") or {}).items()]
            blocks.append("\n".join(block))
        return "\n\n".join(blocks)
