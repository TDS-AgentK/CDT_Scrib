import re

NAME_RE = re.compile(r"^[a-z0-9_-]{1,80}$")


class InteractionType:
    PING = 1
    APPLICATION_COMMAND = 2
    MESSAGE_COMPONENT = 3
    APPLICATION_COMMAND_AUTOCOMPLETE = 4
    MODAL_SUBMIT = 5


class ResponseType:
    PONG = 1
    CHANNEL_MESSAGE_WITH_SOURCE = 4
    APPLICATION_COMMAND_AUTOCOMPLETE_RESULT = 8
    MODAL = 9


class ComponentType:
    ACTION_ROW = 1
    TEXT_INPUT = 4


class TextInputStyle:
    SHORT = 1
    PARAGRAPH = 2


EPHEMERAL_FLAG = 1 << 6


def valid_name(name: str) -> bool:
    return bool(NAME_RE.match(name))
