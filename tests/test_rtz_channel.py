"""
Messages RÉELS du canal RTZ ACADEMY (historique mai → nov. 2026) : formats de signaux,
messages de gestion (BE, TP1 touché, clôtures manuelles, partiels) et cas pièges.
"""
import pytest

from parser.regex_parser import RegexParser, looks_like_signal
from parser.update_parser import parse_update, parse_update_no_reply
from tests.test_updates import FakeMT5, make_bot

# Signal RTZ type (format le plus fréquent : TP1 chiffré + TP2 OPEN/OUVERT)
SIG = """🚨 BUY XAUUSD **__

__****__➡️__****__ENTRY : 4066-4069

__****__🔴__****__SL : 4062

__****__🟢__****__TP1: 4072
__****__🟢__****__TP2: OUVERT


__**⚠️**__Disclaimer : Il ne s’agit en aucun cas d’un conseil financier mais uniquement d’une alerte à titre indicative."""


class RichFakeMT5(FakeMT5):
    """FakeMT5 + volume / profit des positions + clôture partielle."""

    def __init__(self, price=4068.0, lot=0.01):
        super().__init__(price)
        self.lot = lot

    async def send_market_order(self, symbol, direction, lot, sl, tp, comment, known_price=None):
        r = await super().send_market_order(symbol, direction, lot, sl, tp, comment, known_price)
        self.positions[r["ticket"]].update(volume=lot, profit=0.0)
        return r

    def set_profit(self, profit):
        for p in self.positions.values():
            p["profit"] = profit

    async def close_position(self, t, volume=None):
        self.log.append(("close", t, volume))
        if volume and volume < self.positions[t]["volume"] - 1e-9:
            self.positions[t]["volume"] = round(self.positions[t]["volume"] - volume, 2)
        else:
            self.positions.pop(t, None)
        return True


def cmds(text):
    return [str(c) for c in parse_update(text)]


# ── Parser de gestion : vrais messages ────────────────────────────────────────

