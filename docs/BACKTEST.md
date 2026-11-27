# Backtest : ce que rapporte vraiment la copie du canal

`tools/backtest.py` rejoue chaque signal sur les **ticks réels** du courtier (MT5) : entrée comme
le bot, puis chaque message de gestion du canal appliqué à son heure exacte avec le vrai parser.

## Résultats (397 signaux, 06/05 → 23/11/2026, 0,01 lot par position)

| Réglage | Trades | Gagnants | Résultat | Pire creux |
|---|---|---|---|---|
| 1 position (TP1) | 355 | 69 % | **+52 $** | 111 $ |
| 2 positions (TP1 + TP2/OPEN) | 355 | 69 % | **−149 $** | 346 $ |

Autres variantes testées (1 position) :
- attendre 15/30/60 s avant d'entrer : pas d'amélioration nette (écarts aléatoires) ;
- ordre limite au meilleur bord de la zone : moitié moins de trades, ~+68 $, creux ~60 $,
  mais résultat irrégulier d'un mois à l'autre.

### Pourquoi la 2e position perd
Quand le SL est touché avant TP1 (~31 % des signaux), elle **double la perte**. Quand TP1 est
touché, elle passe au BE et rapporte peu en moyenne. Gain moyen TP1 ≈ 4 $, perte moyenne ≈ 7 $ :
le canal gagne souvent mais petit.

### Limites
- Basé sur la version **finale** (modifiée) des signaux.
- Les images de gestion ne sont pas rejouées (seulement les textes).
- Horizon 4 h, ordres en attente expirés après 30 min.

## Relancer le backtest
```
venv\Scripts\python.exe -m tools.export_channel --limit 4000        (bot arrêté)
venv\Scripts\python.exe -m tools.backtest --legs 1 2 --delay 0 30   (MT5 ouvert)
```
