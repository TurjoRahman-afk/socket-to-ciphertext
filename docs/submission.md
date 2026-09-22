# Third submission -- 30 September 2026, 11:59pm

What the deadline asks for, and where each piece is.

| Deliverable | Where | State |
|---|---|---|
| **Implementation** | the repository | done |
| **Tests** | `tests/`, 352 of them | done |
| **Testing report** | [docs/testing-report.md](testing-report.md) | done |
| **Revised design** | [docs/design.md](design.md) | done -- revision history in §0 |
| **Individual reflection** | `docs/reflection-<name>.md`, one each | **five still to write** |

Supporting documents, not asked for but worth submitting:

| | |
|---|---|
| [docs/protocol.md](protocol.md) | the wire format, all 24 frame types |
| [docs/threat-model.md](threat-model.md) | what the encryption does and does not protect |
| [docs/demo.md](demo.md) | how to demonstrate it, in the order that makes the point |
| [README.md](../README.md) | how to run it |

---

## The five reflections

This is the only outstanding item, and it cannot be delegated -- a reflection
is a personal account. [docs/reflection-template.md](reflection-template.md)
has the prompts and the factual material to draw on.

| Student ID | Name | File | Done |
|---|---|---|:--:|
| 1820242075 | Aiuna | `docs/reflection-aiuna.md` | ☐ |
| 1820242085 | Faiza | `docs/reflection-faiza.md` | ☐ |
| 1820242086 | Turjo | `docs/reflection-turjo.md` | ☐ |
| 1820242087 | Aya | `docs/reflection-aya.md` | ☐ |
| 1820242109 | Keisha | `docs/reflection-keisha.md` | ☐ |

Two sections of the design report are also still marked
**[write this yourselves]**: §9.3 (what we would do differently) and §9.4
(individual contributions). §9.4 in particular should be filled in by the
five of you together, not by one person guessing.

---

## Before submitting

```bash
pytest                    # 352 pass
ruff check .              # clean
python -m demo.bench      # reproduces the numbers in the testing report
run.bat fresh             # the application actually starts
```

- [ ] All five reflections written
- [ ] design.md §9.3 and §9.4 filled in
- [ ] Nothing in any document contradicts any other document
- [ ] The demo has been rehearsed once, start to finish

That last one matters more than it sounds. The first run after `run.bat fresh`
generates two keypairs and a database, which takes a couple of seconds --
fine when you expect it, alarming in front of a room.

---

## Known gaps, stated rather than hidden

Both are in the documents already; they are collected here so nobody is
surprised by a question.

- **The cross-internet tunnel has not been run.** It needs a tunnelling
  client that is not installed on the development machine. What the code is
  responsible for *has* been verified: TLS over a real non-loopback
  interface, direct messages and rooms both. `docs/demo.md` §7 says this in
  those words.
- **Room receipts do not exist.** Delivery and read receipts are direct
  messages only. A room receipt needs per-member state -- "read by 3 of 5" --
  which is a different model rather than a bigger version of this one.
