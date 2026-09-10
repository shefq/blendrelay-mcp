"""Validated changes with no Blender or model-provider dependency."""
import copy
import math
import re
from .model import DomainError, entities, validate, strict, number, wall_points, uid

OPERATIONS = {
    "opening.resize": ("width_m", "height_m", "sill_m", "anchor"),
    "wall.set_height": ("height_m",),
    "wall.move": ("dx_m", "dy_m"),
    "material.assign": ("role", "material_id"),
    "asset.set_parameters": ("width_m", "depth_m", "height_m", "drawers"),
    "asset.move": ("position_m", "rotation_rad"),
    "asset.add": ("family", "label", "room_id", "position_m", "width_m", "depth_m", "height_m", "drawers", "material_id"),
    "entity.delete": (),
    "roof.set_parameters": ("kind", "pitch_degrees", "overhang_m", "material_id"),
}


def propose(model, operations):
    if not isinstance(operations, list) or not 1 <= len(operations) <= 100:
        raise DomainError("INVALID_OPERATIONS", "Supply 1–100 operations")
    candidate = copy.deepcopy(model)
    touched = set()
    for op in operations:
        strict(op, ("kind", "target_id", "parameters"), ("kind", "target_id", "parameters"))
        kind, target, params = op["kind"], op["target_id"], op["parameters"]
        if kind not in OPERATIONS: raise DomainError("UNSUPPORTED_OPERATION", kind)
        strict(params, OPERATIONS[kind])
        idx = entities(candidate)
        obj = idx.get(target)
        if obj is None and kind not in ("asset.add", "roof.set_parameters"):
            raise DomainError("UNKNOWN_ENTITY", "Target no longer exists", [target])
        touched.add(target)
        if kind == "opening.resize":
            if obj not in candidate["openings"]: raise DomainError("WRONG_ENTITY_TYPE", "Select an opening")
            old_width = obj["width_m"]
            width = params.get("width_m", old_width)
            number(width, "width", 0.2, 20)
            anchor = params.get("anchor", obj.get("anchor", "centre_fixed"))
            if anchor not in ("centre_fixed", "start_fixed"): raise DomainError("INVALID_ANCHOR", "Use centre_fixed or start_fixed")
            if anchor == "centre_fixed": obj["offset_m"] += (old_width-width)/2
            obj.update(params); obj["width_m"] = width
            touched.add(obj["host_wall_id"])
            touched.update(e["id"] for e in candidate["windows"]+candidate["doors"] if e["opening_id"] == target)
        elif kind == "wall.set_height":
            if obj not in candidate["walls"]: raise DomainError("WRONG_ENTITY_TYPE", "Select a wall")
            strict(params, OPERATIONS[kind], ("height_m",))
            obj.update(params)
            touched.update(e["id"] for e in candidate["openings"] if e["host_wall_id"] == target)
        elif kind == "wall.move":
            if obj not in candidate["walls"]: raise DomainError("WRONG_ENTITY_TYPE", "Select a wall")
            dx, dy = number(params.get("dx_m", 0), "dx", -20, 20), number(params.get("dy_m", 0), "dy", -20, 20)
            edge = idx[obj["edge_id"]]
            moved = {edge["start_vertex_id"], edge["end_vertex_id"]}
            # Move shared vertices, never an independent wall mesh.
            for vid in moved:
                idx[vid]["xy_m"] = [idx[vid]["xy_m"][0]+dx, idx[vid]["xy_m"][1]+dy]
                touched.add(vid)
            changed_edges = {e["id"] for e in candidate["edges"] if moved.intersection((e["start_vertex_id"], e["end_vertex_id"]))}
            touched.update(changed_edges)
            changed_walls = {w["id"] for w in candidate["walls"] if w["edge_id"] in changed_edges}
            touched.update(changed_walls)
            touched.update(r["id"] for r in candidate["spaces"] if any(s["edge_id"] in changed_edges for s in r["boundary"]))
            touched.update(o["id"] for o in candidate["openings"] if o["host_wall_id"] in changed_walls)
            touched.update(e["id"] for e in candidate["windows"]+candidate["doors"] if e["opening_id"] in touched)
        elif kind == "material.assign":
            strict(params, OPERATIONS[kind], ("material_id",))
            if params["material_id"] not in {m["id"] for m in candidate["materials"]}: raise DomainError("UNKNOWN_MATERIAL", "Unknown material")
            if "materials" in obj:
                role = params.get("role", "frame")
                if role not in obj["materials"]: raise DomainError("UNKNOWN_PART", f"Available material roles: {list(obj['materials'])}")
                obj["materials"][role] = params["material_id"]
            elif "material_id" in obj: obj["material_id"] = params["material_id"]
            else: raise DomainError("WRONG_ENTITY_TYPE", "This entity has no material")
        elif kind == "asset.set_parameters":
            if obj not in candidate["assets"]: raise DomainError("WRONG_ENTITY_TYPE", "Select a procedural asset")
            obj["parameters"].update(params)
        elif kind == "asset.move":
            if obj not in candidate["assets"]: raise DomainError("WRONG_ENTITY_TYPE", "Select an asset")
            obj.update(params)
        elif kind == "asset.add":
            strict(params, OPERATIONS[kind], ("family", "position_m", "width_m", "depth_m", "height_m"))
            if target in idx: raise DomainError("DUPLICATE_ID", "Asset ID already exists")
            candidate["assets"].append({"id": target, "label": params.get("label", params["family"]),
                "family": params["family"], "room_id": params.get("room_id"), "position_m": params["position_m"], "rotation_rad": 0,
                "parameters": {k: params.get(k, 3) for k in ("width_m", "depth_m", "height_m", "drawers")},
                "material_id": params.get("material_id", "mat-wood")})
        elif kind == "entity.delete":
            if obj in candidate["assets"]: candidate["assets"].remove(obj)
            elif obj in candidate["windows"]+candidate["doors"]:
                touched.add(obj["opening_id"])
                opening = idx[obj["opening_id"]]; touched.add(opening["host_wall_id"])
                candidate["openings"].remove(opening)
                for group in ("windows", "doors"):
                    if obj in candidate[group]: candidate[group].remove(obj)
            else: raise DomainError("UNSUPPORTED_DELETE", "Delete supports furnishings, doors, and windows; topology deletion needs a new layout")
        elif kind == "roof.set_parameters":
            if target != "roof": raise DomainError("WRONG_ENTITY_TYPE", "Use target_id roof")
            roof = candidate["roof"]; roof.update(params)
            if roof["kind"] not in ("flat", "gable", "none"): raise DomainError("UNSUPPORTED_ROOF", "Choose flat, gable, or none")
            number(roof["pitch_degrees"], "roof pitch", 5, 55); number(roof["overhang_m"], "overhang", 0, 2)
    # Include dependencies in protection checks, not only direct targets.
    blocked = touched.intersection(model.get("protected_ids", []))
    if blocked: raise DomainError("PROTECTED_ENTITY", "Change affects protected elements", sorted(blocked))
    validate(candidate)
    before, after = entities(model), entities(candidate)
    changed = sorted(k for k in set(before)|set(after) if before.get(k) != after.get(k))
    touched.update(changed)
    # Protect generated dependants too (for example a roof following a wall).
    from .geometry import build_specs
    old_specs={(s['entity_id'],s['role']):s['hash'] for s in build_specs(model)}
    new_specs={(s['entity_id'],s['role']):s['hash'] for s in build_specs(candidate)}
    touched.update(k[0] for k in set(old_specs)|set(new_specs) if old_specs.get(k)!=new_specs.get(k))
    blocked=touched.intersection(model.get('protected_ids',[]))
    if blocked:raise DomainError('PROTECTED_ENTITY','Change affects protected generated dependencies',sorted(blocked))
    changes=[]
    def diff(eid,path,left,right):
        if left==right:return
        if isinstance(left,dict) and isinstance(right,dict):
            for k in sorted(set(left)|set(right)):diff(eid,(path+'.' if path else '')+k,left.get(k),right.get(k))
        else:changes.append({'entity_id':eid,'field':path or 'entity','before':left,'after':right})
    for eid in changed:diff(eid,'',before.get(eid),after.get(eid))
    if model.get('roof')!=candidate.get('roof'):diff('roof','',model.get('roof'),candidate.get('roof'))
    return candidate, sorted(touched), {"changed_entities": changed, "changes": changes, "operation_count": len(operations)}


