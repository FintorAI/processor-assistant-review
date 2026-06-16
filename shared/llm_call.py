"""Shared LLM Call Utility — Make structured LLM calls within tools.

Some tools need LLM reasoning for tasks like:
- Comparing/analyzing unstructured text (e.g., document comments, trust names)
- Making judgment calls on fuzzy matches
- Summarizing findings or explaining mismatches
- Generating human-readable suggestions

This module provides a simple, reusable interface for making Claude calls
from within generated tool functions.

Usage:
    from shared.llm_call import llm_call, llm_json_call, llm_structured_call

    # Simple text response
    result = llm_call(
        prompt="Compare these two names: 'John A. Smith' vs 'Smith, John'",
        system="You are a mortgage document reviewer. Be concise.",
    )
    # result.text, result.success, result.usage

    # Structured response with a defined schema (RECOMMENDED)
    data = llm_structured_call(
        prompt="Compare borrower name in LOS vs document",
        schema={
            "match": {"type": "boolean", "description": "Whether the names match"},
            "confidence": {"type": "number", "description": "0.0 to 1.0"},
            "los_value": {"type": "string", "description": "Name from LOS"},
            "doc_value": {"type": "string", "description": "Name from document"},
            "explanation": {"type": "string", "description": "Why they match or don't"},
            "issues": {"type": "array", "items": {"type": "string"}, "description": "List of discrepancies found"},
        },
        system="You are a mortgage document reviewer comparing borrower names.",
        context={"los_name": "John A. Smith", "doc_name": "Smith, John"},
    )
    # data = {"match": True, "confidence": 0.95, "los_value": "John A. Smith", ...}
    # Guaranteed to have exactly those keys, or None on failure.

    # Quick JSON response (less strict, no schema enforcement)
    data = llm_json_call(
        prompt="Analyze these conditions and return a JSON list...",
        system="Return valid JSON only.",
    )
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────

# Default model — fast + capable for in-tool reasoning
DEFAULT_MODEL = "claude-sonnet-4-6"

# Smaller model for simple classification / yes-no decisions
FAST_MODEL = "claude-haiku-3-20250303"

# Token budget defaults
DEFAULT_MAX_TOKENS = 1024
FAST_MAX_TOKENS = 256


@dataclass
class LLMResult:
    """Result from an LLM call."""
    text: str = ""
    success: bool = True
    model: str = ""
    usage: dict = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def input_tokens(self) -> int:
        return self.usage.get("input_tokens", 0)

    @property
    def output_tokens(self) -> int:
        return self.usage.get("output_tokens", 0)


# ── Core Functions ────────────────────────────────────────────────────

def llm_call(
    prompt: str,
    system: str = "You are a helpful assistant for mortgage loan processing.",
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    context: Optional[dict] = None,
    temperature: float = 0.0,
) -> LLMResult:
    """Make a synchronous LLM call and return text.

    Args:
        prompt: The user prompt / question
        system: System prompt (controls persona and behavior)
        model: Claude model to use
        max_tokens: Max output tokens
        context: Optional dict of additional context to append to prompt
        temperature: Sampling temperature (0.0 = deterministic)

    Returns:
        LLMResult with text, usage, and error info
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.error("[LLM_CALL] ANTHROPIC_API_KEY not set")
        return LLMResult(success=False, error="ANTHROPIC_API_KEY not set")

    # Append context if provided
    full_prompt = prompt
    if context:
        context_str = "\n".join(f"- {k}: {v}" for k, v in context.items())
        full_prompt += f"\n\nAdditional context:\n{context_str}"

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": full_prompt}],
        )

        text = response.content[0].text
        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }

        logger.info(
            f"[LLM_CALL] model={model} "
            f"in={usage['input_tokens']} out={usage['output_tokens']} "
            f"len={len(text)}"
        )

        return LLMResult(text=text, success=True, model=model, usage=usage)

    except Exception as e:
        logger.error(f"[LLM_CALL] Error: {e}")
        return LLMResult(success=False, error=str(e))


