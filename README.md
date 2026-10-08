# Hermano

**Hermano** (Spanish for *brother*) is an AI companion for children and teens with autism, ADHD and other support needs in inclusive public schools of Salta, northern Argentina. Each child picks a companion, *hermano* or *hermana* (sister), that walks them through the school day and keeps every adult around that child in sync: teachers, principals, families and the child's own specialists.

Built for the [Qloo Agentic Hackathon](https://qloo.devpost.com/).

## Why

In Argentina, a student with a disability in a mainstream school has an individual inclusion plan (PPI, Resolution CFE 311/16), written by the school team together with the family. A UNICEF Argentina study of 20 public schools (2020) found that 67% of school staff had clear guidelines for writing those plans, but only 53% for monitoring and evaluating them, and only 34% of students said they had taken part in any stage of their own plan.

Hermano works on that gap: the day-to-day follow-up, and the child's own voice.

## How it works

1. An adult enters what the child likes: a show, a game, a band, a sport.
2. **Qloo is the core.** Hermano looks each interest up with `/search` and asks `/v2/insights` for related books, artists, TV shows, video games and movies. A child who loves *Bluey* and *Minecraft* gets motivators from five domains, not one.
3. For every block of the school day, Hermano prepares a first/then cue, a warning before each change, calm-down options and a reward taken from those tastes. It rotates rewards so they do not wear out, and suggests a reading close to the child's tastes for the topic of the class.
4. At the end of the day the child says how each step went, with faces (pictograms next), on the family phone, a school tablet or their own phone.
5. Each adult gets a different report, only about the children linked to them: practical notes for the teacher, an aggregate for the principal, plain language for the family, a structured log for the specialist.

Without Qloo, every child would get the same generic routine.

## Privacy by design

- No cameras, no biometrics, and Hermano does not read the child's phone: the phone is the child's own window to Hermano.
- Hermano stores supports ("needs a warning before changing activity"), never diagnoses. Health words are scrubbed from every report.
- Only interest names and Qloo entity ids are sent to Qloo, never a child's name, id or supports (tested).
- Every adult sees only their own children (tested).
- The public demo uses invented students. `data/mock_taste.json` was invented by the team for offline work: it is **not** Qloo data, and real Qloo responses are never stored in this repository.

## Status

Day-one skeleton, 8 October 2026: data model, Qloo client with an offline mock, day planner, rotation, check-ins and the four reports, with 17 tests. Next: the web views (child view for the phone, adult views), the LLM writer, adults approving new motivators before the child sees them, an English toggle for judges, and the hosted demo.

## Run it

Python 3.9 or newer, no dependencies.

```bash
python -m unittest -v          # tests
python -m hermano demo         # one day: plans and the report for every adult
python -m hermano privacy      # everything that was sent to Qloo
```

With a hackathon key, `export QLOO_API_KEY=...` and the same commands call `https://hackathon.api.qloo.com`. Never commit the key.

## Credits

Idea and product decisions: Jorge Abraham Mendieta (Aguaray, Salta, Argentina). Code written with AI assistance.

## License

MIT, see [LICENSE](LICENSE).
