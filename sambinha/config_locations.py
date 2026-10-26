"""Locate keys in TOML that has already been validated by tomllib."""
import re
import tomllib

_TOKENS = re.compile(
    r'"""(?:[^"\\]|\\[\s\S]|"(?!""))*""""{0,2}'
    r"|'''[\s\S]*?'''\'{0,2}"
    r'|"(?:[^"\\\n]|\\.)*"'
    r"|'[^'\n]*'"
    r'|\#[^\n]*|[^\S\n]+|\n|[=.,\[\]{}]|[^\s=.,\[\]{}\#\"\x27]+'
)


def key_lines(source: str) -> dict[tuple, int]:
    """Track table, array and inline-table paths without inspecting value text."""
    tokens = []
    line = 1
    for match in _TOKENS.finditer(source):
        token = match.group()
        if not token.startswith('#') and (token == '\n' or not token.isspace()):
            tokens.append((token, line))
        line += token.count('\n')
    position = 0
    locations = {}
    arrays = {}

    def peek():
        return tokens[position][0] if position < len(tokens) else ''

    def read_key(end):
        nonlocal position
        start = position
        while peek() != end:
            position += 1
        data = tomllib.loads(''.join(token for token, _ in tokens[start:position]) + '=0')
        keys = []
        while isinstance(data, dict):
            key, data = next(iter(data.items()))
            keys.append(key)
        position += 1
        return tuple(keys)

    def record(path, number):
        # A dotted key or subtable also defines each of its parent keys.
        for length in range(1, len(path) + 1):
            locations.setdefault(path[:length], number)

    def assignment(prefix):
        number = tokens[position][1]
        path = prefix + read_key('=')
        record(path, number)
        value(path)

    def value(prefix):
        nonlocal position
        if peek() in ('[', '{'):
            opening = peek()
            closing = ']' if opening == '[' else '}'
            position += 1
            index = 0
            while peek() != closing:
                if peek() in ('\n', ','):
                    position += 1
                elif opening == '{':
                    assignment(prefix)
                else:
                    value(prefix + (index,))
                    index += 1
            position += 1
        else:
            while peek() and peek() not in ('\n', ',', ']', '}'):
                position += 1

    table = ()
    while peek():
        if peek() == '\n':
            position += 1
        elif peek() == '[':
            number = tokens[position][1]
            position += 1
            is_array = peek() == '['
            if is_array:
                position += 1
            keys = read_key(']')
            if is_array:
                position += 1
            table = ()
            for index, key in enumerate(keys):
                table += (key,)
                if is_array and index == len(keys) - 1:
                    arrays[table] = arrays.get(table, -1) + 1
                if table in arrays:
                    table += (arrays[table],)
            record(table, number)
        else:
            assignment(table)
    return locations
