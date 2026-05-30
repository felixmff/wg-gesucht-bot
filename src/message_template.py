import re
from pathlib import Path


VARIABLES_MARKER = "---variables---"
MESSAGE_MARKER = "---message---"


def load_template(path: str) -> str:
    content = Path(path).read_text(encoding="utf-8")

    if content.lstrip().startswith(VARIABLES_MARKER):
        _, _, rest = content.partition(MESSAGE_MARKER)
        if not rest.strip():
            raise ValueError(f"Template {path} missing {MESSAGE_MARKER} section")
        return rest.strip()

    return content.strip()


def render_template(template: str, variables: dict[str, str]) -> str:
    message = template
    for key, value in variables.items():
        message = message.replace(f"{{{{{key}}}}}", value)
    message = re.sub(r"\{\{[a-z_]+\}\}", "", message)
    return message.strip()


def apply_codeword(message: str, codeword_line: str) -> str:
    codeword_line = codeword_line.strip()
    if not codeword_line:
        return message
    return f"{codeword_line}\n\n{message}"
