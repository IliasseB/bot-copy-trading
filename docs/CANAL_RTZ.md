# Le canal RTZ ACADEMY : comment il publie, comment le bot le lit

Analyse de ~4000 messages (mai → novembre 2026).

## Les signaux (~410)
Format quasi constant :
```
🚨 BUY XAUUSD            (ou SELL, VENTE GOLD, ACHAT GOLD)
➡️ ENTRY : 4066-4069     (zone ; parfois un seul prix, parfois "NOW")
🔴 SL : 4062             (3 fois "A DÉFINIR" → signal refusé, jamais de trade sans SL)
🟢 TP1: 4072
🟢 TP2: OPEN             (ou Open, OUVERT, un prix, parfois TP3)
```
- Presque tous les signaux sont **modifiés** après publication (correction, ajout d'un TP…).
- Une pré-alerte « XAUUSD BUY NOW » précède le signal d'environ 100 s ; le signal est parfois
  posté **en réponse** à cette pré-alerte.
- 8 signaux gardent une faute de frappe jamais corrigée (ex. `ENTRY 4423 / SL 4521`) : refusés.

## La gestion (~950 messages)
- **~90 %** sont des **réponses** au signal (parfois réponse à une réponse).
- Convention d'emoji : ❌ = changement de SL, ✅ = TP / info, 🟰 = BE HIT.
- **Un quart** sont des **images sans texte** (voir [IMAGES.md](IMAGES.md)).

| Formulation fréquente | Commande |
|---|---|
| MODIFIER SL A x / DÉPLACEZ SL A x / JE REMODIFIE LE SL À x | `SL` |
| MODIFIER TP1 A x / MODIFIER TP A x / TP MODIFIER x | `TP` (TP sans n° = 1re position avec TP chiffré) |
| METTEZ (VOUS) BE (SUR TP2) / DÉPLACEZ LE SL AU PRIX D'ENTRÉE | `BE` |
| TP1 TOUCHÉ / TP1 ✅ / TP1 CHECK / INSTANT TP | `TP_HIT` (encaisse si en profit) |
| CLÔTUREZ TP2 MANUELLEMENT / PRENEZ VOTRE TP1 | `CLOSE_LEG` |
| SÉCURISEZ 50 % / CLÔTUREZ 75 % DES PROFITS | `PARTIAL` |
| CLÔTUREZ LE TRADE / CLÔTUREZ TOUT / OUT / EXIT SHORT | `CLOSE` |
| BE HIT / BE TOUCHÉ | `BE_HIT` (ferme le reste) |
| SL HIT / SL TOUCHÉ | `SL_HIT` (ferme le reste, annule les ordres en attente) |

## Ce qui est volontairement ignoré
- Conditions et futur : « SI on rejette… », « QUAND le prix… », « on verra pour le BE ».
- Conseils : « n'hésitez pas à clôturer », « possibilité de sécuriser proche du TP ».
- Infos : « +50 PIPS », « GOLD 4126 », « TP2 EN COURS », « MEILLEUR PE ».
- Dispatching : « clôturez les positions les plus hautes » (le bot n'a qu'une entrée).
- Textes pédagogiques (rappel, conseil, bilan) et lignes de plus de 160 caractères.

## Sans « répondre »
Un ordre clair (SL, TP, BE, clôture) posté sans répondre est appliqué au **dernier signal**,
seulement si c'est bien lui qui a des trades ouverts. Les constats (« BE HIT », « SL touché »)
sans réponse sont ignorés : ce sont souvent des témoignages de membres.
