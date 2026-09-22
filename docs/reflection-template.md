# Individual Reflection -- template

> **One per person, written by that person, in their own words.**
>
> This file is a template and a set of prompts. It is deliberately not a
> draft: a reflection is an account of what *you* did and what *you* learned,
> and five reflections written by the same hand would read like five
> reflections written by the same hand. Markers notice.
>
> Copy this file to `docs/reflection-<yourname>.md`, delete the prompts as you
> answer them, and keep your own voice. Two honest paragraphs are worth more
> than two polished pages.

---

## Suggested length

400--800 words. Long enough to say something, short enough that you have to
choose what matters.

---

## 1. What I worked on

*Be specific. "I helped with the server" says nothing; "I wrote the room
registry and the membership tests, and rewrote the fan-out after we found it
was holding the lock while sending" says exactly what you did.*

If you are not sure what you touched, `git log --author="<your name>"` will
remind you.

---

## 2. Something I got wrong, and what it taught me

*This is the section markers actually read.*

A reflection that says everything went well says nothing. Pick one real
thing: a bug you introduced, a design you argued for that turned out badly, a
misunderstanding that cost the team time. Say what you believed, why it was
wrong, and what you do differently now.

If you need candidates, the project has several documented honestly:

- The threat model claimed room messages were encrypted per member when they
  were plaintext. It was corrected to admit it, and only then was the gap
  actually closed. What does it mean that the document was wrong before the
  code was?
- Three bugs passed the entire test suite because every test covered one side
  of a boundary and nothing covered the boundary (`docs/testing-report.md`
  §5). Did you write any of those tests? What would you check now?
- The GUI looked "pixelated" and it was not a drawing problem at all -- the
  process had not declared DPI awareness, so Windows was stretching it. How
  long did we look in the wrong place, and why?
- The contact list showed everyone who happened to be online, because presence
  and contacts had been treated as the same thing. Two ideas wearing one name.

Pick one you were genuinely involved in. Do not borrow one you were not.

---

## 3. Something I learned that I did not expect to

*Prompts, not answers -- write whichever is true for you.*

- What did you believe about threads, sockets, or encryption before this
  project that turned out to be wrong?
- Was there a moment where the "obvious" solution was the wrong one?
- What did measuring something teach you that reading about it had not?
- Did any part of the design that seemed like extra work at the time end up
  saving you later?

---

## 4. Working as a team of five

*Honest, not diplomatic. What actually happened?*

- How did you divide work, and did that division survive contact with the
  project?
- What was harder than expected about five people in one repository?
- Was there a disagreement worth having? How did it resolve?
- What would you do differently in the first week if you started again?

---

## 5. What I would do with another two weeks

*One or two concrete things, with a reason -- not a wish list.*

The project's own list of what is unfinished is in `docs/design.md` §9.2 and
`docs/threat-model.md`. Pick what *you* would do and say why that one.

---

## Factual material you may draw on

All of this is accurate as of the third submission. Use what is relevant to
what you actually did; do not recite the lot.

**Scale.** 6,860 lines of source, 4,481 lines of tests, 352 tests, 83%
statement coverage, 24 protocol frame types, nine phases.

**Performance.** Delivery p50 0.55ms including the database write. 4,061
messages per second through one server. 388 of 400 concurrent clients, where
the limit is 1,954 OS threads rather than anything about the protocol.

**Architecture.** A hub rather than peer-to-peer. Thread per connection with
a bounded queue to a writer thread. The router imports no `socket`, no `ssl`
and no `threading`, which is why its 84 tests need no network. The client
splits into `net` / `model` / `controller` / `view`, and a test fails the
build if the model ever imports a GUI library.

**Security.** X25519 to HKDF to AES-GCM, a fresh nonce per message. Room
messages sealed once per member, so the server holds every envelope and can
open none. Metadata is not protected and we say so. TLS is a separate,
optional layer.

**Things that went wrong, documented rather than hidden.** `docs/design.md`
§8.4 and §9.3; `docs/testing-report.md` §5; the header of
`docs/threat-model.md`.

---

## Before you submit

- [ ] It is in your own words, and sounds like you
- [ ] Section 2 names a real mistake, not a modest-sounding strength
- [ ] Nothing in it contradicts the design report or the testing report
- [ ] You have not claimed work somebody else did
- [ ] Filename is `docs/reflection-<yourname>.md`
