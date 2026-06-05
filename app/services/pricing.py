# USD per 1 million tokens
_PRICES: dict[str, dict[str, float]] = {
    # Gemini
    "gemini-2.0-flash":         {"input": 0.10,  "output": 0.40},
    "gemini-1.5-pro":           {"input": 3.50,  "output": 10.50},
    "gemini-1.5-flash":         {"input": 0.075, "output": 0.30},
    # Groq-hosted models
    "llama3-8b-8192":           {"input": 0.05,  "output": 0.08},
    "llama3-70b-8192":          {"input": 0.59,  "output": 0.79},
    "llama-3.1-8b-instant":     {"input": 0.05,  "output": 0.08},
    "llama-3.3-70b-versatile":  {"input": 0.59,  "output": 0.79},
    "mixtral-8x7b-32768":       {"input": 0.24,  "output": 0.24},
    "gemma2-9b-it":             {"input": 0.20,  "output": 0.20},
}

_FALLBACK = {"input": 1.00, "output": 1.00}


def calculate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    price = _PRICES.get(model, _FALLBACK)
    return (prompt_tokens * price["input"] + completion_tokens * price["output"]) / 1_000_000
