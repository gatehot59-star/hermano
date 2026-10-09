# Hermano

**Hermano** (Spanish for *brother*) is a school-day companion for children and teens with support needs, such as autism or ADHD, in inclusive public schools of Salta, northern Argentina. Each child picks a companion, *hermano* or *hermana* (sister), that walks them through the school day, and every adult around that child gets the report they need: teachers, principals, families and the child's own specialists.

Built for the [Qloo Agentic Hackathon](https://qloo.devpost.com/).

## Why

In Argentina, a student with a disability in a mainstream school has an individual inclusion plan (PPI, Resolution CFE 311/16), written by the school team together with the family. In a UNICEF Argentina study of 20 schools in 5 provinces (2020), 67% of the education actors consulted said they had clear guidelines for writing those plans, but only 53% for monitoring and evaluating them, and only 34% of the students said they had been invited to any stage of their own plan.

Hermano works on that gap: the day-to-day follow-up, and the child's own voice.

## How it works

1. An adult enters what the child likes: a show, a game, a band, a sports star.
2. **Qloo is the core.** Hermano looks each interest up with `/search` and asks `/v2/insights` for related books, artists, TV shows, video games and movies. Movies and shows are requested with `filter.content_rating` for the child's age, so Qloo itself keeps them age-appropriate.
3. **Nothing new reaches the child without an adult's OK.** Qloo's ideas go first to the child's family or teacher. The ones they approve become the child's motivators; the ones they reject are left out of the next Qloo request with `filter.exclude.entities`. The child's own favorites, which an adult entered, are rewards from day one.
   **The age gate runs before any adult sees an idea**, and on the child's own favorites too: movies and shows must carry a rating for the child's age (checked again in code on Qloo's answer); games use the stricter of Qloo's ESRB rating and the team's table, and under 13 a game with no rating is not proposed; under 13, a new artist or book needs a Qloo tag for children or families (such as Childrens, Middle Grade or Family-Oriented).
4. **The agent plans the day.** A language model (Qwen3.8-27B on Groq) reads the day and the ideas through tools, chooses which adult-approved idea follows each class, and suggests up to two new Qloo ideas for the adults, each with one sentence on why it fits. It only ever sees the placeholder STUDENT_1, the child's age, the blocks and the titles: no name, no supports. Hermano's rules check every choice (no reward after recess, nothing unapproved for the child) and tell the model what to fix. Without the model, the rule-based planner does the same job.
5. For every block of the school day, Hermano prepares a first/then cue, a warning before each change and calm-down options. Rewards come only after classes, never after recess or at dismissal, a Qloo idea at most once a day, and the child's own favorites come back once the other approved ideas were used (with nothing left, the child chooses a game with the teacher); across days they rotate so they do not wear out.
   For a child whose adults agreed a movement break, every class gets one made from the child's own approved tastes: dance to a song by a favorite artist, move like a character from a favorite show, movie or game, or a simple classroom move (stretch, march in place, take something to the teacher's desk). The teacher sees which break goes in which class; the specialist sees how many were offered.
   For every class, the teacher also gets one reinforcement idea built on the class topic and one of the child's approved tastes, a different one each class and not that class's reward when there is another: explain the solar system the way a character from a favorite show would, turn addition into a level of a favorite game, make up a verse about it to the beat of a favorite artist. With no taste left, the child draws the topic in three steps. The idea follows the agreed supports ("give it one step at a time", "split it into two short parts"), and it goes only to the child's teacher, never to the child's own view.
   The child's specialist can pick exercises for the child to practice at school, from a closed catalog of everyday moves and phrases (square breathing, pushing the wall, asking for help with one sentence): never a treatment and never a diagnosis. Each one comes once a day, at the first moment of the day where it fits, and the child taps "I did it" or "Not today". The specialist reads those answers, the teacher gets the reminders and the family gets them to practice at home; the principal does not see them, and they never go to Qloo or to the language model.
   Every step of the child's day also has an "I feel nervous" button. It opens three calm steps, one at a time: a calming exercise the child's specialist agreed (or breathing and counting), the child's own approved music with headphones (or an agreed support that fits the moment, such as the quiet corner or headphones at a noisy recess, or the stress ball), and that Hermano told the teacher. The teacher and the specialist see during which steps it happened, the family reads it in plain words, and the principal only gets a count.
6. At the end of the day the child says how each step went, with faces.
7. Each adult gets a different report, only about the children linked to them: practical notes and new ideas to approve for the teacher, an aggregate for the principal, plain language for the family, a structured log for the specialist.

Without Qloo, each child would only get the two or three favorites an adult typed, over and over: that is what Hermano falls back to when Qloo does not answer.

## Privacy and safety by design

- No cameras, no biometrics, and Hermano does not read the child's phone: the phone is the child's own window to Hermano.
- Supports and the specialist's exercises come from closed catalogs ("a warning before every change of activity", "push the wall with both hands"). Hermano stores no diagnosis and no free-text notes, and health words are scrubbed from every report anyway (tested against the 31 terms of an earlier audit).
- Only interest names go to Qloo. An interest that contains a child's name, a health word or contact data is refused before any request, and the Qloo client refuses health or contact text on its own (tested).
- Every adult sees only their own children, and only the child's family or teacher can approve a motivator (tested).
- Qloo answers can be cached privately on the server (`cache/`, git-ignored); they are never committed. `hermano/data/mock_taste.json` was invented by the team for offline work: it is **not** Qloo data.
- The public demo uses invented students.

## Status

8 October 2026, first batch after the first audit: the Qloo client follows the hackathon rules (hackathon base URL only, the `/search` type names, 404 read as "no results", retries on 429 and timeouts, private cache), plus the age gate, adult approval, the privacy guard, English and Spanish, and `pip install`.

Second batch, same day: `hermano/llm.py`, the language model client. Model: Qwen3.8-27B on Groq (`qwen/qwen3.8-27b`, a Groq Preview model), falling back to `openai/gpt-oss-20b` and then to the rule-based planner. Every message is checked before it leaves: health words, contact data or a real name stop the request, so the model only sees placeholders such as STUDENT_1. It includes a small tool loop with a step limit. First calls to the real Groq API, same afternoon, with invented data only: both models read the day and queued one idea for an adult to approve, after a class block and with the placeholder only (Qwen 3 of 3 runs; gpt-oss-20b 2 of 3, the third misnamed a tool, which Groq rejects with `tool_use_failed` and Hermano now retries). Groq's edge answers 403 (error 1010) to Python's default user agent, so the Groq and Qloo clients send `hermano/<version>`. One plan with two tool calls used about 2,000 tokens; the free plan allows 8,000 per minute.

Third batch, same day: the agent planner, `hermano/planner.py`. The model gets three tools (`get_day`, `get_motivators`, `submit_plan`) and the code accepts only rewards after a class or special block and only ideas an adult approved; its suggestions, with their reasons, go to the teacher's and the family's reports, never to the child. If the model is down or writes a health word or contact data, the rule-based plan stands. In the first full demo against the real API (invented school, three children) Qwen planned the day of all three children in 28 seconds, every reward after a class and every suggestion inside the age filter. 90 tests.

Fourth batch, same day: the web views, `hermano/web.py` (`python -m hermano serve`). The child's view is made for the phone: the day step by step, a heads-up before each change, calm-down options, and three faces to say how each step went. It shows only titles an adult approved, never Hermano's suggestions or their reasons. Each adult gets a page with their report; the family and the teacher also get the new ideas, with the agent's reason, and Yes and No buttons. No request calls the language model or Qloo: the day's plans are built once per day and language (the rule-based plan at once, the agent's plan in the background) and kept in a private cache, so visitors do not spend the free quota. Every visitor gets their own copy of the approvals and the check-ins, so judges do not change each other's demo. The pages carry no scripts. 119 tests.

Fifth batch, same day: first runs against the real Qloo hackathon API. Video games are `urn:entity:videogame` (the documented `video_game` answers 400), and a type Qloo refuses is now skipped instead of emptying the whole profile. The real answers showed two holes, now closed by the age gate above: Genshin Impact (ESRB T) reached an 11-year-old, and an Australian rapper reached an 8-year-old. With real Qloo, the invented school's day now has real rewards: on the first day each child's own favorites, from the second day also the Qloo ideas the family approved. A Qloo idea no longer comes twice in one day, in the rule-based plan or in the agent's; the child's own favorites may come back, but only after every other approved idea. Movement breaks with the child's own music and shows, for children with that agreed support. Reinforcement ideas for the teacher, one per class, from the class topic and the child's approved tastes. Exercises from the specialist's closed catalog, with the child's own "I did it". An "I feel nervous" button on every step of the child's day. 168 tests.

Not done yet: the hosted demo.

Known limits: titles without a US content rating (for example, local Argentine shows) are left out by the rating filter. Some of Qloo's book and artist entries have no tags at all, so under 13 they are left out even when they are for children (seen: the first Harry Potter book, Canticuénticos). TV-PG still lets through shows written for teenagers. The ESRB ratings in the team's table may be wrong; the stricter rating wins. An interest that matches a child's name is refused even when it is also a real title. Suggested readings do not match the class topic yet.

## Run it

Python 3.9 or newer, no dependencies.

```bash
python -m unittest -v              # tests
python -m hermano demo             # one day: plans, the child view and the report for every adult
python -m hermano demo --lang es   # the same in Spanish
python -m hermano demo --days 3    # three days; each family approves one new idea per day (simulated)
python -m hermano privacy          # everything that was sent to Qloo
python -m hermano demo --planner rules   # never call the language model, even with a key
python -m hermano serve           # the web views on http://127.0.0.1:8000/
```

Or install it with `pip install .` and run `hermano demo`.

With a hackathon key, `export QLOO_API_KEY=...` and the same commands call `https://hackathon.api.qloo.com`. Never commit the key.

With a Groq key, `export GROQ_API_KEY=...` turns the language model on; `HERMANO_MODELS` (comma separated) changes the model order. Without it, Hermano uses its rule-based planner.

On a host, run `python -m hermano serve --host 0.0.0.0` (or set `HOST` and `PORT`), and set `HERMANO_HTTPS=1` behind HTTPS. The day's plans are cached in `cache/plans` (git-ignored; `HERMANO_PLAN_DIR` changes it), so a day costs at most one agent plan per child and language, however many people open the demo. `/healthz` reports which plan is being served.

## Public demo on Render

`render.yaml` is a Render Blueprint for one free web service: `pip install .`, then `python -m hermano serve --host 0.0.0.0` on the port Render gives, a health check on `/healthz`, and Python 3.12 (`.python-version`). Render asks for `GROQ_API_KEY` and `QLOO_API_KEY` when the Blueprint is created; the values never go into the repository. A free service sleeps after 15 minutes without visitors and takes about a minute to wake up, and its disk is not kept. So after waking up Hermano serves the rule-based plan at once, plans with the agent in the background, and the home page reloads itself every 20 seconds until the agent is done.

## Credits

Idea and product decisions: Jorge Abraham Mendieta (Aguaray, Salta, Argentina). Code written with AI assistance.

## License

MIT, see [LICENSE](LICENSE).
