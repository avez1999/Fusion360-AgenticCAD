import json
import math
import threading
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from queue import Queue
from uuid import uuid4

import adsk.core
import adsk.fusion

# ======================
# Config
# ======================
HOST = "127.0.0.1"
PORT = 18080

# IMPORTANT: set this to the SAME value as FUSION_BRIDGE_TOKEN in your .env
AUTH_TOKEN = "avez_fusion_2026_!9xQ3p"

CUSTOM_EVENT_ID = "FusionBridgeExecEvent"

# IMPORTANT: do NOT call adsk.core.Application.get() at import time
app = None
ui = None

_server = None
_server_thread = None

_task_queue = Queue()
_task_results = {}

_custom_event = None
_handlers = []


def _json_response(handler, code, obj):
    data = json.dumps(obj).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _auth_ok(handler):
    return handler.headers.get("X-Token") == AUTH_TOKEN


class _ExecEventHandler(adsk.core.CustomEventHandler):
    def notify(self, args: adsk.core.CustomEventArgs):
        try:
            while not _task_queue.empty():
                task_id, fn, payload = _task_queue.get_nowait()
                try:
                    out = fn(payload)
                    _task_results[task_id]["result"] = {"ok": True, "result": out}
                except Exception as e:
                    _task_results[task_id]["result"] = {
                        "ok": False,
                        "error": str(e),
                        "trace": traceback.format_exc(),
                    }
                finally:
                    _task_results[task_id]["event"].set()
        except Exception:
            try:
                if ui:
                    ui.messageBox("FusionBridge custom event error:\n" + traceback.format_exc())
            except:
                pass


def _run_on_fusion_thread(fn, payload):
    task_id = str(uuid4())
    ev = threading.Event()
    _task_results[task_id] = {"event": ev, "result": None}

    _task_queue.put((task_id, fn, payload))
    app.fireCustomEvent(CUSTOM_EVENT_ID)

    ev.wait(timeout=30.0)
    res = _task_results[task_id]["result"]
    del _task_results[task_id]
    if res is None:
        return {"ok": False, "error": "Timeout waiting for Fusion execution."}
    return res


# ======================
# Tools
# ======================

def _ping(_payload=None):
    return {"message": "pong"}


def _get_state(_payload=None):
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        return {"design": None, "note": "No active Fusion design."}

    root = design.rootComponent

    params = []
    try:
        for p in design.allParameters:
            params.append({
                "name": p.name,
                "expression": p.expression,
                "value": p.value,
                "unit": p.unit
            })
    except Exception:
        pass

    bodies = []
    try:
        for b in root.bRepBodies:
            bodies.append({
                "name": b.name,
                "isSolid": b.isSolid,
                "isVisible": b.isVisible
            })
    except Exception:
        pass

    timeline = []
    try:
        tl = design.timeline
        for i in range(tl.count):
            tli = tl.item(i)
            ent = tli.entity
            timeline.append({
                "index": i,
                "name": tli.name,
                "entityType": ent.entityType if ent else None
            })
    except Exception:
        pass

    units_mgr = design.unitsManager
    default_len_units = units_mgr.defaultLengthUnits if units_mgr else None

    return {
        "designName": design.parentDocument.name,
        "defaultLengthUnits": default_len_units,
        "parameters": params,
        "bodies": bodies,
        "timeline": timeline
    }


def _list_bodies(_payload=None):
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        raise Exception("No active Fusion design.")
    root = design.rootComponent

    out = []
    for i in range(root.bRepBodies.count):
        b = root.bRepBodies.item(i)
        bb = b.boundingBox
        out.append({
            "index": i,
            "name": b.name,
            "isSolid": b.isSolid,
            "isVisible": b.isVisible,
            "bbox": {
                "min": [bb.minPoint.x, bb.minPoint.y, bb.minPoint.z],
                "max": [bb.maxPoint.x, bb.maxPoint.y, bb.maxPoint.z],
            }
        })
    return {"bodies": out}


def _get_last_body(_payload=None):
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        raise Exception("No active Fusion design.")
    root = design.rootComponent
    if root.bRepBodies.count < 1:
        raise Exception("No bodies found.")
    b = root.bRepBodies.item(root.bRepBodies.count - 1)
    return {"bodyIndex": root.bRepBodies.count - 1, "bodyName": b.name}


def _create_sketch_on_plane(payload):
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        raise Exception("No active Fusion design.")
    root = design.rootComponent

    plane = str(payload.get("plane", "XY")).upper()
    if plane == "XY":
        cp = root.xYConstructionPlane
    elif plane == "XZ":
        cp = root.xZConstructionPlane
    elif plane == "YZ":
        cp = root.yZConstructionPlane
    else:
        raise Exception("plane must be XY, XZ, or YZ")

    sk = root.sketches.add(cp)
    return {"sketchName": sk.name}