def llm_json_call(
    prompt: str,
    system: str = "You are a helpful assistant. Return valid JSON only, no markdown fences.",
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    context: Optional[dict] = None,
    temperature: float = 0.0,
) -> dict | list | None:
    """Make an LLM call and parse the response as JSON.

    Args:
        prompt: The user prompt (should ask for JSON output)
        system: System prompt (should instruct JSON output)
        model: Claude model to use
        max_tokens: Max output tokens
        context: Optional dict of additional context
        temperature: Sampling temperature

    Returns:
        Parsed JSON (dict or list), or None on error
    """
    result = llm_call(
        prompt=prompt,
        system=system,
        model=model,
        max_tokens=max_tokens,
        context=context,
        temperature=temperature,
    )

    if not result.success:
        logger.error(f"[LLM_JSON_CALL] LLM call failed: {result.error}")
        return None

    text = result.text.strip()

    # Strip markdown fences if present
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"[LLM_JSON_CALL] Failed to parse JSON: {e}\nRaw text: {text[:200]}")
        return None


def llm_structured_call(
    prompt: str,
    schema: dict[str, Any],
    system: str = "You are a helpful assistant for mortgage loan processing.",
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    context: Optional[dict] = None,
    temperature: float = 0.0,
    tool_name: str = "structured_response",
    tool_description: str = "Return the structured response",
) -> dict | None:
    """Make an LLM call that returns a structured response matching a defined schema.

    Uses Claude's tool-use feature to FORCE the response into the exact shape
    you define. This is far more reliable than asking for JSON in the prompt.

    Args:
        prompt: The user prompt / question
        schema: Dict defining the response shape. Each key maps to a property
                definition with "type", "description", and optionally "enum",
                "items" (for arrays), "properties" (for nested objects).

                Types: "string", "number", "integer", "boolean", "array", "object"

                Examples:
                    Simple:
                        {"match": {"type": "boolean", "description": "Do they match?"}}

                    With enum:
                        {"severity": {"type": "string", "enum": ["low", "medium", "high"]}}

                    With array:
                        {"issues": {"type": "array", "items": {"type": "string"}, "description": "List of issues"}}

                    Nested object:
                        {"address": {"type": "object", "properties": {
                            "street": {"type": "string"},
                            "city": {"type": "string"},
                        }, "description": "Parsed address"}}

        system: System prompt
        model: Claude model to use
        max_tokens: Max output tokens
        context: Optional dict of additional context to append to prompt
        temperature: Sampling temperature (0.0 = deterministic)
        tool_name: Internal tool name (doesn't affect output)
        tool_description: Internal tool description

    Returns:
        Dict with exactly the keys defined in schema, or None on error.

    Example:
        >>> data = llm_structured_call(
        ...     prompt="Is this a VA loan? Loan type field says 'VA'",
        ...     schema={
        ...         "is_va": {"type": "boolean", "description": "Whether this is a VA loan"},
        ...         "loan_type": {"type": "string", "description": "Detected loan type"},
        ...         "confidence": {"type": "number", "description": "Confidence 0-1"},
        ...     },
        ... )
        >>> data
        {"is_va": True, "loan_type": "VA", "confidence": 1.0}
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.error("[LLM_STRUCTURED] ANTHROPIC_API_KEY not set")
        return None

    # Append context if provided
    full_prompt = prompt
    if context:
        context_str = "\n".join(f"- {k}: {v}" for k, v in context.items())
        full_prompt += f"\n\nAdditional context:\n{context_str}"

    # Build the tool definition from the schema
    # Each schema key becomes a property in the tool's input_schema
    properties = {}
    required = []
    for key, definition in schema.items():
        if isinstance(definition, str):
            # Shorthand: {"field": "description"} → {"field": {"type": "string", "description": "..."}}
            properties[key] = {"type": "string", "description": definition}
        elif isinstance(definition, dict):
            prop: dict[str, Any] = {"type": definition.get("type", "string")}
            if "description" in definition:
                prop["description"] = definition["description"]
            if "enum" in definition:
                prop["enum"] = definition["enum"]
            if "items" in definition:
                prop["items"] = definition["items"]
            if "properties" in definition:
                prop["properties"] = definition["properties"]
                prop["type"] = "object"
            if "default" not in definition:
                required.append(key)
            properties[key] = prop
        else:
            properties[key] = {"type": "string", "description": str(definition)}
            required.append(key)

    tool_def = {
        "name": tool_name,
        "description": tool_description,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": full_prompt}],
            tools=[tool_def],
            tool_choice={"type": "tool", "name": tool_name},
        )

        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }

        # Extract the tool call result
        for block in response.content:
            if block.type == "tool_use" and block.name == tool_name:
                data = block.input
                logger.info(
                    f"[LLM_STRUCTURED] model={model} "
                    f"in={usage['input_tokens']} out={usage['output_tokens']} "
                    f"keys={list(data.keys())}"
                )
                return data

        # Fallback: if no tool_use block, try parsing text as JSON
        for block in response.content:
            if hasattr(block, "text") and block.text:
                try:
                    data = json.loads(block.text)
                    if isinstance(data, dict):
                        logger.info(f"[LLM_STRUCTURED] Fallback JSON parse succeeded")
                        return data
                except json.JSONDecodeError:
                    pass

        logger.error("[LLM_STRUCTURED] No tool_use block in response")
        return None

    except Exception as e:
        logger.error(f"[LLM_STRUCTURED] Error: {e}")
        return None


def llm_classify(
    text: str,
    categories: list[str],
    system: str = "Classify the following text into exactly one of the given categories. Return ONLY the category name, nothing else.",
    model: str = FAST_MODEL,
) -> str | None:
    """Classify text into one of the given categories using a fast LLM call.

    Args:
        text: Text to classify
        categories: List of valid category names
        system: System prompt
        model: Model to use (defaults to fast/haiku)

    Returns:
        The matched category name, or None if classification failed
    """
    prompt = f"Categories: {', '.join(categories)}\n\nText to classify:\n{text}"

    result = llm_call(
        prompt=prompt,
        system=system,
        model=model,
        max_tokens=FAST_MAX_TOKENS,
        temperature=0.0,
    )

    if not result.success:
        return None

    answer = result.text.strip()

    # Exact match
    if answer in categories:
        return answer

    # Case-insensitive match
    answer_lower = answer.lower()
    for cat in categories:
        if cat.lower() == answer_lower:
            return cat

    # Partial match (category appears in answer)
    for cat in categories:
        if cat.lower() in answer_lower:
            return cat

    logger.warning(f"[LLM_CLASSIFY] No match for '{answer}' in {categories}")
    return None


def llm_compare(
    item_a: str,
    item_b: str,
    comparison_type: str = "name",
    system: str | None = None,
) -> dict:
    """Compare two items and return a structured match result.

    Useful for comparing borrower names, addresses, dates, etc.
    where fuzzy matching logic is needed.

    Args:
        item_a: First item (e.g., LOS value)
        item_b: Second item (e.g., document value)
        comparison_type: Type of comparison ("name", "address", "date", "amount", "general")

    Returns:
        dict with keys: match (bool), confidence (float 0-1), explanation (str)
    """
    type_hints = {
        "name": "Compare these person names. Account for middle names, suffixes (Jr, Sr, III), name order, nicknames, and maiden/married name differences.",
        "address": "Compare these addresses. Account for abbreviations (St/Street, Blvd/Boulevard), unit/apt formats, and USPS standardization differences.",
        "date": "Compare these dates. Account for different date formats (MM/DD/YYYY vs YYYY-MM-DD).",
        "amount": "Compare these monetary amounts. Account for formatting differences ($, commas, decimal places).",
        "general": "Compare these values and determine if they represent the same thing.",
    }

    default_system = (
        "You are a mortgage document reviewer comparing field values. "
        f"{type_hints.get(comparison_type, type_hints['general'])}"
    )

    result = llm_structured_call(
        prompt=f"Item A (from LOS): {item_a}\nItem B (from document): {item_b}",
        schema={
            "match": {"type": "boolean", "description": "Whether the items match"},
            "confidence": {"type": "number", "description": "Confidence score from 0.0 to 1.0"},
            "explanation": {"type": "string", "description": "Brief reason for the match/mismatch"},
        },
        system=system or default_system,
        model=FAST_MODEL,
        max_tokens=FAST_MAX_TOKENS,
    )

    if result and isinstance(result, dict):
        return {
            "match": result.get("match", False),
            "confidence": result.get("confidence", 0.0),
            "explanation": result.get("explanation", ""),
        }

    return {"match": False, "confidence": 0.0, "explanation": "LLM comparison failed"}
