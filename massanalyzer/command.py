"""Toolbar button, HTML palette and palette <-> Python message handling."""

import json
import os
import traceback

import adsk.core
import adsk.fusion

from . import heat, mass_tree
from .heatmap import Heatmap

CMD_ID = "FusionMassAnalyzer_Show"
CMD_NAME = "Analyse de masse"
CMD_TOOLTIP = "Masse totale, répartition par sous-ensemble (sunburst) et carte de chaleur des pièces."
PALETTE_ID = "FusionMassAnalyzer_Palette"
PALETTE_NAME = "Fusion Mass Analyzer"
WORKSPACE_ID = "FusionSolidEnvironment"
# Utilities > Add-ins (promoted) and Inspect
PANELS = (("SolidScriptsAddinsPanel", True), ("InspectPanel", False))

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESOURCES = os.path.join(ROOT_DIR, "resources")
PALETTE_HTML = os.path.join(ROOT_DIR, "palette", "index.html")

_handlers = []
_heatmap = Heatmap()
_state = {"result": None, "design": None}


def _app():
    return adsk.core.Application.get()


def _ui():
    return _app().userInterface


def log(msg, level=None):
    level = level or adsk.core.LogLevels.InfoLogLevel
    _app().log("[FusionMassAnalyzer] " + msg, level, adsk.core.LogTypes.ConsoleLogType)


# ---------------------------------------------------------------- lifecycle

def start():
    ui = _ui()
    cmd_def = ui.commandDefinitions.itemById(CMD_ID)
    if not cmd_def:
        cmd_def = ui.commandDefinitions.addButtonDefinition(CMD_ID, CMD_NAME, CMD_TOOLTIP, RESOURCES)
    handler = _CommandCreated()
    cmd_def.commandCreated.add(handler)
    _handlers.append(handler)

    workspace = ui.workspaces.itemById(WORKSPACE_ID)
    for panel_id, promoted in PANELS:
        panel = workspace.toolbarPanels.itemById(panel_id) if workspace else None
        panel = panel or ui.allToolbarPanels.itemById(panel_id)
        if panel and not panel.controls.itemById(CMD_ID):
            control = panel.controls.addCommand(cmd_def)
            control.isPromoted = promoted


def stop():
    ui = _ui()
    try:
        if _heatmap.active:
            _heatmap.restore()
    except Exception:
        log(traceback.format_exc(), adsk.core.LogLevels.ErrorLogLevel)
    palette = ui.palettes.itemById(PALETTE_ID)
    if palette:
        palette.deleteMe()
    for panel_id, _ in PANELS:
        panel = ui.allToolbarPanels.itemById(panel_id)
        control = panel.controls.itemById(CMD_ID) if panel else None
        if control:
            control.deleteMe()
    cmd_def = ui.commandDefinitions.itemById(CMD_ID)
    if cmd_def:
        cmd_def.deleteMe()
    _handlers.clear()


# ---------------------------------------------------------------- palette

def show_palette():
    ui = _ui()
    palette = ui.palettes.itemById(PALETTE_ID)
    if not palette:
        url = PALETTE_HTML.replace("\\", "/")  # same convention as Autodesk's template
        palette = ui.palettes.add(PALETTE_ID, PALETTE_NAME, url, True, True, True, 960, 760, True)
        # wire the HTML handler first: nothing below may prevent it
        incoming = _HtmlIncoming()
        palette.incomingFromHTML.add(incoming)
        _handlers.append(incoming)
        try:
            palette.dockingState = adsk.core.PaletteDockingStates.PaletteDockStateRight
        except Exception:
            log("Ancrage de la palette impossible : " + traceback.format_exc(),
                adsk.core.LogLevels.WarningLogLevel)
        # the page sends "ready" once loaded, which triggers the first analysis
    else:
        palette.isVisible = True
        palette.sendInfoToHTML("refresh", "{}")


def _active_design():
    design = adsk.fusion.Design.cast(_app().activeProduct)
    if not design:
        raise RuntimeError("Aucun design actif : ouvrez un document dans l'espace Conception.")
    return design


def _current_result():
    result = _state["result"]
    if result is None:
        raise RuntimeError("Lancez d'abord une analyse.")
    return result


def _analyze(data):
    design = _active_design()
    result = mass_tree.build_tree(design.rootComponent,
                                  ignore_hidden=bool(data.get("ignoreHidden")),
                                  include_bodies=True)
    _state["result"], _state["design"] = result, design
    payload = result.to_payload()
    payload["document"] = _app().activeDocument.name
    payload["heatmapActive"] = _heatmap.active
    return payload


def _select(data):
    result = _current_result()
    entity = result.entities.get(data.get("id"))
    sel = _ui().activeSelections
    sel.clear()
    if entity is None:
        return {"selected": False}
    if not entity.isValid:
        raise RuntimeError("Élément introuvable, le design a changé : relancez l'analyse.")
    sel.add(entity)
    return {"selected": True}


def _heatmap_apply(data):
    result = _current_result()
    colored = _heatmap.apply(_state["design"], result, log_scale=bool(data.get("logScale")))
    return {"colored": colored, "legend": heat.legend(), "heatmapActive": True}


def _heatmap_restore(_data):
    if _heatmap.active:
        n = _heatmap.restore()
        return {"restored": n, "heatmapActive": False}
    # nothing saved in this session: strip leftovers from a previous one
    n = _heatmap.strip_leftovers(_active_design())
    return {"restored": n, "leftovers": True, "heatmapActive": False}


def _export_csv(data):
    result = _current_result()
    ui = _ui()
    dlg = ui.createFileDialog()
    dlg.title = "Exporter les masses (CSV)"
    dlg.filter = "CSV (*.csv)"
    dlg.initialFilename = "%s_masses.csv" % result.root.name
    if dlg.showSave() != adsk.core.DialogResults.DialogOK:
        return {"cancelled": True}
    if data.get("format") == "intl":
        text = mass_tree.to_csv(result, delimiter=",", decimal=".")
    else:
        text = mass_tree.to_csv(result)
    with open(dlg.filename, "w", encoding="utf-8-sig", newline="") as f:
        f.write(text)
    return {"path": dlg.filename}


ACTIONS = {
    "ready": _analyze,
    "analyze": _analyze,
    "select": _select,
    "heatmap": _heatmap_apply,
    "restore": _heatmap_restore,
    "exportCsv": _export_csv,
}


# ---------------------------------------------------------------- handlers

class _CommandCreated(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            cmd = adsk.core.CommandCreatedEventArgs.cast(args).command
            cmd.isAutoExecute = True  # no dialog: just open the palette
            on_execute = _CommandExecute()
            cmd.execute.add(on_execute)
            _handlers.append(on_execute)
        except Exception:
            _ui().messageBox(traceback.format_exc())


class _CommandExecute(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            show_palette()
        except Exception:
            _ui().messageBox(traceback.format_exc())


class _HtmlIncoming(adsk.core.HTMLEventHandler):
    def notify(self, args):
        html_args = adsk.core.HTMLEventArgs.cast(args)
        action = html_args.action
        handler = ACTIONS.get(action)
        if handler is None:
            return  # e.g. internal "response" events
        try:
            data = json.loads(html_args.data) if html_args.data else {}
            reply = {"ok": True, "data": handler(data)}
        except Exception as exc:
            log(traceback.format_exc(), adsk.core.LogLevels.ErrorLogLevel)
            reply = {"ok": False, "error": str(exc)}
        html_args.returnData = json.dumps(reply)
