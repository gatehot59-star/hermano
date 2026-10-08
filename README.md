# Hermano

**Hermano** (Spanish for *brother*) is a school-day companion for children and teens with support needs, such as autism or ADHD, in inclusive public schools of Salta, northern Argentina. Each child picks a companion, *hermano* or *hermana* (sister), that walks them through the school day, and every adult around that child gets the report they need: teachers, principals, families and the child's own specialists.

Built for the [Qloo Agentic Hackathon](https://qloo.devpost.com/).

## Why

In Argentina, a student with a disability in a mainstream school has an individual inclusion plan (PPI, Resolution CFE 311/16), written by the school team together with the family. In a UNICEF Argentina study of 20 schools in 5 provinces (2020), 67% of the education actors consulted said they had clear guidelines for writing those plans, but only 53% for monitoring and evaluating them, and only 34% of the students said they had been invited to any stage of their own plan.

Hermano works on that gap: the day-to-day follow-up, and the child's own voice.

## How it works

1. An adult enters what the child likes: a show, a game, a band, a sports star.
2. **Qloo is the core.** Hermano looks each interest up with `/search` and asks `/v2/insights` for related books, artists, TV shows, video games and movies. Movies and shows are requested with `filter.content_rating` for the child's age, so Qloo itself keeps them age-appropriate.
3. **Nothing new reaches the child without an adult's OK.** Qloo's ideas go first to the child's family or teacher. The ones they approve become the child's motivators; the ones they reject are left out of the next Qloo request with `filter.exclude.entities`. Games the team knows are rated above the child's age (ESRB) are never even proposed.
4. **The agent plans the day.** A language model (Qwen3.8-27B on Groq) reads the day and the ideas through tools, chooses which adult-approved idea follows each class, and suggests up to two new Qloo ideas for the adults, each with one sentence on why it fits. It only ever sees the placeholder STUDENT_1, the child's age, the blocks and the titles: no name, no supports. Hermano's rules check every choice (no reward after recess, nothing unapproved for the child) and tell the model what to fix. Without the model, the rule-based planner does the same job.
5. For every block of the school day, Hermano prepares a first/then cue, a warning before each change and calm-down options. Rewards come only after classes, never after recess or at dismissal, and they rotate so they do not wear out.
6. At the end of the day the child says how each step went, with faces.
7. Each adult gets a different report, only about the children linked to them: practical notes and new ideas to approve for the teacher, an aggregate for the principal, plain language for the family, a structured log for the specialist.

Without Qloo, every child would get the same generic routine: that is exactly what Hermano falls back to when Qloo does not answer.

## Privacy and safety by design

- No cameras, no biometrics, and Hermano does not read the child's phone: the phone is the child's own window to Hermano.
- Supports come from a closed catalog ("a warning before every change of activity"). Hermano stores no diagnosis and no free-text notes, and health words are scrubbed from every report anyway (tested against the 31 terms of an earlier audit).
- Only interest names go to Qloo. An interest that contains a child's name, a health word or contact data is refused before any request, and the Qloo client refuses health or contact text on its own (tested).
- Every adult sees only their own children, and only the child's family or teacher can approve a motivator (tested).
- Qloo answers can be cached privately on the server (`cache/`, git-ignored); they are never committed. `hermano/data/mock_taste.json` was invented by the team for offline work: it is **not** Qloo data.
- The public demo uses invented students.

## Status

8 October 2026, first batch after the first audit: the Qloo client follows the hackathon rules (hackathon base URL only, the `/search` type names, 404 read as "no results", retries on 429 and timeouts, private cache), plus the age gate, adult approval, the privacy guard, English and Spanish, and `pip install`.

Second batch, same day: `hermano/llm.py`, the language model client. Model: Qwen3.8-27B on Groq (`qwen/qwen3.8-27b`, a Groq Preview model), falling back to `openai/gpt-oss-20b` and then to the rule-based planner. Every message is checked before it leaves: health words, contact data or a real name stop the request, so the model only sees placeholders such as STUDENT_1. It includes a small tool loop with a step limit. First calls to the real Groq API, same afternoon, with invented data only: both models read the day and queued one idea for an adult to approve, after a class block and with the placeholder only (Qwen 3 of 3 runs; gpt-oss-20b 2 of 3, the third misnamed a tool, which Groq rejects with `tool_use_failed` and Hermano now retries). Groq's edge answers 403 (error 1010) to Python's default user agent, so the Groq and Qloo clients send `hermano/<version>`. One plan with two tool calls used about 2,000 tokens; the free plan allows 8,000 per minute.

Third batch, same day: the agent planner, `hermano/planner.py`. The model gets three tools (`get_day`, `get_motivators`, `submit_plan`) and the code accepts only rewards after a class or special block and only ideas an adult approved; its suggestions, with their reasons, go to the teacher's and the family's reports, never to the child. If the model is down or writes a health word or contact data, the rule-based plan stands. In the first full demo against the real API (invented school, three children) Qwen planned the day of all three children in 28 seconds, every reward after a class and every suggestion inside the age filter. 90 tests.

Not done yet, in this order: the web views (the child's view for the phone, the adults' views), the hosted demo, and a test against the real Qloo API, which waits for the hackathon key.

Known limits: titles without a US content rating (for example, local Argentine shows) are left out by the rating filter. The ESRB ratings for games are typed by the team. An interest that matches a child's name is refused even when it is also a real title. Suggested readings do not match the class topic yet.

## Run it

Python 3.9 or newer, no dependencies.

```bash
python -m unittest -v              # tests
python -m hermano demo             # one day: plans, the child view and the report for every adult
python -m hermano demo --lang es   # the same in Spanish
python -m hermano demo --days 3    # three days; each family approves one new idea per day (simulated)
python -m hermano privacy          # everything that was sent to Qloo
python -m hermano demo --planner rules   # never call the language model, even with a key
```

Or install it with `pip install .` and run `hermano demo`.

With a hackathon key, `export QLOO_API_KEY=...` and the same commands call `https://hackathon.api.qloo.com`. Never commit the key.

With a Groq key, `export GROQ_API_KEY=...` turns the language model on; `HERMANO_MODELS` (comma separated) changes the model order. Without it, Hermano uses its rule-based planner.

## Credits

Idea and product decisions: Jorge Abraham Mendieta (Aguaray, Salta, Argentina). Code written with AI assistance.

## License

MIT, see [LICENSE](LICENSE).
