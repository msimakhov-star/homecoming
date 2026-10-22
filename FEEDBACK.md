# Feedback on Nebius Token Factory and the NVIDIA models

Written while building Homecoming (22 to 29 October 2026). Specific, so it is useful. Items marked **[to confirm]** were observed before the live key was added and are re-checked on the live runs; the final submission states the measured numbers.

## Nebius Token Factory

**What we used it for.** Three NVIDIA models through the OpenAI-compatible chat endpoint: `nvidia/Nemotron-3.5-Super-VL` for reading photos with strict JSON schemas, `nvidia/Nemotron-3-Ultra-550b-a55b` for one reasoning call per run, `nvidia/Nemotron-3_5-Lightning` for many small guard calls.

**Zero to hello world.**
- The public catalogue endpoint (`/api/public/models_info`) is excellent: exact model ids, modalities, prices and `use_cases` in one JSON. We built our cost meter straight from it. Please link it from the docs; we found it by reading the catalogue page's network calls.
- Model ids are inconsistent in style: `Nemotron-3.5-Super-VL`, `Nemotron-3_5-Lightning` (underscore), `Nemotron-3-Ultra-550b-a55b` (lower case). Every one of these is a copy-paste trap. Aliases such as `nvidia/nemotron-3.5-lightning` that resolve to the canonical id would remove a whole class of first-run errors.
- Onboarding requires a payment card before an API key can be created, even with a promo code in hand. For a hackathon this is the single biggest drop-off point we saw in the Devpost forum (dozens of threads). A card-free sandbox key with a hard cap would fix it.

**What worked well.**
- OpenAI compatibility meant our client is 60 lines of `urllib`, no SDK.
- `response_format: json_schema` with `strict: true` on Super VL **[to confirm]**: strict output is what makes a photo-to-table step usable.
- The `usage` field on every response made a per-run cost meter trivial.

**What needs work.**
- **No spend cap or balance alert on the account.** We had to build rate limits and a daily USD budget into the app. A per-key monthly cap (like most API vendors offer) is the one feature we would ask for first.
- A request fails mid-flight with HTTP 402 when the balance runs out, with no warning **[reported by other entrants; to confirm]**. A 24-hour-notice email at a threshold would be enough.
- Promo credits are said to expire 90 days after issue; the documentation does not state this clearly anywhere we could find.
- Whether reasoning effort is controlled by `reasoning_effort` or `chat_template_kwargs` per model is not documented per model; we used `reasoning_effort` and guarded against it being ignored.

**Would we build on it again?** Yes, for inference: the catalogue, the prices and the OpenAI compatibility are right. We would want the spend cap before putting a card behind a public demo.

## NVIDIA models

**Nemotron 3.5 Super VL.** The core of the product. Strengths: strict JSON, reads printed labels and a typed discharge letter reliably **[measured accuracy here]**, and follows the "3x3 grid cell" instruction so items can be outlined on the photo without bounding boxes. Weaknesses: **[curved bottles / small print measured here]**. A documented bounding-box output format would let us outline items precisely instead of by grid cell.

**Nemotron 3 Ultra.** Used once per run, text only, with `reasoning_effort: medium`. It correctly spotted a drug listed as both "stopped" and "continue" in a letter and turned discrepancies into questions rather than advice when told to **[to confirm on live runs]**. Latency **[measured]**. At $1/$3 per million tokens it costs about a cent per kitchen.

**Nemotron 3.5 Lightning.** Used as a policy guard per sentence and for the ask box. Fast and cheap enough to call ten times per run. Our regex blocklist catches the obvious imperatives; Lightning catches the paraphrases **[rate measured]**.

**What we did not use and why.** No fine-tuning: no NVIDIA model is listed as trainable on Token Factory. No Serverless GPU: the app is CPU-only and the models are served. No NemoClaw / Hermes: not a personal-agent product.

## Tavily

Used as the fallback when RxNorm (a US database) does not know a UK brand name: a domain-restricted search of official leaflet sites and a read of the active-ingredient line. **[hit rate measured]**. The `include_domains` filter is exactly what a safety-sensitive app needs.

## One-line summary

The models and the catalogue are strong; the account tooling (card-first onboarding, no spend cap, undocumented credit expiry) is what stood between a weekend builder and a public demo.