@pytest.mark.parametrize("text, expected", [
    ("❌ MODIFIER SL A 4036.20", ["SL→4036.2"]),
    ("❌ MODIFIER SL 4688", ["SL→4688.0"]),
    ("❌ JE REMODIFIE LE SL À 4394.50 !", ["SL→4394.5"]),
    ("❌ SI VOUS N’AVEZ PAS ENCORE SÉCURISÉ VOS PROFITS, MODIFIEZ VOTRE SL À 4053 !", ["SL→4053.0"]),
    ("✅ MODIFIER TP1 A 4046", ["TP1→4046.0"]),
    ("✅ MODIFIER TP A 4707", ["TP→4707.0"]),
    ("✅ TP MODIFIER 4559", ["TP→4559.0"]),
    ("🚨 MODIFICATION TP1\n🟢 TP1 : 4255\n🔒 Pensez à sécuriser vos positions", ["TP1→4255.0"]),
    # Ancien bug grave : "TP2 ... 50%" lisait TP2 = 50 → TP modifié à ~4050
    ("✅ METTEZ BE SUR TP2 ET CLÔTUREZ 50% DES PROFITS", ["PARTIAL 50%", "BE TP2"]),
    ("✅ +60 PIPS LAISSEZ TP2 COURRIR ET METTEZ BE SUR TP2 !", ["BE TP2"]),
    ("✅ METTEZ BE TP1 & TP2 ET SÉCURISEZ 50% DES PROFITS MANUELLEMENT", ["PARTIAL 50%", "BE TP1+TP2"]),
    ("🔒 Déplacez votre stop loss au point d’entrée sur votre TP2. ✅", ["BE TP2"]),
    ("mettez vous BE", ["BE"]),
    ("📊 TRADE UPDATE / Vous pouvez BE 🔒", ["BE"]),
    # L'exemple demandé : BE + TP1 touché → on encaisse TP1 et on met le reste au BE
    ("BE, TP1 touché", ["TP_HIT TP1", "BE"]),
    ("TP1 touché ✅ mettez vous BE", ["TP_HIT TP1", "BE"]),
    ("✅TP1 CHECK +50 PIPS ⏳ ✅ METTEZ VOUS BE SUR TP2", ["TP_HIT TP1", "BE TP2"]),
    ("✅ INSTANT TP", ["TP_HIT TP"]),
    # Clôtures manuelles d'une jambe
    ("**__✅__****__ PRENEZ VOTRE TP1 MANUELLEMENT MAINTENANT.__**", ["CLOSE_LEG TP1"]),
    ("✅ CLÔTUREZ TP2 MANUELLEMENT", ["CLOSE_LEG TP2"]),
    ("🔥 GOLD 4037 +90 PIPS CLÔTUREZ LE TP2 MANUELLEMENT MAINTENANT 🤫", ["CLOSE_LEG TP2"]),
    ("🔥 Prenez votre TP1 maintenant pour éviter les spreads et sécuriser vos gains !\n🔒 Mettez votre TP2 à BE",
     ["CLOSE_LEG TP1", "BE TP2"]),
    ("✅ SÉCURISEZ TP1 ET CLÔTUREZ PARTIELLEMENT 50% TP2 ET METTEZ VOUS BE !",
     ["CLOSE_LEG TP1", "PARTIAL 50% TP2", "BE"]),
    # Fin de trade
    ("🟰 BE HIT", ["BE_HIT"]),
    ("🟰 BE HIT SUR TP2 !", ["BE_HIT TP2"]),
    ("❌ SL HIT !", ["SL_HIT"]),
    ("✅ CLÔTUREZ LE TRADE MANUELLEMENT +100 PIPS", ["CLOSE"]),
    ("Clôturez tout", ["CLOSE"]),
    ("❌ OUT", ["CLOSE"]),
    ("🚨 EXIT SHORT", ["CLOSE"]),
    ("**__🔥__****__ ON A PRIS NOS PROFITS A 3689.50 ET ON EST SORTIS DU TRADE AU PARFAIT TIMING LET’S GOOO ! __**",
     ["CLOSE"]),
    ("✅ SÉCURISEZ LE TRADE EN PROFIT DES QUE VOUS POUVEZ", ["CLOSE (si profit)"]),
    ("✅ SÉCURISEZ 75% ET METTEZ VOUS BE", ["PARTIAL 75%", "BE"]),
])
def test_ordres_reels(text, expected):
    assert cmds(text) == expected


@pytest.mark.parametrize("text", [
    # conditions / futur / conseils
    "SI ON REJETTE AU NIVEAUX 82-83 JE VOUS CONSEILLE DE VOUS METTRE BE",
    "ON VA RETESTER LA ZONE SOYEZ A L’AFFÛT SUR LE BE",
    "Quand le prix atteindras 3862 clôturez manuellement partiellement le tp1 à 80% et mettez be tp2.",
    "**__On verra pour le break Even dans 2 min pour la clôture de la bougie.__**",
    "On va bientôt pouvoir se mettre BE ! 🔒",
    "✅ VOUS AVEZ ENCORE LA POSSIBILITÉ DE CLÔTURER PROCHE DU TP",
    "ON APPROCHE DU TP N’HÉSITEZ PAS À CLÔTURER MANUELLEMENT POUR ÉVITER DE VOUS FAIRE AVOIR PAR LE SPREAD.",
    "🙏 NE LE PRENEZ PAS POUR ENCORE ON EST TOUJOURS PAS DANS LA ZONE",
    "🧨 ON EST DANS LA ZONE DE PROFIT AVEC UNE TELLE VOLATILITÉ PRENEZ PAS DE RISQUE",
    # infos / résultats
    "✅ +50 PIPS !", "🔥 GOLD 4126", "✅ FROM 4040", "⏳ TP2 EN COURS GOLD 4033 🔥", "+60pips puis BE….   Standby",
    "✅ MEILLEUR PE 40 PIPS DE SL 50 DE TP", "📊 Trade update : gérez bien vos lots, l’équipe !",
    # ancien faux positif : "SL ... A 4522" lu comme nouveau SL
    "⚠️ GÉRER VOS LOTS GROS SL SI VOUS ENTRÉE A 4522 ON VOUS METS EN GARDE",
    # ancien faux positif : "PRENEZ QUE TP1 ET TP2" lu comme TP1 = 2
    "✅ PRENEZ QUE TP1 ET TP2",
    # dispatching : le bot n'a qu'une entrée → pas de fermeture totale
    "Clôturez les positions les plus hautes en profits ✅",
    # textes pédagogiques
    "📌 PETITE PIQÛRE DE RAPPEL\n⚠️ SE METTRE EN BREAKEVEN ET DÉPLACER LE STOP LOSS AU POINT D’ENTRÉE.",
    "⚠️ IMPORTANT : Une fois que l’annonce « TP HIT » ou « SL HIT » est postée, ne rentrez surtout pas dans le trade.",
])
def test_messages_sans_ordre(text):
    assert cmds(text) == []


