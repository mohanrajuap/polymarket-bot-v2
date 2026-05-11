"""Groq AI client - the mastermind brain"""

import json
import logging
import requests
import config
from constants import GROQ_API_URL, GROQ_MODEL, GROQ_MAX_TOKENS, GROQ_TEMPERATURE

logger = logging.getLogger(__name__)

class GroqClient:
    def __init__(self):
        self.api_key = config.GROQ_API_KEY
        self.model = GROQ_MODEL
        self.call_count = 0
        self.error_count = 0

    def complete(self, prompt: str, system: str = None, max_tokens: int = None) -> str:
        """Make a completion request to Groq"""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        body = {
            "model": self.model,
            "messages": messages,
            "temperature": GROQ_TEMPERATURE,
            "max_tokens": max_tokens or GROQ_MAX_TOKENS,
        }

        try:
            r = requests.post(GROQ_API_URL, headers=headers, json=body, timeout=15)
            r.raise_for_status()
            self.call_count += 1
            return r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            self.error_count += 1
            logger.error(f"Groq API error: {e}")
            return None

    def analyze_market(self, market: dict, signals: dict, patterns: list) -> dict:
        """
        Core market analysis - returns trade decision
        """
        # Build pattern summary for context
        pattern_text = ""
        if patterns:
            best = patterns[:3]
            pattern_text = "\n".join([
                f"- {p['condition_desc']}: {p['win_rate']*100:.0f}% WR ({p['sample_size']} trades)"
                for p in best
            ])

        prompt = f"""Analyze this prediction market and decide whether to trade.

MARKET:
Question: {market.get('question')}
Current YES price: {market.get('price_yes', 0.5):.3f}
Current NO price: {market.get('price_no', 0.5):.3f}
Liquidity: ${market.get('liquidity', 0):,.0f}
Hours until resolution: {market.get('hours_left', 0):.1f}

BTC SIGNALS:
Current price: ${signals.get('btc_price', 0):,.2f}
1min momentum: {signals.get('momentum_1m', 0)*100:.3f}%
5min momentum: {signals.get('momentum_5m', 0)*100:.3f}%
15min momentum: {signals.get('momentum_15m', 0)*100:.3f}%
Volume ratio: {signals.get('volume_ratio', 1):.2f}x normal
RSI: {signals.get('rsi', 50):.1f}
Trend: {signals.get('trend', 'neutral')}

SENTIMENT:
Fear/Greed Index: {signals.get('fear_greed', 50)} ({signals.get('fear_greed_class', 'Neutral')})

HISTORICAL PATTERNS (what worked before):
{pattern_text if pattern_text else "No patterns yet - early stage"}

RULES:
- Only respond with valid JSON
- edge = abs(your_estimate - market_price)
- If edge < 0.05 → action = "skip"
- If confidence < 0.4 → action = "skip"
- Never recommend NO bets (data shows NO bets lose long-term)
- Be realistic and data-driven

Respond ONLY with this JSON:
{{
  "estimated_probability": 0.XX,
  "confidence": 0.XX,
  "edge": 0.XX,
  "action": "buy" or "skip",
  "side": "yes",
  "reasoning": "brief explanation under 100 words"
}}"""

        response = self.complete(prompt)
        if not response:
            return self._default_skip("Groq API unavailable")

        return self._parse_response(response, market)

    def _parse_response(self, response: str, market: dict) -> dict:
        """Parse and validate Groq response"""
        try:
            # Clean response
            text = response.strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            text = text.strip()

            data = json.loads(text)

            # Validate fields
            prob = float(data.get("estimated_probability", 0.5))
            conf = float(data.get("confidence", 0))
            edge = float(data.get("edge", 0))
            action = data.get("action", "skip")
            side = data.get("side", "yes")
            reasoning = data.get("reasoning", "")

            # Apply guards
            market_price = market.get("price_yes", 0.5)

            # Never bet on extreme prices
            if market_price > 0.72 or market_price < 0.28:
                return self._default_skip(f"Price guard: {market_price:.2f}")

            return {
                "estimated_probability": prob,
                "confidence": conf,
                "edge": edge,
                "action": action,
                "side": "yes",  # Always YES (NO bets lose)
                "reasoning": reasoning,
                "market_price": market_price,
            }

        except Exception as e:
            logger.error(f"Failed to parse Groq response: {e}\nResponse: {response[:200]}")
            return self._default_skip(f"Parse error: {e}")

    def _default_skip(self, reason: str) -> dict:
        return {
            "estimated_probability": 0.5,
            "confidence": 0.0,
            "edge": 0.0,
            "action": "skip",
            "side": "yes",
            "reasoning": reason,
        }

    def get_status(self) -> dict:
        return {
            "model": self.model,
            "calls": self.call_count,
            "errors": self.error_count,
            "key_set": bool(self.api_key),
        }


groq_client = GroqClient()
