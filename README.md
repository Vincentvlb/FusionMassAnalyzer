# FusionMassAnalyzer

Add-in Autodesk Fusion (API Python) qui analyse la répartition des masses d'un assemblage :

- **masse totale** et masse cumulée de chaque sous-ensemble, sur toute la profondeur de l'arbre ;
- **sunburst** (camembert multi-niveaux) zoomable, avec un tableau trié par masse ;
- **carte de chaleur** : colore temporairement chaque pièce selon sa masse, puis **restaure** l'état d'origine ;
- **export CSV** de la hiérarchie et des masses.

> 📸 *Captures à faire* — voir [la liste en bas de page](#captures-à-faire).

## Fonctionnalités

| | |
|---|---|
| Bouton | **Utilitaires › Compléments › Analyse de masse** (bouton mis en avant) et **Inspecter › Analyse de masse**. Ouvre une palette ancrée à droite. |
| Parcours | `rootComponent` → `occurrences` → `childOccurrences`, sans limite de profondeur. Chaque instance d'un composant est un nœud distinct (les masses sont mises en cache par corps natif : 40 vis identiques = 1 seul calcul de `physicalProperties`). |
| Masses | Masse propre (corps directement dans le composant) et masse cumulée (propre + sous-ensembles), en kg via `physicalProperties.mass`. Affichage en **kg** au-delà de 1 kg, en **g** en dessous. |
| Masqués | Case **Ignorer les éléments masqués** : saute les occurrences et corps non visibles (et leurs sous-arbres). |
| Alertes | Corps / composants **sans matériau**, à **masse nulle**, corps **surfaciques** ou **erreur de calcul** : marqués ⚠ dans le tableau et listés sous celui-ci (clic = sélection). |
| Sunburst | D3 v7 embarqué (`palette/vendor`, aucun CDN). Clic sur une part : zoom sur le sous-ensemble **et** sélection dans Fusion. Clic au centre : niveau parent. Fil d'Ariane cliquable. |
| Tableau | Nom, chemin, masse cumulée, % du total. Trié par masse (en-têtes cliquables), filtrable, limité au sous-ensemble zoomé. Clic sur une ligne → sélection de l'occurrence (ou du corps) dans Fusion. |
| Carte de chaleur | **Colorer par masse** : rouge = plus lourd → orange → jaune → bleu → gris-bleu = plus léger, 10 paliers, **échelle log** en option. |
| Restaurer | Remet exactement les apparences d'origine (`occurrence.appearance` et `body.appearance`, surcharge ou non), puis supprime les apparences `MassHeat_` devenues inutilisées. |
| CSV | Niveau, type, nom (indenté), chemin, masse propre, masse cumulée, %, alertes. Format `;` + virgule décimale (Excel FR) ou `,` + point décimal. UTF-8 avec BOM. |

### Carte de chaleur : détails

- Pièces coloriées : chaque **occurrence feuille** (sans sous-occurrence) reçoit une surcharge d'apparence au niveau de l'occurrence (donc par instance). Les corps appartenant à la racine ou à un sous-ensemble qui contient aussi des sous-occurrences sont coloriés corps par corps.
- Les apparences `MassHeat_00` … `MassHeat_09` sont créées dans le design par copie d'une apparence de la *Fusion Appearance Library* (recherchée par son identifiant, indépendant de la langue), puis leur couleur est modifiée.
- L'état d'origine est mémorisé en session. Si Fusion a été fermé alors que les couleurs étaient appliquées, **Restaurer** retire en dernier recours toutes les surcharges qui pointent vers une apparence `MassHeat_` et supprime ces apparences.
- Pensez à **restaurer avant d'enregistrer** le document si vous ne voulez pas conserver les couleurs. Les surcharges d'apparence de **faces** ne sont pas modifiées et restent prioritaires à l'affichage.

## Installation

1. Téléchargez le dépôt (**Code › Download ZIP**, puis décompressez) ou clonez-le :
   ```bash
   git clone https://github.com/vincentvlb/FusionMassAnalyzer.git
   ```
   Le dossier doit s'appeler `FusionMassAnalyzer` (même nom que le `.py` et le `.manifest`).
2. Dans Fusion : **Utilitaires › Compléments › Scripts et compléments** (raccourci `Maj+S`).
3. Cliquez sur **+** (à côté de la recherche) › **Script ou complément depuis l'appareil**, puis choisissez le dossier `FusionMassAnalyzer`.
4. Dans la liste, lancez **FusionMassAnalyzer** (cochez *Exécuter au démarrage* si vous voulez qu'il se charge à chaque démarrage).
5. Ouvrez un assemblage et cliquez sur **Utilitaires › Compléments › Analyse de masse**.

Le complément s'arrête depuis la même boîte de dialogue ; à l'arrêt, une carte de chaleur encore active est restaurée automatiquement.

## Structure

```
FusionMassAnalyzer.py         point d'entrée run/stop
FusionMassAnalyzer.manifest   manifeste de l'add-in
massanalyzer/
  mass_tree.py                parcours + agrégation des masses, CSV, formats (Python pur)
  heat.py                     cibles, échelle lin/log, paliers de couleur (Python pur)
  heatmap.py                  apparences MassHeat_, sauvegarde/restauration (API Fusion)
  command.py                  bouton, palette, messages palette <-> Python (API Fusion)
palette/                      index.html, palette.js, palette.css, vendor/d3.min.js, demo.js
resources/                    icônes du bouton
tests/                        tests unitaires sur une structure Fusion mockée
tools/make_demo.py            régénère palette/demo.js
```

## Développement

L'add-in n'a **aucune dépendance** à l'exécution : il tourne dans le Python embarqué de Fusion. [uv](https://docs.astral.sh/uv/) gère uniquement l'environnement de développement (pytest, `uv.lock`, Python 3.12 comme Fusion).

```bash
uv sync
```

```bash
uv run pytest
```

Les modules `mass_tree` et `heat` n'importent pas `adsk` : ils sont testés hors Fusion avec des objets qui imitent l'API (`tests/fusion_mocks.py` — collections `count`/`item`, proxys de corps, instances multiples, éléments masqués, corps sans matériau…).

Prévisualiser la palette dans un navigateur (données de démo, sans Fusion) :

```bash
python3 -m http.server 8765 -d palette
```

puis ouvrez <http://localhost:8765>. Après une modification de la logique ou des mocks :

```bash
uv run python -m tools.make_demo
```

Dans Fusion, les messages d'erreur du complément s'affichent dans la **Console de texte** (Utilitaires › Compléments › Afficher la console de texte).

## Captures à faire

À placer dans `docs/screenshots/` puis à référencer ici :

- [ ] `toolbar.png` — le bouton *Analyse de masse* dans Utilitaires › Compléments
- [ ] `palette.png` — la palette : masse totale, sunburst et tableau sur un vrai assemblage
- [ ] `zoom.png` — le sunburst zoomé sur un sous-ensemble, occurrence sélectionnée dans le navigateur
- [ ] `heatmap.png` — le modèle coloré par masse, avec la légende
- [ ] `install.png` — la boîte *Scripts et compléments* avec le bouton **+**

## Licence

[MIT](LICENSE). D3 est distribué sous licence ISC ([palette/vendor/D3-LICENSE](palette/vendor/D3-LICENSE)).
