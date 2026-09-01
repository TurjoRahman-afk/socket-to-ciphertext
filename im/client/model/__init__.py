"""Application state: conversations, roster, rooms, unread counts.

Nothing in this package may import tkinter, now or ever. That rule is what
lets the console view and the Tk view be two interchangeable views over one
model -- and it is the evidence for the MVC claim in the report. It is
enforced by tests/test_model_has_no_tkinter.py.
"""