def test_sans_reponse_constats_ignores():
    assert parse_update_no_reply("🟰 BE HIT") == []
    assert parse_update_no_reply("J’ai pas mis bcp de lot et à chaque fois sa touché le BE") == []
    assert [str(c) for c in parse_update_no_reply("❌ MODIFIER LE SL À 4500")] == ["SL→4500.0"]


# ── Parser de signaux ─────────────────────────────────────────────────────────

def test_tp_ouvert_devient_jambe_open():
    sig, _ = RegexParser().parse(SIG, 1, 1)
    assert sig.take_profits == [4072.0, None]
    assert looks_like_signal(SIG)


def test_sl_a_definir_refuse():
    text = SIG.replace("SL : 4062", "SL :  A DÉFINIR")
    sig, _ = RegexParser().parse(text, 1, 1)
    assert sig is None


# ── Bout en bout avec le bot (MT5 simulé) ─────────────────────────────────────

@pytest.mark.asyncio
async def test_signal_poste_en_reponse_a_la_pre_alerte_est_bien_trade():
    """Bug réel (#9595, #10671...) : le signal en réponse à 'XAUUSD BUY NOW' était pris pour une gestion."""
    mt5 = RichFakeMT5(price=4068.0)
    bot = make_bot(mt5, lot=0.02)
    await bot._on_telegram_message("XAUUSD BUY NOW", 100, 1, False)
    await bot._on_telegram_message(SIG, 101, 1, False, reply_to=100)
    assert len(mt5.positions) == 2
    # la gestion postée en réponse à la PRÉ-ALERTE s'applique au signal
    await bot._on_telegram_message("❌ MODIFIER SL A 4060", 102, 1, False, reply_to=100)
    assert all(p["sl"] == 4060.0 for p in mt5.positions.values())


@pytest.mark.asyncio
async def test_tp1_touche_be_ferme_tp1_et_protege_le_reste():
    mt5 = RichFakeMT5(price=4068.0)
    bot = make_bot(mt5, lot=0.02)
    await bot._on_telegram_message(SIG, 200, 1, False)
    tp1, tp2 = sorted(mt5.positions, key=lambda t: mt5.positions[t]["comment"])
    assert mt5.positions[tp1]["comment"] == "TG#200_TP1" and mt5.positions[tp2]["comment"] == "TG#200_TP2_OPEN"
    mt5.set_profit(3.5)
    await bot._on_telegram_message("BE, TP1 touché", 201, 1, False, reply_to=200)
    assert tp1 not in mt5.positions                       # TP1 encaissé manuellement
    assert mt5.positions[tp2]["sl"] == 4068.0             # TP2 (OPEN) au BE


@pytest.mark.asyncio
async def test_tp1_touche_mais_en_perte_on_ne_ferme_pas():
    mt5 = RichFakeMT5(price=4068.0)
    bot = make_bot(mt5, lot=0.02)
    await bot._on_telegram_message(SIG, 210, 1, False)
    mt5.set_profit(-2.0)
    await bot._on_telegram_message("TP1 ✅", 211, 1, False, reply_to=210)
    assert len(mt5.positions) == 2


