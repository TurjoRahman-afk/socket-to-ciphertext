"""The Tkinter view. Phase 7 -- deliberately last.

A separate design pass decides layout, visual language and interaction detail
before any code lands here. Nothing in phases 0 through 6 depends on that
decision.

The queue-plus-after() bridge is the only place worker threads and widgets
meet: the reader thread pushes decoded frames onto a queue.Queue, and the main
thread drains it in a root.after(50, poll) loop that reschedules itself.
Calling a widget method from a worker thread corrupts state silently instead
of raising.
"""
