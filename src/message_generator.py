import json
import logging

from openai import OpenAI

logger = logging.getLogger("bot")

SYSTEM_PROMPT = """Du hilfst bei WG-Bewerbungen auf WG-Gesucht.de auf Deutsch.

Analysiere den Inseratetext und antworte NUR mit validem JSON in diesem Format:
{
  "codeword_line": "eine Zeile zum Einfügen am Anfang der Nachricht, oder leerer String"
}

Regeln für codeword_line:
- Prüfe, ob die Anzeige ein Codewort, Stichwort, Filterwort, Emoji oder Betreff verlangt
- Typische Formulierungen: "Codewort", "Code-Wort", "schreib das Wort", "schreib bitte",
  "Lieblingsobst", "Betreff", "Stichwort", "Passwort", "benutze folgenden Emoji",
  "nutze folgendes Emoji", "Antwort mit", Wörter in Anführungszeichen als Pflichtangabe
- Wenn ein Emoji verlangt wird: codeword_line ist genau dieses Emoji (ggf. mit kurzem Text davor)
- Wenn ein Wort in Anführungszeichen verlangt wird: codeword_line ist genau dieses Wort
- Wenn nichts verlangt wird: codeword_line muss "" sein
- Erfinde niemals ein Codewort"""


class MessageGenerator:
    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-chat",
        base_url: str = "https://api.deepseek.com",
    ):
        self.model = model
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def detect_codeword(
        self,
        listing_text: str,
        user_name: str,
        address: str,
        wg_type: str,
    ) -> str:
        user_prompt = (
            f"Anbieter: {user_name}\n"
            f"Adresse: {address}\n"
            f"Typ: {wg_type}\n\n"
            f"Inseratetext:\n{listing_text[:4000]}"
        )

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.4,
        )
        content = response.choices[0].message.content or "{}"
        data = json.loads(content)

        return str(data.get("codeword_line", "")).strip()


def build_message(
    config: dict,
    listing: dict,
    listing_text: str,
    logger_obj: logging.Logger,
) -> str:
    from src.message_template import (
        apply_codeword,
        load_template,
        render_template,
    )

    template = load_template(config["message_file"])
    ai_config = config.get("ai", {})
    api_key = ai_config.get("api_key", "")

    codeword_line = ""

    if api_key and listing_text.strip():
        try:
            generator = MessageGenerator(
                api_key=api_key,
                model=ai_config.get("model", "deepseek-chat"),
                base_url=ai_config.get("base_url", "https://api.deepseek.com"),
            )
            codeword_line = generator.detect_codeword(
                listing_text=listing_text,
                user_name=listing["user_name"],
                address=listing["address"],
                wg_type=listing.get("wg_type", ""),
            )
            if codeword_line:
                logger_obj.info(f"AI codeword_line: {codeword_line}")
        except Exception:
            logger_obj.exception("AI codeword detection failed, using template fallback.")
    elif not api_key:
        logger_obj.info("No DEEPSEEK_API_KEY — skipping AI codeword detection.")
    else:
        logger_obj.info("Empty listing text — skipping AI codeword detection.")

    message = render_template(template, {})
    message = apply_codeword(message, codeword_line)
    return message
