"""Shared LLM client.

A thin wrapper over Ollama so every agent talks to the model the same
way, and so switching providers later is a one-file change rather than
an edit in six places.

Two entry points:
  complete()      — plain text in, text out
  complete_json() — same, but validates and parses the response as JSON,
                    retrying once with a corrective prompt if the model
                    wraps its output in prose.

The JSON retry exists because small local models occasionally ignore
"return only JSON" and add a preamble. Rather than crashing the agent,
we strip common wrappers, and if that fails, ask again more firmly.
"""

import os
import re
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

import ollama


_client = None


def get_client():
    global _client
    if _client is None:
        _client = ollama.Client(host=settings.ollama_host)
    return _client


def complete(prompt, system=None, temperature=0.1, max_tokens=1024):
    """Send a prompt, get text back.

    Low temperature by default — these agents extract and reason over
    facts, where consistency matters more than creativity.
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = get_client().chat(
        model=settings.ollama_model,
        messages=messages,
        options={
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    )
    return response["message"]["content"].strip()


def extract_json(text):
    """Pull a JSON object out of a model response.

    Handles the common failure modes: markdown code fences, a prose
    preamble before the JSON, or trailing commentary after it.
    Returns None if nothing parseable is found.
    """
    text = text.strip()

    # Try the whole thing first — the happy path
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strip markdown fences
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Grab the outermost {...} or [...] block
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue

    return None


def complete_json(prompt, system=None, temperature=0.1, max_tokens=1024):
    """Like complete(), but returns parsed JSON.

    Retries once with a corrective prompt if the first response
    isn't parseable. Returns None if both attempts fail.
    """
    base_system = system or ""
    json_system = (
        base_system
        + "\n\nRespond with valid JSON only. No preamble, no explanation, "
        + "no markdown fences. Start your response with { or [."
    ).strip()

    raw = complete(
        prompt,
        system=json_system,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    parsed = extract_json(raw)
    if parsed is not None:
        return parsed

    # One corrective retry — show the model its own bad output
    retry_prompt = (
        "Your previous response was not valid JSON:\n\n"
        + raw[:500]
        + "\n\nRespond to the original request with ONLY valid JSON.\n\n"
        + "Original request:\n"
        + prompt
    )
    raw2 = complete(
        retry_prompt,
        system=json_system,
        temperature=0.0,
        max_tokens=max_tokens,
    )
    return extract_json(raw2)


def health_check():
    """Confirm the model is reachable and returns sane output."""
    try:
        result = complete_json(
            'Return this exact JSON: {"status": "ok", "n": 42}',
            max_tokens=64,
        )
        if result and result.get("status") == "ok":
            return True, settings.ollama_model + " responding correctly"
        return False, "Unexpected response: " + str(result)
    except Exception as e:
        return False, type(e).__name__ + ": " + str(e)


if __name__ == "__main__":
    print("Provider: " + str(settings.llm_provider))
    print("Model:    " + str(settings.ollama_model))
    print("Host:     " + str(settings.ollama_host))
    print()
    ok, message = health_check()
    print(("OK   " if ok else "FAIL ") + message)