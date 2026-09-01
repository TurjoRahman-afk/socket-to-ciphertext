# Design report

> Written across the project, submitted at the end of phase 8. This file is the
> outline; each section is filled in as the phase that produces it completes.

1. **Requirements** -- the brief's requirements as a table, each mapped to the
   phase and module that satisfies it.
2. **Architecture** -- the hub topology, and the client's split into
   connection / model / controller / view.
3. **Protocol** -- from `docs/protocol.md`, with the reasoning for
   newline-delimited JSON over the alternatives.
4. **Threading model** -- one reader and one writer thread per connection; the
   single queue where worker threads and the UI meet; where locks are held and
   why the GIL is not one.
5. **Connection state machine** -- the client session FSM and what each state
   allows the user to do.
6. **Security** -- the mechanism, the threat model, and the limitations, stated
   plainly.
7. **Persistence** -- schema, offline delivery, history.
8. **Testing** -- unit tests without sockets, integration tests with them, and
   the load check.
9. **Evaluation** -- what works, what does not, and what would be done
   differently.

## Figures

| Figure | Subject |
|--------|---------|
| 1 | The client, split so the view can arrive last |
| 2 | End-to-end path of one chat line |
| 3 | Frame format |
| 4 | Client session state machine |
| 5 | Repository layout |
