"""FusionMassAnalyzer - Autodesk Fusion add-in entry point."""

import traceback

import adsk.core

from .massanalyzer import command


def run(context):
    try:
        command.start()
    except Exception:
        adsk.core.Application.get().userInterface.messageBox(
            "FusionMassAnalyzer - échec du démarrage :\n" + traceback.format_exc())


def stop(context):
    try:
        command.stop()
    except Exception:
        adsk.core.Application.get().userInterface.messageBox(
            "FusionMassAnalyzer - échec de l'arrêt :\n" + traceback.format_exc())