def _create_sketch_rect_xy(payload):
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        raise Exception("No active Fusion design.")
    root = design.rootComponent

    x_mm = float(payload.get("x_mm", 40.0))
    y_mm = float(payload.get("y_mm", 30.0))

    um = design.unitsManager
    x = um.convert(x_mm, "mm", um.internalUnits)
    y = um.convert(y_mm, "mm", um.internalUnits)

    sk = root.sketches.add(root.xYConstructionPlane)
    lines = sk.sketchCurves.sketchLines

    p0 = adsk.core.Point3D.create(0, 0, 0)
    p1 = adsk.core.Point3D.create(x, 0, 0)
    p2 = adsk.core.Point3D.create(x, y, 0)
    p3 = adsk.core.Point3D.create(0, y, 0)

    lines.addByTwoPoints(p0, p1)
    lines.addByTwoPoints(p1, p2)
    lines.addByTwoPoints(p2, p3)
    lines.addByTwoPoints(p3, p0)

    return {"sketchName": sk.name, "profilesCount": sk.profiles.count}


def _create_sketch_circle_xy(payload):
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        raise Exception("No active Fusion design.")
    root = design.rootComponent
    um = design.unitsManager

    r_mm = float(payload.get("r_mm", 10.0))
    cx_mm = float(payload.get("cx_mm", 0.0))
    cy_mm = float(payload.get("cy_mm", 0.0))

    r = um.convert(r_mm, "mm", um.internalUnits)
    cx = um.convert(cx_mm, "mm", um.internalUnits)
    cy = um.convert(cy_mm, "mm", um.internalUnits)

    sk = root.sketches.add(root.xYConstructionPlane)
    circles = sk.sketchCurves.sketchCircles
    circles.addByCenterRadius(adsk.core.Point3D.create(cx, cy, 0), r)

    return {"sketchName": sk.name, "profilesCount": sk.profiles.count}


def _create_sketch_two_circles_xy(payload):
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        raise Exception("No active Fusion design.")
    root = design.rootComponent
    um = design.unitsManager

    od_mm = float(payload.get("od_mm"))
    id_mm = float(payload.get("id_mm", 0.0))

    od_r = um.convert(od_mm / 2.0, "mm", um.internalUnits)
    id_r = um.convert(id_mm / 2.0, "mm", um.internalUnits) if id_mm > 0 else 0

    sk = root.sketches.add(root.xYConstructionPlane)
    circles = sk.sketchCurves.sketchCircles
    circles.addByCenterRadius(adsk.core.Point3D.create(0, 0, 0), od_r)
    if id_mm > 0:
        circles.addByCenterRadius(adsk.core.Point3D.create(0, 0, 0), id_r)

    return {"sketchName": sk.name, "profilesCount": sk.profiles.count}


def _create_sketch_on_last_body_top(_payload=None):
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        raise Exception("No active Fusion design.")
    root = design.rootComponent
    if root.bRepBodies.count < 1:
        raise Exception("No bodies found.")

    body = root.bRepBodies.item(root.bRepBodies.count - 1)

    # find top planar face by max Z centroid (bbox)
    top_face = None
    top_z = -1e99
    for f in body.faces:
        try:
            if f.geometry.surfaceType != adsk.core.SurfaceTypes.PlaneSurfaceType:
                continue
            bb = f.boundingBox
            zc = 0.5 * (bb.minPoint.z + bb.maxPoint.z)
            if zc > top_z:
                top_z = zc
                top_face = f
        except Exception:
            pass

    if not top_face:
        raise Exception("No planar top face found.")

    sk = root.sketches.add(top_face)
    return {"sketchName": sk.name}


def _sketch_center_rectangle(payload):
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        raise Exception("No active Fusion design.")
    root = design.rootComponent
    um = design.unitsManager

    w_mm = float(payload.get("w_mm", 40))
    h_mm = float(payload.get("h_mm", 30))
    cx_mm = float(payload.get("cx_mm", 0))
    cy_mm = float(payload.get("cy_mm", 0))

    w = um.convert(w_mm, "mm", um.internalUnits)
    h = um.convert(h_mm, "mm", um.internalUnits)
    cx = um.convert(cx_mm, "mm", um.internalUnits)
    cy = um.convert(cy_mm, "mm", um.internalUnits)

    if root.sketches.count < 1:
        raise Exception("No sketch available. Create a sketch first.")
    sk = root.sketches.item(root.sketches.count - 1)

    lines = sk.sketchCurves.sketchLines
    p0 = adsk.core.Point3D.create(cx - w / 2, cy - h / 2, 0)
    p1 = adsk.core.Point3D.create(cx + w / 2, cy - h / 2, 0)
    p2 = adsk.core.Point3D.create(cx + w / 2, cy + h / 2, 0)
    p3 = adsk.core.Point3D.create(cx - w / 2, cy + h / 2, 0)
    lines.addByTwoPoints(p0, p1)
    lines.addByTwoPoints(p1, p2)
    lines.addByTwoPoints(p2, p3)
    lines.addByTwoPoints(p3, p0)

    return {"profilesCount": sk.profiles.count}


