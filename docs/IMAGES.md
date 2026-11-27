# Lecture des images du canal

Le canal poste une partie de sa gestion en **images sans texte**, toujours les mêmes modèles :

| Image | Nombre (mai → nov.) | Action du bot |
|---|---|---|
| TP1 HIT | 188 | encaisse TP1 si encore ouvert et en profit, annule les ordres en attente |
| TP2 HIT | 10 | encaisse TP2 si en profit |
| SL HIT | 28 | ferme le reste, annule les ordres en attente |
| BE TOUCHÉ | 24 | ferme le reste |
| METTEZ VOUS BE !! | 55 | passe les positions au BE |
| +N PIPS, LET'S GO | ~150 | rien (info) |

Vérification sur les prix réels : quand « TP1 HIT » est posté, le prix avait bien atteint TP1
dans 173 cas sur 185 (les 12 autres : annonce légèrement en avance à cause du spread).

## Comment ça marche
Chaque image est réduite à une empreinte de 1024 bits (dHash 32×32) et comparée aux modèles de
`parser/image_templates.json` :
- même modèle (recompression Telegram) : ≤ 26 bits d'écart ;
- modèles différents (« TP1 HIT » vs « TP2 HIT ») : ≥ 81 bits ;
- image inconnue (capture d'écran, pub…) : ≥ 445 bits.

Seuil : 50 bits. Résultat sur les 467 images réelles : 467 bien classées.
Pas d'OCR, pas d'IA, pas d'internet : ~10 ms par image.

## Quand le canal change de design
Une image non reconnue ne déclenche **aucune action** ; elle est copiée dans
`logs/images_inconnues/`. Pour l'apprendre :
```
venv\Scripts\python.exe -m tools.add_image_template logs\images_inconnues\10920.jpg TP1_HIT
```
Labels : `TP1_HIT`, `TP2_HIT`, `TP3_HIT`…, `SL_HIT`, `BE_HIT`, `BE`, `INFO`. Puis relancer le bot.
