"""Map the names people type to the exact values stored in the dataset.

Ported from Corp-ChatWithData `services/agent_powerbi/service/agent/use_cases/entity_normalizer.py`:
same per-domain dictionaries (tenancy's `config` block), same Arabic normalisation, same
templates. Deterministic and model-free: resolve_entities returns the corrections, and the agent
must use those exact values in its DAX filters.
"""

from __future__ import annotations

import difflib
import logging
import re
import unicodedata
from dataclasses import asdict, dataclass

logger = logging.getLogger("gb_powerbi.entities")

_TASHKEEL = re.compile(r"[ؗ-ًؚ-ْٰ]")
_CHAR_MAP = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ئ": "ي", "ة": "ه", "ؤ": "و"})
DEFAULT_HEADING = "ENTITY NAME CORRECTIONS (MANDATORY — use these exact values in your DAX):"
_DEFAULT_MIN_MATCH = 3


def normalize(text: str) -> str:
    """Normalise for comparison (not display): NFC, no Arabic diacritics, unified letter variants,
    case-folded so Latin names match whatever case the employee typed."""
    text = unicodedata.normalize("NFC", text)
    text = _TASHKEEL.sub("", text)
    return text.translate(_CHAR_MAP).casefold()


@dataclass(slots=True)
class EntityMatch:
    user_text: str
    exact_name: str
    category: str
    code: int | str | None = None
    related_exact_name: str | None = None
    template: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("template")
        return {k: v for k, v in d.items() if v is not None}


class EntityNormalizer:
    def __init__(self, config: dict | None) -> None:
        config = config or {}
        self.heading: str = config.get("heading") or DEFAULT_HEADING
        self._min = int(config.get("min_match_length") or _DEFAULT_MIN_MATCH)
        self._entries: list[tuple[str, str, str, int | str | None]] = []
        self._templates: dict[str, str] = {}
        self._related: dict[tuple[str, str], str] = {}
        by_category: dict[str, dict[str, str]] = {}
        for name, spec in (config.get("categories") or {}).items():
            if not isinstance(spec, dict):
                continue
            self._templates[name] = spec.get("template") or ""
            cleaned: dict[str, str] = {}
            for value in spec.get("values") or []:
                exact, code = _split(value)
                if not exact:
                    continue
                norm = normalize(exact).strip()
                self._entries.append((norm, exact, name, code))
                cleaned[norm] = exact
            by_category[name] = cleaned
        for link in config.get("crosswalks") or []:
            if not isinstance(link, dict):
                continue
            source, target = by_category.get(link.get("from") or ""), by_category.get(link.get("to") or "")
            if not source or not target:
                continue
            cutoff = float(link.get("cutoff", 0.3))
            for src_norm, src_exact in source.items():
                hit = difflib.get_close_matches(src_norm, list(target), n=1, cutoff=cutoff)
                if hit:
                    dst = target[hit[0]]
                    self._related[(link["from"], src_exact)] = dst
                    self._related[(link["to"], dst)] = src_exact
        # Longest first, so a full name wins over a part of it.
        self._entries.sort(key=lambda e: len(e[0]), reverse=True)

    @property
    def configured(self) -> bool:
        return bool(self._entries)

    def find(self, text: str) -> list[EntityMatch]:
        norm_q = normalize(text)
        matches, seen = [], set()
        for norm_name, exact, category, code in self._entries:
            if len(norm_name) < self._min or exact in seen or norm_name not in norm_q:
                continue
            # A shorter name inside a longer one already matched is not a second entity.
            if any(norm_name in normalize(m.exact_name) for m in matches):
                continue
            matches.append(EntityMatch(user_text=_original(text, norm_name) or exact, exact_name=exact,
                                       category=category, code=code,
                                       related_exact_name=self._related.get((category, exact)),
                                       template=self._templates.get(category, "")))
            seen.add(exact)
        return matches

    def corrections(self, matches: list[EntityMatch]) -> str:
        if not matches:
            return ""
        lines = [self.heading]
        for m in matches:
            if m.template:
                try:
                    lines.append(m.template.format(user_text=m.user_text, exact_name=m.exact_name,
                                                   code=m.code if m.code is not None else "",
                                                   related_exact_name=m.related_exact_name or m.exact_name))
                    continue
                except (KeyError, IndexError, ValueError) as exc:
                    logger.warning("entity template for %r is invalid (%s); using the generic form",
                                   m.category, exc)
            suffix = f" (code {m.code})" if m.code is not None else ""
            lines.append(f'  - {m.category}: use exact spelling "{m.exact_name}"{suffix}')
        return "\n".join(lines)


def _original(text: str, norm_target: str) -> str | None:
    """The substring the person actually typed that normalises to *norm_target*."""
    norm_q = normalize(text)
    idx = norm_q.find(norm_target)
    if idx == -1:
        return None
    positions = [i for i, ch in enumerate(text) if normalize(ch)]
    if idx + len(norm_target) <= len(positions):
        return text[positions[idx]:positions[idx + len(norm_target) - 1] + 1]
    return None


def _split(value) -> tuple[str, int | str | None]:
    if isinstance(value, dict):
        return (value.get("name") or "").strip(), value.get("code")
    if isinstance(value, str):
        return value.strip(), None
    return "", None