def _sketch_two_circles_current(payload):
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        raise Exception("No active Fusion design.")
    root = design.rootComponent
    um = design.unitsManager
    if root.sketches.count < 1:
        raise Exception("No sketch available.")
    sk = root.sketches.item(root.sketches.count - 1)

    circles = sk.sketchCurves.sketchCircles
    od_mm = float(payload.get("od_mm"))
    id_mm = float(payload.get("id_mm", 0.0))

    od_r = um.convert(od_mm / 2.0, "mm", um.internalUnits)
    circles.addByCenterRadius(adsk.core.Point3D.create(0, 0, 0), od_r)

    if id_mm and id_mm > 0:
        id_r = um.convert(id_mm / 2.0, "mm", um.internalUnits)
        circles.addByCenterRadius(adsk.core.Point3D.create(0, 0, 0), id_r)

    return {"profilesCount": sk.profiles.count}


def _sketch_line(payload):
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        raise Exception("No active Fusion design.")
    root = design.rootComponent
    um = design.unitsManager

    x1 = um.convert(float(payload.get("x1_mm", 0)), "mm", um.internalUnits)
    y1 = um.convert(float(payload.get("y1_mm", 0)), "mm", um.internalUnits)
    x2 = um.convert(float(payload.get("x2_mm", 10)), "mm", um.internalUnits)
    y2 = um.convert(float(payload.get("y2_mm", 0)), "mm", um.internalUnits)

    if root.sketches.count < 1:
        raise Exception("No sketch available.")
    sk = root.sketches.item(root.sketches.count - 1)

    sk.sketchCurves.sketchLines.addByTwoPoints(
        adsk.core.Point3D.create(x1, y1, 0),
        adsk.core.Point3D.create(x2, y2, 0),
    )
    return {"profilesCount": sk.profiles.count}


def _extrude_last_profile(payload):
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        raise Exception("No active Fusion design.")
    root = design.rootComponent

    distance_mm = float(payload.get("distance_mm", 5.0))
    operation = payload.get("operation", "newBody")

    if root.sketches.count < 1:
        raise Exception("No sketches found to extrude.")
    sk = root.sketches.item(root.sketches.count - 1)

    if sk.profiles.count < 1:
        raise Exception("Sketch has no profiles to extrude.")
    prof = sk.profiles.item(0)

    um = design.unitsManager
    dist = um.convert(distance_mm, "mm", um.internalUnits)

    extrudes = root.features.extrudeFeatures
    ext_input = extrudes.createInput(prof, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)

    opmap = {
        "newBody": adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
        "join": adsk.fusion.FeatureOperations.JoinFeatureOperation,
        "cut": adsk.fusion.FeatureOperations.CutFeatureOperation,
        "intersect": adsk.fusion.FeatureOperations.IntersectFeatureOperation,
    }
    ext_input.operation = opmap.get(operation, opmap["newBody"])
    ext_input.setDistanceExtent(False, adsk.core.ValueInput.createByReal(dist))

    ext = extrudes.add(ext_input)
    return {"extrudeFeatureName": ext.name, "bodiesCount": root.bRepBodies.count}


def _extrude_profile(payload):
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        raise Exception("No active Fusion design.")
    root = design.rootComponent
    um = design.unitsManager

    sketch_index_from_end = int(payload.get("sketch_index_from_end", 1))
    profile_index = int(payload.get("profile_index", 0))
    distance_mm = float(payload.get("distance_mm", 5.0))
    operation = payload.get("operation", "newBody")

    idx = root.sketches.count - sketch_index_from_end
    if idx < 0 or idx >= root.sketches.count:
        raise Exception("Invalid sketch_index_from_end.")

    sk = root.sketches.item(idx)
    if profile_index < 0 or profile_index >= sk.profiles.count:
        raise Exception("Invalid profile_index.")
    prof = sk.profiles.item(profile_index)

    dist = um.convert(distance_mm, "mm", um.internalUnits)

    extrudes = root.features.extrudeFeatures
    ext_input = extrudes.createInput(prof, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)

    opmap = {
        "newBody": adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
        "join": adsk.fusion.FeatureOperations.JoinFeatureOperation,
        "cut": adsk.fusion.FeatureOperations.CutFeatureOperation,
        "intersect": adsk.fusion.FeatureOperations.IntersectFeatureOperation,
    }
    ext_input.operation = opmap.get(operation, opmap["newBody"])
    ext_input.setDistanceExtent(False, adsk.core.ValueInput.createByReal(dist))

    ext = extrudes.add(ext_input)
    return {"extrudeFeatureName": ext.name, "bodiesCount": root.bRepBodies.count}


