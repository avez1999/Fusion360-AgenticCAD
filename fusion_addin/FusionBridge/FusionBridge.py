import traceback
import adsk.core

from . import bridge_server

def run(context):
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface if app else None
        if ui:
            ui.messageBox("FusionBridge: starting HTTP bridge...")
        bridge_server.start()  # starts 127.0.0.1:18080
        if ui:
            ui.messageBox("FusionBridge: HTTP bridge running on http://127.0.0.1:18080")
    except:
        try:
            app = adsk.core.Application.get()
            ui = app.userInterface if app else None
            if ui:
                ui.messageBox("FusionBridge FAILED:\n\n" + traceback.format_exc())
        except:
            pass

def stop(context):
    try:
        bridge_server.stop()
    except:
        pass
