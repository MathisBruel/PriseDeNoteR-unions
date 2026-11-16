from bot import commands as cmd
from bot.aggregator import CaptionAggregator, strip_committed

CFG = {"command_prefix": "!", "cmd_record": "enregistrer", "cmd_stop": "stop", "cmd_leave": "quitter",
       "cmd_status": "statut"}


def test_parse_command_variants():
    assert cmd.parse_command("!enregistrer", CFG) == cmd.RECORD
    assert cmd.parse_command("  !Enregistrer la réunion svp ", CFG) == cmd.RECORD
    assert cmd.parse_command("!STOP", CFG) == cmd.STOP
    assert cmd.parse_command("!quitter", CFG) == cmd.LEAVE
    assert cmd.parse_command("!statut", CFG) == cmd.STATUS


def test_parse_command_ignores_normal_chat():
    for text in ("enregistrer", "il faut !enregistrer", "!", "!inconnu", "", "! enregistrer"):
        assert cmd.parse_command(text, CFG) is None or text == "! enregistrer"
    assert cmd.parse_command("on va quitter bientôt", CFG) is None


def test_custom_prefix_and_accents():
    cfg = {**CFG, "command_prefix": "/bot ", "cmd_record": "démarrer"}
    assert cmd.parse_command("/bot demarrer", cfg) == cmd.RECORD
    assert cmd.parse_command("/bot DÉMARRER", cfg) == cmd.RECORD
    assert cmd.parse_command("!enregistrer", cfg) is None


def test_allowed_senders():
    assert cmd.is_allowed("N'importe qui", [])
    assert cmd.is_allowed("alice martin", ["Alice Martin"])
    assert cmd.is_allowed("Élodie", ["elodie"])
    assert not cmd.is_allowed("Bob", ["Alice Martin"])
    assert not cmd.is_allowed("", ["Alice Martin"])
    assert cmd.is_self("You") and cmd.is_self("Vous") and not cmd.is_self("Youssef")


def test_strip_committed():
    assert strip_committed("", "abc") == "abc"
    assert strip_committed("Bonjour à tous.", "Bonjour à tous. On commence") == " On commence"
    # Meet a fait défiler le début du bloc
    assert strip_committed("Première phrase. Deuxième phrase.", "Deuxième phrase. Troisième") == " Troisième"
    # Retouche de casse dans la partie déjà publiée
    assert strip_committed("bonjour a tous", "Bonjour à tous et bienvenue").strip() == "et bienvenue"


def test_aggregator_emits_final_text_once_after_idle():
    agg = CaptionAggregator(idle=2)
    assert agg.update("1", "Alice", "Bon", 0.0) == []
    assert agg.update("1", "Alice", "Bonjour à", 0.5) == []
    assert agg.update("1", "Alice", "Bonjour à tous", 1.0) == []
    assert agg.flush(2.0) == []  # encore actif
    [seg] = agg.flush(3.5)
    assert (seg.speaker, seg.text, seg.start, seg.end) == ("Alice", "Bonjour à tous", 0.0, 1.0)
    assert agg.flush(10) == []  # pas de doublon

    # Le même bloc reprend : seule la suite est publiée.
    agg.update("1", "Alice", "Bonjour à tous. On commence.", 12.0)
    [seg] = agg.flush(15)
    assert seg.text == "On commence." and seg.start == 12.0


def test_aggregator_interleaved_speakers_and_force_flush():
    agg = CaptionAggregator(idle=2)
    agg.update("1", "Alice", "Qui commence ?", 0)
    agg.update("2", "Bob", "Moi", 1)
    agg.update("2", "Bob", "Moi, si vous voulez", 1.5)
    segments = agg.flush(1.6, force=True)
    assert [(s.speaker, s.text) for s in segments] == [("Alice", "Qui commence ?"), ("Bob", "Moi, si vous voulez")]
    assert agg.blocks == {}


def test_aggregator_splits_long_monologue_on_sentences():
    agg = CaptionAggregator(idle=2, max_chars=100, keep_tail=20)
    sentences = [f"Voici la phrase numéro {i} de ce long monologue." for i in range(8)]
    emitted, text = [], ""
    for i, sentence in enumerate(sentences):
        text = f"{text} {sentence}".strip()
        emitted += agg.update("1", "Alice", text, float(i))
    emitted += agg.flush(100)
    assert len(emitted) >= 3
    assert " ".join(s.text for s in emitted) == text  # rien de perdu, rien en double
    assert all(s.text.endswith(".") for s in emitted)


def test_aggregator_prime_skips_text_before_recording():
    agg = CaptionAggregator(idle=1)
    agg.prime("1", "Alice", "Ceci a été dit avant.", 0)
    agg.update("1", "Alice", "Ceci a été dit avant. Et ceci pendant.", 1)
    [seg] = agg.flush(5)
    assert seg.text == "Et ceci pendant."