def _revolve_profile(payload):
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        raise Exception("No active Fusion design.")
    root = design.rootComponent

    sketch_index_from_end = int(payload.get("sketch_index_from_end", 1))
    profile_index = int(payload.get("profile_index", 0))
    axis_line_index = int(payload.get("axis_line_index", 0))
    angle_deg = float(payload.get("angle_deg", 360.0))

    idx = root.sketches.count - sketch_index_from_end
    if idx < 0:
        raise Exception("Invalid sketch_index_from_end.")
    sk = root.sketches.item(idx)

    if sk.profiles.count <= profile_index:
        raise Exception("Invalid profile_index.")
    prof = sk.profiles.item(profile_index)

    if sk.sketchCurves.sketchLines.count <= axis_line_index:
        raise Exception("Invalid axis_line_index.")
    axis_line = sk.sketchCurves.sketchLines.item(axis_line_index)

    revol = root.features.revolveFeatures
    rev_in = revol.createInput(prof, axis_line, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    rev_in.setAngleExtent(False, adsk.core.ValueInput.createByString(f"{angle_deg} deg"))
    feat = revol.add(rev_in)
    return {"revolveFeatureName": feat.name, "bodiesCount": root.bRepBodies.count}


def _hole_on_last_body_top_face(payload):
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        raise Exception("No active Fusion design.")
    root = design.rootComponent
    um = design.unitsManager

    if root.bRepBodies.count < 1:
        raise Exception("No bodies found.")

    dia_mm = float(payload.get("dia_mm", 5.0))
    depth_mm = float(payload.get("depth_mm", 10.0))
    x_mm = float(payload.get("x_mm", 0.0))
    y_mm = float(payload.get("y_mm", 0.0))

    dia = um.convert(dia_mm, "mm", um.internalUnits)
    depth = um.convert(depth_mm, "mm", um.internalUnits)
    x = um.convert(x_mm, "mm", um.internalUnits)
    y = um.convert(y_mm, "mm", um.internalUnits)

    body = root.bRepBodies.item(root.bRepBodies.count - 1)

    top_face = None
    top_z = -1e99
    for f in body.faces:
        try:
            if f.geometry.surfaceType != adsk.core.SurfaceTypes.PlaneSurfaceType:
                continue
            bb = f.boundingBox
            zc = 0.5 * (bb.minPoint.z + bb.maxPoint.z)
            if zc > top_z:
                top_z = zc
                top_face = f
        except Exception:
            pass

    if not top_face:
        raise Exception("No planar top face found.")

    sk = root.sketches.add(top_face)
    pt = sk.sketchPoints.add(adsk.core.Point3D.create(x, y, 0))

    holes = root.features.holeFeatures
    hole_input = holes.createSimpleInput(adsk.core.ValueInput.createByReal(dia))
    hole_input.setPositionBySketchPoint(pt)
    hole_input.setDistanceExtent(adsk.core.ValueInput.createByReal(depth))
    hole = holes.add(hole_input)

    return {"holeFeatureName": hole.name}


def _hole_bolt_circle_one(payload):
    bcd_mm = float(payload.get("bcd_mm"))
    hole_dia_mm = float(payload.get("hole_dia_mm"))
    depth_mm = float(payload.get("depth_mm", 9999.0))
    angle_deg = float(payload.get("angle_deg", 0.0))

    r = bcd_mm / 2.0
    x = r * math.cos(math.radians(angle_deg))
    y = r * math.sin(math.radians(angle_deg))

    return _hole_on_last_body_top_face({
        "dia_mm": hole_dia_mm,
        "depth_mm": depth_mm,
        "x_mm": x,
        "y_mm": y
    })


def _circular_pattern_last_feature(payload):
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        raise Exception("No active Fusion design.")
    root = design.rootComponent

    qty = int(payload.get("qty", 6))
    angle = str(payload.get("angle", "360 deg"))

    tl = design.timeline
    if tl.count < 1:
        raise Exception("No timeline features to pattern.")
    ent = tl.item(tl.count - 1).entity
    if not ent:
        raise Exception("Last timeline entity not found.")

    objs = adsk.core.ObjectCollection.create()
    objs.add(ent)

    axis = root.zConstructionAxis

    pats = root.features.circularPatternFeatures
    pat_in = pats.createInput(objs, axis)
    pat_in.quantity = adsk.core.ValueInput.createByReal(qty)
    pat_in.totalAngle = adsk.core.ValueInput.createByString(angle)
    pat = pats.add(pat_in)

    return {"patternFeatureName": pat.name}


def _countersink_hole_on_last_body_top_face(payload):
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        raise Exception("No active Fusion design.")
    root = design.rootComponent
    um = design.unitsManager

    if root.bRepBodies.count < 1:
        raise Exception("No bodies found.")

    hole_dia_mm = float(payload.get("hole_dia_mm", 6.0))
    cs_dia_mm = float(payload.get("cs_dia_mm", 10.0))
    cs_angle_deg = float(payload.get("cs_angle_deg", 82.0))
    depth_mm = float(payload.get("depth_mm", 9999.0))
    x_mm = float(payload.get("x_mm", 0.0))
    y_mm = float(payload.get("y_mm", 0.0))

    hole_d = um.convert(hole_dia_mm, "mm", um.internalUnits)
    cs_d = um.convert(cs_dia_mm, "mm", um.internalUnits)
    depth = um.convert(depth_mm, "mm", um.internalUnits)
    x = um.convert(x_mm, "mm", um.internalUnits)
    y = um.convert(y_mm, "mm", um.internalUnits)

    body = root.bRepBodies.item(root.bRepBodies.count - 1)

    top_face = None
    top_z = -1e99
    for f in body.faces:
        try:
            if f.geometry.surfaceType != adsk.core.SurfaceTypes.PlaneSurfaceType:
                continue
            bb = f.boundingBox
            zc = 0.5 * (bb.minPoint.z + bb.maxPoint.z)
            if zc > top_z:
                top_z, top_face = zc, f
        except Exception:
            pass
    if not top_face:
        raise Exception("No planar top face found.")

    sk = root.sketches.add(top_face)
    pt = sk.sketchPoints.add(adsk.core.Point3D.create(x, y, 0))

    holes = root.features.holeFeatures
    hole_input = holes.createSimpleInput(adsk.core.ValueInput.createByReal(hole_d))
    hole_input.setPositionBySketchPoint(pt)
    hole_input.setDistanceExtent(adsk.core.ValueInput.createByReal(depth))

    # Countersink
    hole_input.tipAngle = adsk.core.ValueInput.createByString(f"{cs_angle_deg} deg")
    hole_input.isCountersink = True
    hole_input.countersinkDiameter = adsk.core.ValueInput.createByReal(cs_d)

    hole_feat = holes.add(hole_input)
    return {"holeFeatureName": hole_feat.name}

# ======================
# NEW: cleanup / utility
# ======================

def _delete_all_bodies(_payload=None):
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        raise Exception("No active Fusion design.")
    root = design.rootComponent

    # Copy list first, then delete
    bodies = [root.bRepBodies.item(i) for i in range(root.bRepBodies.count)]
    deleted = 0
    for b in bodies:
        try:
            b.deleteMe()
            deleted += 1
        except:
            pass
    return {"deletedBodies": deleted, "remainingBodies": root.bRepBodies.count}


def _combine_all_bodies(payload):
    """Join all bodies into one (target = first body)."""
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        raise Exception("No active Fusion design.")
    root = design.rootComponent

    if root.bRepBodies.count < 2:
        return {"note": "Need >=2 bodies to combine.", "bodiesCount": root.bRepBodies.count}

    op = str(payload.get("operation", "join")).lower()  # join|cut|intersect
    target = root.bRepBodies.item(0)

    tools = adsk.core.ObjectCollection.create()
    for i in range(1, root.bRepBodies.count):
        tools.add(root.bRepBodies.item(i))

    cmb = root.features.combineFeatures
    cmb_in = cmb.createInput(target, tools)

    if op == "cut":
        cmb_in.operation = adsk.fusion.FeatureOperations.CutFeatureOperation
        cmb_in.isKeepToolBodies = False
    elif op == "intersect":
        cmb_in.operation = adsk.fusion.FeatureOperations.IntersectFeatureOperation
        cmb_in.isKeepToolBodies = False
    else:
        cmb_in.operation = adsk.fusion.FeatureOperations.JoinFeatureOperation
        cmb_in.isKeepToolBodies = False

    feat = cmb.add(cmb_in)
    return {"combineFeatureName": feat.name, "bodiesCount": root.bRepBodies.count}


def _circular_pattern_last_body(payload):
    """Pattern the LAST body around Z axis (not last feature)."""
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        raise Exception("No active Fusion design.")
    root = design.rootComponent

    qty = int(payload.get("qty", 6))
    total_angle = str(payload.get("angle", "360 deg"))

    if root.bRepBodies.count < 1:
        raise Exception("No bodies found to pattern.")

    body = root.bRepBodies.item(root.bRepBodies.count - 1)

    objs = adsk.core.ObjectCollection.create()
    objs.add(body)

    pats = root.features.circularPatternFeatures
    pat_in = pats.createInput(objs, root.zConstructionAxis)
    pat_in.quantity = adsk.core.ValueInput.createByReal(qty)
    pat_in.totalAngle = adsk.core.ValueInput.createByString(total_angle)
    pat = pats.add(pat_in)

    return {"patternFeatureName": pat.name, "bodiesCount": root.bRepBodies.count}


# ======================
# NEW: assembly helpers
# ======================

def _component_from_last_body(payload):
    """Create a new component and move the last body into it."""
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        raise Exception("No active Fusion design.")
    root = design.rootComponent

    name = str(payload.get("name", "Comp"))
    if root.bRepBodies.count < 1:
        raise Exception("No bodies to move into component.")

    body = root.bRepBodies.item(root.bRepBodies.count - 1)

    occ = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    comp = occ.component
    comp.name = name

    body.moveToComponent(occ)
    return {"componentName": comp.name, "occurrenceName": occ.name}


def _rigid_joint_last_two_components(_payload=None):
    """Create an As-Built rigid joint between last two occurrences in root."""
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        raise Exception("No active Fusion design.")
    root = design.rootComponent

    if root.occurrences.count < 2:
        raise Exception("Need at least 2 occurrences/components.")

    occA = root.occurrences.item(root.occurrences.count - 2)
    occB = root.occurrences.item(root.occurrences.count - 1)

    joints = root.asBuiltJoints
    ji = joints.createInput(occA, occB, adsk.fusion.JointTypes.RigidJointType)
    j = joints.add(ji)
    return {"asBuiltJointName": j.name, "type": "rigid", "A": occA.name, "B": occB.name}


# ======================
# NEW: gear helpers
# ======================

def _polar_point(r, ang):
    return adsk.core.Point3D.create(r * math.cos(ang), r * math.sin(ang), 0)


def _involute_points(base_r, r_end, rotation, n=20):
    if r_end <= base_r:
        return None
    t_end = math.sqrt((r_end / base_r) ** 2 - 1.0)
    pts = adsk.core.ObjectCollection.create()
    for i in range(n + 1):
        t = t_end * (i / n)
        r = base_r * math.sqrt(1 + t * t)
        theta = (t - math.atan(t)) + rotation
        pts.add(_polar_point(r, theta))
    return pts


def _create_spur_gear_involute(payload):
    """
    Approx involute spur gear:
      - sketch one tooth (two involutes + arcs)
      - extrude tooth (new body)
      - circular pattern BODY
      - combine all
      - optional bore cut
    """
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        raise Exception("No active Fusion design.")
    root = design.rootComponent
    um = design.unitsManager

    z = int(payload.get("teeth", 24))
    m = float(payload.get("module_mm", 2.0))
    pa_deg = float(payload.get("pressure_angle_deg", 20.0))
    thickness_mm = float(payload.get("thickness_mm", 10.0))
    bore_mm = float(payload.get("bore_mm", 0.0))
    backlash_mm = float(payload.get("backlash_mm", 0.0))

    if z < 6:
        raise Exception("teeth must be >= 6")
    if m <= 0:
        raise Exception("module_mm must be > 0")

    pa = math.radians(pa_deg)
    pitch_d = m * z
    pitch_r = pitch_d / 2.0
    base_r = pitch_r * math.cos(pa)

    addendum = 1.0 * m
    dedendum = 1.25 * m
    outer_r = pitch_r + addendum
    root_r = max(0.1, pitch_r - dedendum)

    circular_pitch = math.pi * m
    tooth_thick = (circular_pitch / 2.0) - (backlash_mm / 2.0)
    half_thick_angle = (tooth_thick / 2.0) / pitch_r

    # involute at pitch
    t_p = math.sqrt((pitch_r / base_r) ** 2 - 1.0)
    theta_p = (t_p - math.atan(t_p))
    rot = half_thick_angle - theta_p

    sk = root.sketches.add(root.xYConstructionPlane)
    curves = sk.sketchCurves

    ptsA = _involute_points(base_r, outer_r, rot, n=20)
    if not ptsA or ptsA.count < 2:
        raise Exception("Failed involute build.")
    curves.sketchFittedSplines.add(ptsA)

    ptsB = adsk.core.ObjectCollection.create()
    for i in range(ptsA.count):
        p = ptsA.item(i)
        ptsB.add(adsk.core.Point3D.create(p.x, -p.y, 0))
    curves.sketchFittedSplines.add(ptsB)

    pA_out = ptsA.item(ptsA.count - 1)
    pB_out = ptsB.item(ptsB.count - 1)
    pA_in = ptsA.item(0)
    pB_in = ptsB.item(0)

    # Outer arc (approx)
    curves.sketchArcs.addByThreePoints(pA_out, _polar_point(outer_r, rot), pB_out)
    # Root arc (approx)
    curves.sketchArcs.addByThreePoints(pA_in, _polar_point(root_r, rot), pB_in)

    if sk.profiles.count < 1:
        raise Exception("No closed tooth profile created.")

    # Extrude tooth
    dist = um.convert(thickness_mm, "mm", um.internalUnits)
    extrudes = root.features.extrudeFeatures
    ext_in = extrudes.createInput(sk.profiles.item(0), adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    ext_in.setDistanceExtent(False, adsk.core.ValueInput.createByReal(dist))
    tooth_ext = extrudes.add(ext_in)

    tooth_body = tooth_ext.bodies.item(0)

    # Pattern tooth body
    objs = adsk.core.ObjectCollection.create()
    objs.add(tooth_body)

    pats = root.features.circularPatternFeatures
    pat_in = pats.createInput(objs, root.zConstructionAxis)
    pat_in.quantity = adsk.core.ValueInput.createByReal(z)
    pat_in.totalAngle = adsk.core.ValueInput.createByString("360 deg")
    pats.add(pat_in)

    # Combine all bodies
    _combine_all_bodies({"operation": "join"})

    # Bore cut
    
    if bore_mm and bore_mm > 0:
        sk2 = root.sketches.add(root.xYConstructionPlane)
        r_bore = um.convert(bore_mm / 2.0, "mm", um.internalUnits)
        sk2.sketchCurves.sketchCircles.addByCenterRadius(adsk.core.Point3D.create(0, 0, 0), r_bore)

        if sk2.profiles.count < 1:
            raise Exception("No bore profile.")
        cut_in = extrudes.createInput(sk2.profiles.item(0), adsk.fusion.FeatureOperations.CutFeatureOperation)
        cut_in.setDistanceExtent(False, adsk.core.ValueInput.createByString("1000 mm"))
        extrudes.add(cut_in)

    return {"gear": "spur_involute_approx", "teeth": z, "module_mm": m, "bodiesCount": root.bRepBodies.count}


def _create_rack_gear(payload):
    """
    Simple rack gear:
      - sketch a closed polyline rack profile (sawtooth top)
      - extrude
    """
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        raise Exception("No active Fusion design.")
    root = design.rootComponent
    um = design.unitsManager

    m = float(payload.get("module_mm", 2.0))
    teeth = int(payload.get("teeth", 12))
    thickness_mm = float(payload.get("thickness_mm", 10.0))
    pa_deg = float(payload.get("pressure_angle_deg", 20.0))

    if teeth < 2:
        raise Exception("rack teeth must be >= 2")

    circular_pitch = math.pi * m
    tooth_height = 2.25 * m
    half_pitch = circular_pitch / 2.0
    base_h = tooth_height * 0.6
    top_h = tooth_height

    sk = root.sketches.add(root.xYConstructionPlane)
    lines = sk.sketchCurves.sketchLines

    pts = []
    pts.append((0, 0))
    pts.append((teeth * circular_pitch, 0))
    pts.append((teeth * circular_pitch, base_h))

    for i in range(teeth, 0, -1):
        xL = (i - 1) * circular_pitch
        xM = xL + half_pitch
        pts.append((xM, top_h))
        pts.append((xL, base_h))

    pts.append((0, base_h))
    pts.append((0, 0))

    for i in range(len(pts) - 1):
        p1 = adsk.core.Point3D.create(um.convert(pts[i][0], "mm", um.internalUnits),
                                      um.convert(pts[i][1], "mm", um.internalUnits), 0)
        p2 = adsk.core.Point3D.create(um.convert(pts[i+1][0], "mm", um.internalUnits),
                                      um.convert(pts[i+1][1], "mm", um.internalUnits), 0)
        lines.addByTwoPoints(p1, p2)

    if sk.profiles.count < 1:
        raise Exception("No rack profile created.")

    extrudes = root.features.extrudeFeatures
    dist = um.convert(thickness_mm, "mm", um.internalUnits)
    ext_in = extrudes.createInput(sk.profiles.item(0), adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    ext_in.setDistanceExtent(False, adsk.core.ValueInput.createByReal(dist))
    ext = extrudes.add(ext_in)

    return {"gear": "rack_basic", "teeth": teeth, "module_mm": m, "bodiesCount": root.bRepBodies.count}


def _create_helical_gear(_payload):
    raise Exception("Helical gear not implemented yet (needs helix+sweep per tooth).")


def _create_internal_gear(_payload):
    raise Exception("Internal gear not implemented yet (needs ring blank + internal tooth cuts).")


def _create_bevel_gear(_payload):
    raise Exception("Bevel gear not implemented yet (needs conical geometry + lofted tooth).")



# ---- Tool registry (defined ONCE) ----
_TOOL_MAP = {
    "ping": _ping,
    "get_state": _get_state,
    "list_bodies": _list_bodies,
    "get_last_body": _get_last_body,

    "create_sketch_on_plane": _create_sketch_on_plane,
    "create_sketch_rect_xy": _create_sketch_rect_xy,
    "create_sketch_circle_xy": _create_sketch_circle_xy,
    "create_sketch_two_circles_xy": _create_sketch_two_circles_xy,
    "create_sketch_on_last_body_top": _create_sketch_on_last_body_top,
    "sketch_center_rectangle": _sketch_center_rectangle,
    "sketch_two_circles_current": _sketch_two_circles_current,
    "sketch_line": _sketch_line,

    "extrude_last_profile": _extrude_last_profile,
    "extrude_profile": _extrude_profile,
    "revolve_profile": _revolve_profile,

    "hole_on_last_body_top_face": _hole_on_last_body_top_face,
    "hole_bolt_circle_one": _hole_bolt_circle_one,
    "circular_pattern_last_feature": _circular_pattern_last_feature,
    "countersink_hole_on_last_body_top_face": _countersink_hole_on_last_body_top_face,
        # cleanup / utility
    "delete_all_bodies": _delete_all_bodies,
    "combine_all_bodies": _combine_all_bodies,
    "circular_pattern_last_body": _circular_pattern_last_body,

    # assembly
    "component_from_last_body": _component_from_last_body,
    "rigid_joint_last_two_components": _rigid_joint_last_two_components,

    # gears
    "create_spur_gear_involute": _create_spur_gear_involute,
    "create_rack_gear": _create_rack_gear,
    "create_helical_gear": _create_helical_gear,
    "create_internal_gear": _create_internal_gear,
    "create_bevel_gear": _create_bevel_gear
}


def _dispatch_tool(payload):
    tool = payload.get("tool")
    args = payload.get("args", {}) or {}
    if tool not in _TOOL_MAP:
        return {"ok": False, "error": f"Unknown tool '{tool}'.", "available": list(_TOOL_MAP.keys())}
    return _run_on_fusion_thread(_TOOL_MAP[tool], args)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not _auth_ok(self):
            return _json_response(self, 401, {"ok": False, "error": "unauthorized"})

        if self.path == "/state":
            res = _run_on_fusion_thread(_get_state, {})
            return _json_response(self, 200, res)

        if self.path == "/ping":
            return _json_response(self, 200, {"ok": True, "result": {"message": "pong"}})

        return _json_response(self, 404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if not _auth_ok(self):
            return _json_response(self, 401, {"ok": False, "error": "unauthorized"})

        if self.path != "/tool":
            return _json_response(self, 404, {"ok": False, "error": "not found"})

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            payload = json.loads(raw)
        except Exception:
            return _json_response(self, 400, {"ok": False, "error": "invalid json"})

        res = _dispatch_tool(payload)
        return _json_response(self, 200, res)

    def log_message(self, format, *args):
        return


def start():
    global app, ui, _custom_event, _server, _server_thread

    app = adsk.core.Application.get()
    if not app:
        raise RuntimeError("Fusion Application not available.")
    ui = app.userInterface

    _custom_event = app.registerCustomEvent(CUSTOM_EVENT_ID)
    on_exec = _ExecEventHandler()
    _custom_event.add(on_exec)
    _handlers.append(on_exec)

    _server = HTTPServer((HOST, PORT), _Handler)
    _server_thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _server_thread.start()

    try:
        ui.messageBox(f"FusionBridge HTTP running at http://{HOST}:{PORT}\nRequires header X-Token")
    except:
        pass


def stop():
    global _server, app
    try:
        if _server:
            _server.shutdown()
            _server.server_close()
            _server = None
    except Exception:
        pass

    try:
        if app:
            app.unregisterCustomEvent(CUSTOM_EVENT_ID)
    except Exception:
        pass