def prompt_operations(model, text, selected):
    idx = entities(model)
    if len(selected) != 1 or selected[0] not in idx:
        raise DomainError("AMBIGUOUS_TARGET", "Select one wall, window, door, or procedural asset")
    target = selected[0]; obj = idx[target]
    lower = text.lower(); operations = []
    for colour in ("black", "white", "red", "blue", "green", "grey", "wood"):
        if re.search(rf"\b{colour}\b", lower):
            role = "frame" if obj in model["windows"]+model["doors"] else None
            p = {"material_id": f"mat-{colour}"}
            if role: p["role"] = role
            operations.append({"kind": "material.assign", "target_id": target, "parameters": p})
            break
    match = re.search(r"(\d+(?:\.\d+)?)\s*(cm|mm|m|metres?|meters?)\b", lower)
    if match:
        value = float(match[1]) * (0.01 if match[2] == "cm" else 0.001 if match[2] == "mm" else 1)
        width = any(s in lower for s in ("wide", "width", "narrow"))
        height = any(s in lower for s in ("tall", "height", "high", "short"))
        delta = any(s in lower for s in ("wider", "narrower", "taller", "shorter"))
        sign = -1 if any(s in lower for s in ("narrower", "shorter")) else 1
        key = "width_m" if width else "height_m"
        if width or height:
            if obj in model["windows"]+model["doors"]:
                opening = idx[obj["opening_id"]]
                operations.append({"kind": "opening.resize", "target_id": opening["id"], "parameters": {key: opening[key]+sign*value if delta else value}})
            elif obj in model["walls"] and height:
                operations.append({"kind": "wall.set_height", "target_id": target, "parameters": {key: obj[key]+sign*value if delta else value}})
            elif obj in model["assets"]:
                operations.append({"kind": "asset.set_parameters", "target_id": target, "parameters": {key: obj["parameters"][key]+sign*value if delta else value}})
    drawers = re.search(r"\b(\d+)\s+drawers?", lower)
    if drawers and obj in model["assets"]:
        operations.append({"kind": "asset.set_parameters", "target_id": target, "parameters": {"drawers": int(drawers[1])}})
    if lower.strip() in ("delete", "delete this", "remove this"):
        operations.append({"kind": "entity.delete", "target_id": target, "parameters": {}})
    if not operations:
        raise DomainError("UNSUPPORTED_PROMPT", "Native edits support dimensions, colours, drawer count, and deletion. Use MCP structured operations for other changes.")
    return operations