@pytest.mark.asyncio
async def test_be_sur_tp2_et_50_pourcent_ne_touche_pas_au_tp():
    mt5 = RichFakeMT5(price=4068.0)
    bot = make_bot(mt5, lot=0.02)
    await bot._on_telegram_message(SIG, 300, 1, False)
    await bot._on_telegram_message("✅ METTEZ BE SUR TP2 ET CLÔTUREZ 50% DES PROFITS", 301, 1, False, reply_to=300)
    assert len(mt5.positions) == 1                        # 50 % de 0.02 = la jambe TP1 fermée
    (left,) = mt5.positions.values()
    assert left["comment"].endswith("TP2_OPEN") and left["sl"] == 4068.0 and left["tp"] == 0.0


@pytest.mark.asyncio
async def test_cloturez_tp2_ne_ferme_que_tp2():
    mt5 = RichFakeMT5(price=4068.0)
    bot = make_bot(mt5, lot=0.02)
    await bot._on_telegram_message(SIG, 400, 1, False)
    await bot._on_telegram_message("✅ CLÔTUREZ TP2 MANUELLEMENT", 401, 1, False, reply_to=400)
    assert [p["comment"] for p in mt5.positions.values()] == ["TG#400_TP1"]


@pytest.mark.asyncio
async def test_reponse_a_une_reponse_remonte_au_signal():
    mt5 = RichFakeMT5(price=4068.0)
    bot = make_bot(mt5, lot=0.02)
    await bot._on_telegram_message(SIG, 500, 1, False)
    await bot._on_telegram_message("✅ +40 PIPS", 501, 1, False, reply_to=500)
    await bot._on_telegram_message("🟰 BE HIT", 502, 1, False, reply_to=501)
    assert mt5.positions == {}


@pytest.mark.asyncio
async def test_sl_hit_annule_les_ordres_en_attente():
    mt5 = RichFakeMT5(price=4075.0)                       # au-dessus de la zone → ordres en attente
    bot = make_bot(mt5, lot=0.02)
    await bot._on_telegram_message(SIG, 600, 1, False)
    assert len(mt5.orders) == 2
    await bot._on_telegram_message("❌ SL HIT !", 601, 1, False, reply_to=600)
    assert mt5.orders == {}


@pytest.mark.asyncio
async def test_ordre_sans_reponse_va_au_dernier_signal():
    mt5 = RichFakeMT5(price=4068.0)
    bot = make_bot(mt5, lot=0.02)
    await bot._on_telegram_message(SIG, 700, 1, False)
    await bot._on_telegram_message("❌ MODIFIER SL A 4061", 701, 1, False)      # pas de reply
    assert all(p["sl"] == 4061.0 for p in mt5.positions.values())


@pytest.mark.asyncio
async def test_sl_faute_de_frappe_ignore():
    mt5 = RichFakeMT5(price=4068.0)
    bot = make_bot(mt5, lot=0.02)
    await bot._on_telegram_message(SIG, 800, 1, False)
    await bot._on_telegram_message("❌ MODIFIER SL A 4562", 801, 1, False, reply_to=800)   # 4062 voulu
    assert all(p["sl"] == 4062.0 for p in mt5.positions.values())


@pytest.mark.asyncio
async def test_sl_ne_recule_pas_une_jambe_deja_au_be():
    mt5 = RichFakeMT5(price=4068.0)
    bot = make_bot(mt5, lot=0.02)
    await bot._on_telegram_message(SIG, 900, 1, False)
    await bot._on_telegram_message("mettez vous BE", 901, 1, False, reply_to=900)
    await bot._on_telegram_message("❌ MODIFIER SL A 4060", 902, 1, False, reply_to=900)
    assert all(p["sl"] == 4068.0 for p in mt5.positions.values())
