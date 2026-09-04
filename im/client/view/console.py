"""A console view. Phase 3, and throwaway by design -- but load-bearing.

Its job is to prove the model and controller are genuinely independent of any
particular interface, so that phase 7 can add a Tkinter view beside it rather
than rewriting anything. Deleting this file at the end must break nothing.

It is also a rehearsal for the Tk view's hardest part. Two things happen at
once here -- frames arriving on the connection's reader thread, and the user
typing on another -- and both are funnelled into ONE queue that a single loop
drains. That is exactly the queue-plus-root.after(50, poll) bridge phase 7
needs, minus the widgets. The model is therefore only ever touched by one
thread, which is what lets it have no locks in it.

    reader thread ---> post_frame() --+
                                      +--> inbox --> run() --> controller --> model
    stdin thread  ---> post_input() --+                          |
                                                                 v
                                                            events --> printed
"""

from __future__ import annotations

import queue
import sys
import threading

from im.client.controller.chat import ChatController
from im.client.model.events import (
    ConnectionStateChanged,
    ConversationSelected,
    ErrorRaised,
    Event,
    MessageAdded,
    PresenceChanged,
    RoomMembersChanged,
    RosterReplaced,
)
from im.common.frames import Frame

HELP = """
  /to <user|#room>   talk to someone, e.g. /to bob
  /who               who is online
  /history           replay the conversation on screen
  /chats             conversations, with unread counts

  /create #room      make a room and join it
  /join #room        join a room somebody else made
  /leave #room       leave a room
  /rooms             rooms you are in
  /members           who is in the room on screen

  /help              this
  /quit              leave

  anything else is sent to the conversation on screen
"""

_STOP = object()


