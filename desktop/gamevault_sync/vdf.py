"""Valve KeyValue (VDF/ACF) parser for Steam's screenshots.vdf and app manifests."""


class VDFParseError(Exception):
    pass


def _vdf_tokenize(text: str) -> list[str]:
    """Tokenize a Valve KeyValue string into a list of tokens.

    Tokens are quoted strings (without the quotes), or the braces { }.
    Whitespace is skipped.  ``//`` comments run to end-of-line.
    Backslash escapes inside quoted strings are honoured.
    """
    tokens: list[str] = []
    i = 0
    length = len(text)
    while i < length:
        ch = text[i]

        # Skip whitespace
        if ch in (" ", "\t", "\r", "\n"):
            i += 1
            continue

        # Skip // line comments
        if ch == "/" and i + 1 < length and text[i + 1] == "/":
            i += 2
            while i < length and text[i] != "\n":
                i += 1
            continue

        # Braces
        if ch in ("{", "}"):
            tokens.append(ch)
            i += 1
            continue

        # Quoted string
        if ch == '"':
            i += 1  # skip opening quote
            buf: list[str] = []
            while i < length:
                c = text[i]
                if c == "\\":
                    i += 1
                    if i < length:
                        esc = text[i]
                        if esc == "n":
                            buf.append("\n")
                        elif esc == "t":
                            buf.append("\t")
                        elif esc == "\\":
                            buf.append("\\")
                        elif esc == '"':
                            buf.append('"')
                        else:
                            buf.append(esc)
                        i += 1
                    continue
                if c == '"':
                    i += 1  # skip closing quote
                    break
                buf.append(c)
                i += 1
            tokens.append("".join(buf))
            continue

        # Unquoted token (up to whitespace or special char)
        buf2: list[str] = []
        while i < length and text[i] not in (" ", "\t", "\r", "\n", '"', "{", "}"):
            buf2.append(text[i])
            i += 1
        if buf2:
            tokens.append("".join(buf2))
            continue

        i += 1

    return tokens


def _vdf_parse_tokens(tokens: list[str], pos: int = 0) -> tuple[dict, int]:
    """Parse tokens into a nested dict.  Returns ``(result_dict, next_pos)``."""
    result: dict = {}
    while pos < len(tokens):
        token = tokens[pos]
        if token == "}":
            return result, pos + 1
        key = token
        pos += 1
        if pos >= len(tokens):
            raise VDFParseError(f"Unexpected end after key {key!r}")
        nxt = tokens[pos]
        if nxt == "{":
            pos += 1
            child, pos = _vdf_parse_tokens(tokens, pos)
            result[key] = child
        else:
            result[key] = nxt
            pos += 1
    return result, pos


def vdf_parse(text: str) -> dict:
    """Parse a Valve KeyValue string into a nested dict."""
    tokens = _vdf_tokenize(text)
    result, _ = _vdf_parse_tokens(tokens)
    return result
