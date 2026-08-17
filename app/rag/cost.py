"""What a question cost to answer.

One question is not one model call. The agent decides what to look up, runs
tools, decides again, composes, then verifies — three to eight calls, and the
deciding rounds each replay every tool result gathered so far, so input tokens
grow faster than the conversation looks like it should. That is the number
worth putting on the screen, and none of it is visible from the answer alone.

Token counts come from the provider, not from re-tokenising the prompt here.
`usage_metadata` on the returned message is what the bill is computed from and
it accounts for things a local count cannot see — cached input billed at a
discount, reasoning tokens that were never in the prompt. `count_tokens` below
is kept for estimating a request before it is sent, which is a different job.

# PRODUCTION NOTE: costs land on the message row (see history.add_answer), so
# per-patient and per-day totals are a query away. Budget alerts, a spend
# dashboard and reconciliation against OpenAI's usage API are the next steps.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import tiktoken
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

logger = logging.getLogger(__name__)

# USD per 1,000 tokens. Add a model here when you start calling it — an unpriced
# model reports its tokens and `None` for cost rather than being charged at some
# other model's rate, because a cost figure that is quietly wrong is worse than
# one that admits it does not know.
PRICING: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"input": 0.00015, "output": 0.00060},
    "gpt-4o": {"input": 0.005, "output": 0.015},
    "gpt-4-turbo": {"input": 0.010, "output": 0.030},
    "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
}

# The model this app actually runs is set in .env and its price is not in the
# table above. Rather than hard-code a number that will be wrong by the time
# anyone reads it, take it from the environment:
#
#   RAG_PRICE_MODEL=gpt-5.4-mini
#   RAG_PRICE_INPUT_PER_1K=0.00025
#   RAG_PRICE_OUTPUT_PER_1K=0.00200
#
# Nothing breaks if they are unset, or if they are set to something that is not
# a number. Token counts still come through; only the dollar figure is withheld.
# This module is imported on the path to serving every question, so a typo in a
# price must degrade to 'cost unknown' and never take the app down with it —
# metering is the least important thing here and must fail the most gently.


def _env_price() -> None:
    model = os.getenv("RAG_PRICE_MODEL")
    prices = {
        "input": os.getenv("RAG_PRICE_INPUT_PER_1K"),
        "output": os.getenv("RAG_PRICE_OUTPUT_PER_1K"),
    }
    if not model or not all(prices.values()):
        return
    try:
        PRICING[model] = {side: float(value) for side, value in prices.items()}
    except ValueError:
        logger.warning(
            "ignoring the price set for %r: RAG_PRICE_INPUT_PER_1K=%r and "
            "RAG_PRICE_OUTPUT_PER_1K=%r are not numbers. Costs will read as unknown.",
            model,
            prices["input"],
            prices["output"],
        )


_env_price()

# Models we have already complained about. A missing price is a one-line
# warning at startup, not a line per model call.
_warned: set[str] = set()


def normalise(model: str) -> str:
    """`openai:gpt-4o-mini` and `gpt-4o-mini-2024-07-18` are both `gpt-4o-mini`.

    Two shapes arrive here. Config carries the provider prefix `init_chat_model`
    wants; the provider's own response carries a dated snapshot suffix. Neither
    matches the pricing table as written, and a lookup that misses is a cost of
    zero — the failure is silent, which is why this exists.
    """
    name = model.split(":", 1)[-1].strip()
    if name in PRICING:
        return name
    # Longest matching prefix, so `gpt-4o-mini-2024-07-18` finds `gpt-4o-mini`
    # rather than `gpt-4o`.
    matches = [known for known in PRICING if name.startswith(known)]
    return max(matches, key=len) if matches else name


@dataclass
class CostInfo:
    """One model call, priced.

    `total_cost_usd` is None when the model has no price on file. Callers must
    treat that as 'unknown', never as free — see the note on PRICING.
    """

    model: str
    input_tokens: int
    output_tokens: int
    input_cost_usd: float | None
    output_cost_usd: float | None
    total_cost_usd: float | None

    # Which part of the graph spent it: 'agent' for a lookup decision, 'compose'
    # for writing the answer, 'verify' for scoring it. The breakdown is the
    # useful half — a question that costs too much is nearly always too many
    # gathering rounds, and a single total cannot show that.
    node: str | None = None


def calculate_cost(model: str, input_tokens: int, output_tokens: int) -> CostInfo:
    """Price one call. Unknown models come back with tokens and no cost."""
    name = normalise(model)
    pricing = PRICING.get(name)

    if pricing is None:
        if name not in _warned:
            _warned.add(name)
            logger.warning(
                "no price on file for %r — token counts will be reported, cost will not. "
                "Set RAG_PRICE_MODEL/RAG_PRICE_INPUT_PER_1K/RAG_PRICE_OUTPUT_PER_1K.",
                name,
            )
        return CostInfo(name, input_tokens, output_tokens, None, None, None)

    input_cost = (input_tokens / 1000) * pricing["input"]
    output_cost = (output_tokens / 1000) * pricing["output"]
    return CostInfo(
        model=name,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        input_cost_usd=round(input_cost, 6),
        output_cost_usd=round(output_cost, 6),
        total_cost_usd=round(input_cost + output_cost, 6),
    )


@dataclass
class Usage:
    """Everything one question spent, across every call it took.

    Deliberately per-question and not a module-level total. A process-wide
    counter in a server answering several people at once accumulates all of
    them into one number, which is neither patient's cost and cannot be
    unmixed afterwards. One of these is created per request and travels with
    the answer.
    """

    calls: list[CostInfo] = field(default_factory=list)

    def record(self, info: CostInfo) -> None:
        self.calls.append(info)

    @property
    def input_tokens(self) -> int:
        return sum(call.input_tokens for call in self.calls)

    @property
    def output_tokens(self) -> int:
        return sum(call.output_tokens for call in self.calls)

    @property
    def cost_usd(self) -> float | None:
        """The bill, or None if any call in it was unpriced.

        None rather than the sum of the priced ones. A partial total presented
        as a total is the same silent-wrongness this module exists to avoid.
        """
        if not self.calls or any(call.total_cost_usd is None for call in self.calls):
            return None
        return round(sum(call.total_cost_usd for call in self.calls), 6)

    @property
    def model(self) -> str | None:
        """The model that did the work, when it was the same one throughout."""
        names = {call.model for call in self.calls}
        return names.pop() if len(names) == 1 else None

    def by_node(self) -> dict[str, dict[str, Any]]:
        """Spend per graph node, for working out where a slow answer went."""
        breakdown: dict[str, dict[str, Any]] = {}
        for call in self.calls:
            entry = breakdown.setdefault(
                call.node or "unknown",
                {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
            )
            entry["calls"] += 1
            entry["input_tokens"] += call.input_tokens
            entry["output_tokens"] += call.output_tokens
            if entry["cost_usd"] is not None and call.total_cost_usd is not None:
                entry["cost_usd"] = round(entry["cost_usd"] + call.total_cost_usd, 6)
            else:
                entry["cost_usd"] = None
        return breakdown

    def as_dict(self) -> dict[str, Any]:
        """The shape the API sends and the transcript stores."""
        return {
            "model": self.model,
            "calls": len(self.calls),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "by_node": self.by_node(),
        }


class CostCallback(BaseCallbackHandler):
    """Counts every model call the graph makes, wherever it makes it.

    A callback rather than a wrapper around each `ainvoke`. There are three call
    sites today and the interesting one — the gathering round — runs a variable
    number of times; wrapping each means three edits now and a forgotten fourth
    the next time a node is added. Attached once to the graph's config, this
    sees them all, including the verifier's structured-output call.

    Nothing here may raise. A cost meter that can fail a request is a worse
    trade than a cost meter that occasionally misses a call, so every hook is
    swallowed and logged.
    """

    def __init__(self, usage: Usage, default_model: str) -> None:
        self.usage = usage
        self.default_model = default_model
        # run_id -> node name. The graph tells us which node it is when the call
        # starts; the tokens only arrive when it ends.
        self._nodes: dict[UUID, str] = {}

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs) -> None:
        node = (kwargs.get("metadata") or {}).get("langgraph_node")
        if node:
            self._nodes[run_id] = node

    def on_llm_end(self, response: LLMResult, *, run_id=None, **kwargs) -> None:
        node = self._nodes.pop(run_id, None)
        try:
            counts = _usage_of(response)
            if counts is None:
                return
            input_tokens, output_tokens, model = counts
            info = calculate_cost(model or self.default_model, input_tokens, output_tokens)
            info.node = node
            self.usage.record(info)
        except Exception:  # noqa: BLE001 - metering must never fail the answer
            logger.exception("could not record the cost of a model call")

    def on_llm_error(self, error, *, run_id=None, **kwargs) -> None:
        self._nodes.pop(run_id, None)


def _usage_of(response: LLMResult) -> tuple[int, int, str | None] | None:
    """Input tokens, output tokens and model name, from wherever they landed.

    Two places, because streaming and non-streaming report differently.
    `usage_metadata` on the message is the modern path and the only one that
    survives a streamed response — and only when the model was built with
    `stream_usage=True`, which is why answering_model is. `llm_output` is the
    older per-provider dict, kept as a fallback.
    """
    model: str | None = (response.llm_output or {}).get("model_name")

    for generations in response.generations:
        for generation in generations:
            message = getattr(generation, "message", None)
            usage = getattr(message, "usage_metadata", None)
            if usage:
                model = model or (getattr(message, "response_metadata", {}) or {}).get("model_name")
                return int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)), model

    tokens = (response.llm_output or {}).get("token_usage") or {}
    if tokens:
        return int(tokens.get("prompt_tokens", 0)), int(tokens.get("completion_tokens", 0)), model

    # A streamed call on a model built without stream_usage reaches here. Worth
    # knowing about: it is the compose call, the most expensive one.
    logger.debug("model call reported no token usage")
    return None


def count_tokens(text: str, model: str = "gpt-4o-mini") -> int:
    """Count tokens in a string locally.

    For sizing something before it is sent — a context budget, a guard on an
    oversized document. Not what the bill is computed from; see the module note.
    """
    try:
        encoding = tiktoken.encoding_for_model(normalise(model))
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")
    return len(encoding.encode(text))
