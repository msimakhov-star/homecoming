# Devpost submission text (paste into the form)

**Project name:** Homecoming

**Tagline:** The weekend after discharge: photograph the letter, the boxes and the pill organiser, and see where the house disagrees with the hospital. Questions for the pharmacist, never advice.

**Track:** Best Apps and Agents

**Built with (tags):** Nebius Token Factory, NVIDIA Nemotron 3.5 Super VL, NVIDIA Nemotron 3 Ultra, NVIDIA Nemotron 3.5 Lightning, Tavily, RxNorm, FastAPI, Modal, Python

**Demo URL:** https://m-simakhov--homecoming-web.us-east.modal.run
**Repo:** https://github.com/msimakhov-star/homecoming
**Video:** <VIDEO_URL>

## Inspiration

Dad comes home from hospital on a Friday with a letter that stops one medicine, changes another and adds a third. The kitchen still holds the old boxes and a filled pill organiser. The GP is closed until Monday. The carer does not need medical advice; they need to know exactly what to ask the pharmacist, and to be sure they have not missed anything. There was no tool that simply put the hospital's list, the cupboard and the organiser side by side.

## What it does

Three photos in, one page out. Homecoming reads the discharge letter, every box on the table and the organiser, normalises each printed name to its active ingredient, and compares the three lists. Every disagreement (a stopped medicine still in the house, a dose change with the old strength still in the cupboard, two brands of the same drug, a medicine the letter never mentions, a letter that contradicts itself) becomes a card outlined on the photo and a single question for the pharmacist. The carer can correct any misread line and re-check, print a one-page sheet, or share a link with family. It never tells anyone what to take.

## How we built it

- **Nemotron 3.5 Super VL on Nebius Token Factory** reads each photo into strict JSON (name, strength, action, grid cell for outlining).
- **RxNorm** (plus a **Tavily** leaflet search for UK brand names) maps printed names to active ingredients. A database, not a model, decides what a medicine is.
- **Plain Python with tests** compares the three lists: seven discrepancy types, deterministic and explainable.
- **Nemotron 3 Ultra** runs once per kitchen, text only: it audits the letter for contradictions and ambiguous wording and writes one pharmacist question per card.
- **Nemotron 3.5 Lightning** checks every sentence against a no-advice policy (after a regex blocklist) and powers the ask box, which can only answer by reframing as pharmacist questions.
- A per-run cost meter is built from Token Factory's `usage` field and catalogue prices. A kitchen costs a few cents.
- Deployed as one scale-to-zero container on Modal with per-IP and daily-budget limits in the app, because Token Factory has no spend cap.

## Challenges

Reading curved labels and small print; making "no advice" true by construction rather than by prompt; keeping a public demo alive for free until judging ends (rate limits, a daily budget, recorded fallback); and UK versus US drug naming (RxNorm is American).

## Accomplishments

A finished product, not a proof of concept: the carer can go from three photos to a printed pharmacist sheet in under a minute, correct the machine where it misread, and nothing it shows is advice. The model split (VL reads, code compares, Ultra reasons once, Lightning guards) keeps it cheap, fast and honest.

## What we learned

Where a model belongs and where it does not. The reconciliation is code with tests; the models do what only they can: read a messy photo and write a clear question.

## What's next

A pharmacist-side view, support for handwritten letters, and a trial with a discharge team.

## Feedback on Nebius and NVIDIA tools

See FEEDBACK.md in the repository (and the feedback field of this form).

## If the project existed before the submission period

It did not. All code was written during the submission period.