class ConsoleView:
    """Renders the model to stdout and turns typed lines into gestures."""

    def __init__(self, controller: ChatController) -> None:
        self.controller = controller
        self.model = controller.model
        self._inbox: queue.Queue = queue.Queue()
        self._running = False
        self._unsubscribe = self.model.subscribe(self._render)

    # ------------------------------------------------------------- inbound ---

    def post_frame(self, frame: Frame) -> None:
        """Called on the connection's reader thread. Does not touch the model."""
        self._inbox.put(("frame", frame))

    def post_state(self, state: str) -> None:
        """Called on whichever thread changed the connection state."""
        self._inbox.put(("state", str(state)))

    def post_input(self, line: str) -> None:
        self._inbox.put(("input", line))

    def stop(self) -> None:
        self._inbox.put((_STOP, None))

    # ---------------------------------------------------------------- loop ---

    def run(self) -> None:
        """Drain the queue until told to stop. Everything the model sees
        happens on this thread and no other."""
        self._running = True
        self._banner()
        self._prompt()

        while self._running:
            kind, payload = self._inbox.get()
            if kind is _STOP:
                break
            try:
                if kind == "frame":
                    self.controller.on_frame(payload)
                elif kind == "state":
                    self.controller.on_state(payload)
                elif kind == "input":
                    self._typed(payload)
            except Exception as exc:  # noqa: BLE001 -- a view must not die
                print(f"  ! {type(exc).__name__}: {exc}")
            self._prompt()

        self._unsubscribe()

    def read_stdin_forever(self) -> threading.Thread:
        """Feed typed lines into the same queue as inbound frames."""

        def pump() -> None:
            for line in sys.stdin:
                self.post_input(line.rstrip("\n"))
            self.stop()  # stdin closed, e.g. piped input ran out

        thread = threading.Thread(target=pump, name="console-input", daemon=True)
        thread.start()
        return thread

    # ------------------------------------------------------------ gestures ---

    def _typed(self, line: str) -> None:
        text = line.strip()
        if not text:
            return

        if not text.startswith("/"):
            if self.model.active is None:
                print("  ! nobody selected -- try /to bob")
            elif self.controller.send(text) is None:
                print("  ! not sent")
            return

        command, _, argument = text.partition(" ")
        argument = argument.strip()

        if command in ("/quit", "/exit"):
            self._running = False
        elif command == "/help":
            print(HELP)
        elif command == "/who":
            online = self.model.online_users()
            print("  online:", ", ".join(online) if online else "(nobody else)")
        elif command == "/to":
            if argument:
                self.controller.select(argument)
            else:
                print("  ! usage: /to <user|#room>")
        elif command == "/history":
            self._history()
        elif command == "/chats":
            self._chats()
        elif command in ("/create", "/join", "/leave"):
            self._room_command(command, argument)
        elif command == "/rooms":
            rooms = self.model.my_rooms()
            print("  rooms:", ", ".join(rooms) if rooms else "(none -- try /create #general)")
        elif command == "/members":
            self._members()
        else:
            print(f"  ! unknown command {command} -- try /help")

    def _room_command(self, command: str, room: str) -> None:
        if not room:
            print(f"  ! usage: {command} #roomname")
            return
        if not room.startswith("#"):
            # Caught here so an obvious mistake does not need a round trip.
            print("  ! a room name starts with # -- try /join #general")
            return

        if command == "/create":
            self.controller.create_room(room)
        elif command == "/join":
            self.controller.join(room)
        else:
            self.controller.leave(room)
            if self.model.active == room:
                self.controller.select(None)

    def _members(self) -> None:
        room = self.model.active
        if room is None or not room.startswith("#"):
            print("  ! not in a room -- /to #general first")
            return
        members = self.model.room_members(room)
        print(f"  {room}:", ", ".join(members) if members else "(nobody)")

    # ------------------------------------------------------------ rendering ---

    def _render(self, event: Event) -> None:
        """The model's observer. Called only from run()'s thread."""
        if isinstance(event, MessageAdded):
            if event.conversation == self.model.active:
                print(f"\n  {self._line(event.message)}")
            else:
                unread = self.model.conversation(event.conversation).unread
                print(
                    f"\n  [{event.conversation}] {event.message.sender}: "
                    f"{event.message.body}   ({unread} unread)"
                )
        elif isinstance(event, RoomMembersChanged):
            who = ", ".join(event.members) if event.members else "nobody"
            print(f"\n  * {event.room}: {who}")
        elif isinstance(event, PresenceChanged):
            print(f"\n  * {event.user} is {'online' if event.online else 'offline'}")
        elif isinstance(event, ConversationSelected):
            print(f"\n  -- now talking to {event.conversation} --")
        elif isinstance(event, ConnectionStateChanged):
            print(f"\n  * connection {event.state}")
        elif isinstance(event, ErrorRaised):
            print(f"\n  ! {event.code}: {event.message}")
        elif isinstance(event, RosterReplaced):
            if event.users:
                print(f"\n  * online: {', '.join(event.users)}")

    def _line(self, message) -> str:
        who = "you" if message.mine else message.sender
        return f"{who}: {message.body}"

    def _history(self) -> None:
        if self.model.active is None:
            print("  ! nobody selected")
            return
        conversation = self.model.conversation(self.model.active)
        if not conversation.messages:
            print("  (nothing yet)")
        for message in conversation.messages:
            print(f"  {self._line(message)}")

    def _chats(self) -> None:
        keys = self.model.keys()
        if not keys:
            print("  (no conversations yet)")
            return
        for key in keys:
            conversation = self.model.conversation(key)
            marker = "*" if key == self.model.active else " "
            unread = f"  ({conversation.unread} unread)" if conversation.unread else ""
            print(f"  {marker} {key}  {len(conversation)} messages{unread}")

    def _banner(self) -> None:
        print(f"\n  logged in as {self.model.username}. /help for commands.")

    def _prompt(self) -> None:
        active = self.model.active or "nobody"
        print(f"\n[{active}] > ", end="", flush=True)
